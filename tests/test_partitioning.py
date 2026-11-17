import copy
import tempfile
import unittest
from itertools import product
from pathlib import Path
from run import Lake
from obslake.catalog import NotFound, Unavailable
from obslake.records import LocatedRow, Observation
from obslake.row_codec import encode_file
from obslake.row_reader import read_manifest_file
from obslake.file_selection import file_may_select
from obslake.snapshots import SnapshotStore

TIME_SPEC = [{"column": "time", "transform": "floor", "width": 10},
             {"column": "time", "transform": "mod", "modulus": 4}]
FULL_SPEC = TIME_SPEC + [{"column": "device", "transform": "floor", "width": 6},
                         {"column": "device", "transform": "mod", "modulus": 3}]


def row(name, time, device=1, amount=1):
    return dict(id=name, time=time, device=device, amount=amount)


def reference_partition(spec, t, d):
    out = []
    for term in spec:
        value = t if term["column"] == "time" else d
        out.append(None if value is None else
                   (value // term["width"] if term["transform"] == "floor"
                    else value % term["modulus"]))
    return out


def reference_truth(p, t, d):
    if p is None:
        return 1
    op = p[0]
    if op == "not":
        return (1, 0, 2)[reference_truth(p[1], t, d)]
    if op in ("and", "or"):
        tables = {"and": ((0, 0, 0), (0, 1, 2), (0, 2, 2)),
                  "or": ((0, 1, 2), (1, 1, 1), (2, 1, 2))}
        return tables[op][reference_truth(p[1], t, d)][reference_truth(p[2], t, d)]
    value = t if p[1] == "time" else d
    if op == "is_null":
        return int(value is None)
    if value is None:
        return 2
    c = p[2]
    return int({"lt": value < c, "le": value <= c, "eq": value == c,
                "ne": value != c, "ge": value >= c, "gt": value > c}[op])


def reference_selected(p, t, d, start, end):
    return (t is not None and start * 86400 <= t < end * 86400
            and reference_truth(p, t, d) == 1)


def reference_candidates(spec, item, p, start, end):
    """Independent cell enumeration from literal predicate/day constants.

    Predicate truth can only change at a constant, so one representative per
    point constant and per open interval between neighbouring constants (plus
    NULL) covers every possible truth value of the file's domain.
    """
    constants = {'time': set(), 'device': set()}
    def collect(node):
        if node is None:
            return
        if node[0] in ("lt", "le", "eq", "ne", "ge", "gt"):
            constants[node[1]].add(node[2])
        elif node[0] in ("and", "or"):
            collect(node[1]); collect(node[2])
        elif node[0] == "not":
            collect(node[1])
    collect(p)
    constants['time'].update((start * 86400, end * 86400 - 1))
    domains = []
    for column in ("time", "device"):
        index = [i for i, term in enumerate(spec) if term["column"] == column]
        allows_none = column == "device"
        low = high = modulus = residue = None
        if column == "time":
            low, high = start * 86400, end * 86400 - 1
        if index:
            values = [item["partition"][i] for i in index]
            if any(value is None for value in values):
                domains.append([None])
                continue
            for i in index:
                term, value = spec[i], item["partition"][i]
                if term["transform"] == "floor":
                    cell_low, cell_high = value * term["width"], value * term["width"] + term["width"] - 1
                    low = cell_low if low is None else max(low, cell_low)
                    high = cell_high if high is None else min(high, cell_high)
                else:
                    modulus, residue = term["modulus"], value
        def allowed(value):
            if low is not None and value < low:
                return False
            if high is not None and value > high:
                return False
            if modulus is not None and value % modulus != residue:
                return False
            return True
        def snap_up(value, ceiling):
            if modulus is not None:
                value += (residue - value) % modulus
            if low is not None and value < low:
                return None
            return value if ceiling is None or value <= ceiling else None
        def snap_down(value, floor_value):
            if modulus is not None:
                value -= (value - residue) % modulus
            if high is not None and value > high:
                return None
            return value if floor_value is None or value >= floor_value else None
        candidates = [None] if allows_none else []
        points = sorted(constants[column])
        for point in points:
            if allowed(point):
                candidates.append(point)
        edges = [None] + points + [None]
        for left, right in zip(edges, edges[1:]):
            ceiling = (right - 1) if right is not None else high
            if high is not None:
                ceiling = high if ceiling is None else min(ceiling, high)
            floor_value = (left + 1) if left is not None else low
            if low is not None:
                floor_value = low if floor_value is None else max(floor_value, low)
            if ceiling is not None and floor_value is not None and ceiling < floor_value:
                continue
            if left is not None:
                value = snap_up(floor_value, ceiling)
            else:
                anchor = ceiling if ceiling is not None else 0
                value = snap_down(anchor, floor_value)
            if value is not None and (right is None or value < right) and allowed(value):
                candidates.append(value)
        domains.append(candidates)
    return any(reference_partition(spec, t, d) == list(item["partition"])
               and reference_selected(p, t, d, start, end)
               for t, d in product(*domains))


class PartitionCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "lake"
        self.lake = Lake(self.path)
        self.addCleanup(self.close)

    def close(self):
        if self.lake is not None:
            self.lake.close()
            self.lake = None

    def reopen(self):
        self.close()
        self.lake = Lake(self.path)

    def use(self, spec):
        sid = self.lake.register_spec(copy.deepcopy(spec))
        self.lake.set_write_spec(sid)
        return sid

    def query(self, name, snapshot, predicate=None, start=0, end=1):
        job = self.lake.create_query(name, snapshot, start, end, predicate)
        return self.lake.read_result(self.lake.run_query(job))


class TestSpecRegistry(PartitionCase):
    def test_validation_copy_and_identity(self):
        supplied = [{"column": "device", "transform": "mod", "modulus": 2}]
        sid = self.lake.register_spec(supplied)
        self.assertIsInstance(sid, str)
        self.assertTrue(sid)
        supplied[0]["modulus"] = 16
        self.assertEqual(self.lake.register_spec([{"column": "device", "transform": "mod", "modulus": 2}]), sid)
        self.lake.register_spec([{"column": "device", "transform": "mod", "modulus": 3}])
        self.assertNotEqual(
            self.lake.register_spec([{"column": "device", "transform": "mod", "modulus": 3}]),
            self.lake.register_spec([{"column": "device", "transform": "mod", "modulus": 2}]))
        self.lake.register_spec(tuple(TIME_SPEC))
        rejected = [
            [], {}, "spec", None, 1,
            [{"column": "time", "transform": "floor", "width": 10, "extra": 0}],
            [{"column": "time", "transform": "floor"}],
            [{"column": "time", "transform": "floor", "width": 10},
             {"column": "time", "transform": "floor", "width": 11}],
            [{"column": "time", "transform": "mod", "modulus": 4},
             {"column": "time", "transform": "mod", "modulus": 5}],
            [{"column": "amount", "transform": "mod", "modulus": 4}],
            [{"column": "time", "transform": "bucket", "width": 4}],
            [{"column": "time", "transform": "floor", "width": True}],
            [{"column": "time", "transform": "floor", "width": 0}],
            [{"column": "time", "transform": "floor", "width": -3}],
            [{"column": "time", "transform": "floor", "width": 1.0}],
            [{"column": "time", "transform": "floor", "width": "10"}],
            [{"column": "time", "transform": "mod", "modulus": 1}],
            [{"column": "time", "transform": "mod", "modulus": 17}],
            [{"column": "time", "transform": "mod", "modulus": True}],
            [{"column": "time", "transform": "mod", "modulus": 4.0}],
            [{"column": "time", "transform": "mod", "modulus": 4, "width": 2}],
            [{"column": "time", "transform": "mod"}],
        ]
        for bad in rejected:
            with self.assertRaises(ValueError):
                self.lake.register_spec(bad)
        smallest = self.lake.register_spec([{"column": "device", "transform": "mod", "modulus": 2}])
        largest = self.lake.register_spec([{"column": "device", "transform": "mod", "modulus": 16}])
        self.assertNotEqual(smallest, largest)

    def test_switch_errors_do_not_move_default(self):
        sid = self.use(TIME_SPEC)
        self.lake.write("fixed", [row("a", 3)])
        before = self.lake.snapshot()
        for bad in (None, 1, True, "", "unknown"):
            with self.assertRaises((ValueError, NotFound)):
                self.lake.set_write_spec(bad)
        with self.assertRaises(NotFound):
            self.lake.set_write_spec("p-missing")
        marker = RuntimeError("rollback")
        def fail(stage):
            if stage == "catalog.before_commit":
                raise marker
        self.lake.catalog.hook = fail
        with self.assertRaises(RuntimeError):
            self.lake.register_spec([{"column": "device", "transform": "floor", "width": 2}])
        with self.assertRaises(RuntimeError):
            self.lake.set_write_spec(sid)
        self.lake.catalog.hook = None
        self.assertEqual([f["partition"] for f in self.lake.snapshot()["files"]], [[0, 3]])
        self.lake.write("after", [row("b", 3)])
        after = self.lake.snapshot()
        self.assertEqual([f["partition"] for f in after["files"]], [[0, 3], [0, 3]])
        self.assertEqual(after["specs"], {sid: TIME_SPEC})
        self.assertTrue(self.lake.write("replay", [row("b", 3)]))
        with self.assertRaises(ValueError):
            self.lake.register_spec([{"column": "time", "transform": "floor", "width": 86400, "bad": 0}])

    def test_ids_survive_reopen_and_repetition(self):
        sid = self.use(FULL_SPEC)
        self.lake.write("A", [row("a", 3, 1), row("b", 7, 4)])
        frozen = self.lake.snapshot()
        self.reopen()
        self.assertEqual(self.lake.register_spec(copy.deepcopy(FULL_SPEC)), sid)
        self.lake.write("B", [row("c", 4, 2)])
        latest = self.lake.snapshot()
        self.assertEqual(latest["files"][-1]["spec_id"], sid)
        self.assertEqual(self.lake.snapshot(frozen["id"]), frozen)
        self.assertEqual(set(latest["specs"]), {sid})


class TestSpecWriting(PartitionCase):
    def test_partition_layout_and_ordinals(self):
        sid = self.use(TIME_SPEC)
        rows = [row("n1", -1), row("z1", 3), row("n2", None), row("z2", 7), row("n3", -11)]
        receipt = self.lake.write("A", rows)
        snapshot = self.lake.snapshot()
        spec_files = [f for f in snapshot["files"] if f["batch_id"] == receipt["batch_id"]]
        self.assertEqual([f["partition"] for f in spec_files],
                         [[-1, 3], [0, 3], [None, None], [-2, 1]])
        self.assertEqual([f["ordinals"] for f in spec_files], [[0], [1, 3], [2], [4]])
        for item in spec_files:
            self.assertEqual(item["spec_id"], sid)
            self.assertNotIn("day", item)
            self.assertEqual(set(item), {"batch_id", "spec_id", "partition", "digest",
                                         "byte_count", "row_count", "ordinals"})
        self.assertEqual(snapshot["specs"], {sid: TIME_SPEC})

    def test_floor_rounds_toward_negative_infinity(self):
        sid = self.use([{"column": "time", "transform": "floor", "width": 7}])
        self.lake.write("A", [row("a", -1), row("b", -7), row("c", -8), row("d", 0), row("e", 6)])
        self.assertEqual([f["partition"] for f in self.lake.snapshot()["files"]],
                         [[-1], [-2], [0]])

    def test_replay_after_switch_keeps_original_files(self):
        first = self.lake.write("A", [row("a", 0), row("b", -1)])
        original = self.lake.snapshot()
        self.use(FULL_SPEC)
        self.assertEqual(self.lake.write("A", [row("a", 0), row("b", -1)]), first)
        self.assertEqual(self.lake.snapshot(), original)
        with self.assertRaises(ValueError):
            self.lake.write("A", [row("a", 0)])
        self.assertEqual(self.lake.snapshot(), original)
        self.use(TIME_SPEC)
        self.lake.write("B", [row("c", 3)])
        latest = self.lake.snapshot()
        self.assertEqual(latest["files"][0].get("day"), 0)
        self.assertNotIn("spec_id", latest["files"][0])
        self.assertEqual(latest["files"][-1]["partition"], [0, 3])

    def test_empty_batch_under_spec(self):
        self.use(TIME_SPEC)
        before = self.lake.snapshot()
        receipt = self.lake.write("empty", [])
        self.assertEqual(receipt["row_count"], 0)
        self.assertEqual(self.lake.snapshot()["files"], before["files"])
        self.assertEqual(len(self.lake.snapshot()["batches"]), 1)

    def test_snapshot_copies_are_independent(self):
        self.use(TIME_SPEC)
        receipt = self.lake.write("A", [row("a", 3, 1)])
        snapshot = self.lake.snapshot()
        snapshot["specs"][receipt["batch_id"]] = []
        snapshot["specs"].clear()
        snapshot["files"][0]["partition"].append(99)
        fresh = self.lake.snapshot()
        self.assertEqual(fresh["specs"], {self.lake.register_spec(TIME_SPEC): TIME_SPEC})
        self.assertEqual(fresh["files"][0]["partition"], [0, 3])

    def test_route_verification_rejects_wrong_partition_object(self):
        self.use(TIME_SPEC)
        receipt = self.lake.write("A", [row("a", 3, 1)])
        snapshot = self.lake.snapshot()
        item, batch = snapshot["files"][0], snapshot["batches"][0]
        forged = encode_file((LocatedRow(batch["batch_id"], 0, Observation("a", 4, 1, 1)),))
        digest = self.lake.objects.seal(forged)
        changed = dict(item, digest=digest, byte_count=len(forged))
        with self.assertRaises(Unavailable):
            read_manifest_file(changed, batch, self.lake.objects, snapshot)
        data = encode_file((LocatedRow(batch["batch_id"], 0, Observation("a", 3, 1, 1)),))
        self.assertEqual(len(read_manifest_file(dict(item, digest=self.lake.objects.seal(data),
                                                   byte_count=len(data)), batch,
                                                self.lake.objects, snapshot)), 1)


class TestExactSelection(PartitionCase):
    def test_read_set_matches_independent_enumeration(self):
        specs = [
            [{"column": "time", "transform": "floor", "width": 10},
             {"column": "time", "transform": "mod", "modulus": 4}],
            [{"column": "device", "transform": "mod", "modulus": 5}],
            [{"column": "time", "transform": "floor", "width": 9}],
            [{"column": "device", "transform": "floor", "width": 6},
             {"column": "time", "transform": "mod", "modulus": 7}],
        ]
        predicates = [
            None,
            ["eq", "time", 7],
            ["ne", "device", 1],
            ["not", ["lt", "time", 8]],
            ["and", ["ge", "time", 8], ["lt", "time", 10]],
            ["and", ["ge", "time", 7], ["lt", "time", 10]],
            ["or", ["eq", "time", 3], ["is_null", "device"]],
            ["not", ["or", ["lt", "time", 8], ["is_null", "device"]]],
            ["and", ["or", ["lt", "time", 0], ["gt", "device", 3]],
                    ["or", ["eq", "time", -1], ["is_null", "device"]]],
            ["not", ["and", ["ge", "time", 0], ["ne", "device", -1]]],
        ]
        rows = [row(str(i), t, d, None if i % 3 == 0 else i * 7)
                for i, (t, d) in enumerate(product(
                    [-11, -10, -1, 0, 3, 4, 7, 8, 10, 19, None],
                    [None, -6, -1, 1, 2, 4, 6, 7]))]
        for index, spec in enumerate(specs):
            self.close()
            self.path = Path(self.temp.name) / ("lake-%d" % index)
            self.lake = Lake(self.path)
            self.use(spec)
            self.lake.write("grid", rows)
            snapshot = self.lake.snapshot()
            for p in predicates:
                for start, end in ((-1, 1), (0, 1), (0, 0), (2, 3)):
                    expected = {f["digest"] for f in snapshot["files"]
                                if reference_candidates(spec, f, p, start, end)}
                    self.lake.objects.read_trace.clear()
                    result = self.query("q-%s-%s-%s-%s" % (index, predicates.index(p), start, end),
                                        snapshot["id"], p, start, end)
                    self.assertEqual(set(self.lake.objects.read_trace), expected,
                                     (spec, p, start, end))
                    wanted = [r for r in rows if reference_selected(p, r["time"], r["device"], start, end)]
                    self.assertEqual([r["id"] for r in result["payload"]["rows"]],
                                     [r["id"] for r in wanted])

    def test_partition_domain_not_actual_rows(self):
        self.use(TIME_SPEC)
        receipt = self.lake.write("A", [row("only-three", 3)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        item = snapshot["files"][0]
        self.lake.objects.set_available(item["digest"], False)
        excluded = self.query("excluded", snapshot["id"],
                              ["and", ["ge", "time", 8], ["lt", "time", 10]])
        self.assertEqual(excluded["payload"]["rows"], [])
        self.assertEqual(self.lake.objects.read_trace, [])
        for index, predicate in enumerate(
                (["not", ["and", ["ge", "time", 0], ["lt", "time", 86400]]],
                 ["and", ["ge", "time", 8], ["lt", "time", 10]])):
            self.lake.objects.read_trace.clear()
            result = self.query("again-%d" % index, snapshot["id"], predicate)
            self.assertEqual(result["payload"]["rows"], [])
            self.assertEqual(self.lake.objects.read_trace, [])
        job = self.lake.create_query("possible", snapshot["id"], 0, 1,
                                     ["and", ["ge", "time", 7], ["lt", "time", 10]])
        with self.assertRaises(Unavailable):
            self.lake.run_query(job)
        self.lake.objects.set_available(item["digest"], True)
        result = self.lake.read_result(self.lake.run_query(job))
        self.assertEqual(result["payload"]["rows"], [])

    def test_joint_null_logic_and_untouched_cold_files(self):
        self.use(FULL_SPEC)
        receipt = self.lake.write("A", [row("x", 3, 1), row("y", 8, None), row("n", None, 1)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        by_partition = {tuple(f["partition"]): f for f in snapshot["files"]}
        for digest in (by_partition[(0, 0, None, None)]["digest"],
                       by_partition[(None, None, 0, 1)]["digest"]):
            self.lake.objects.set_available(digest, False)
        from obslake.predicates import freeze_predicate
        definition = dict(day_start=0, day_end=1,
                          predicate=freeze_predicate(["not", ["or", ["lt", "time", 8],
                                                             ["is_null", "device"]]]))
        expected = set()
        self.assertEqual(expected, {f["digest"] for f in snapshot["files"]
                                    if file_may_select(snapshot, f, definition)})
        self.lake.objects.read_trace.clear()
        result = self.query("not-or", snapshot["id"], ["not", ["or", ["lt", "time", 8],
                                                                ["is_null", "device"]]])
        self.assertEqual([r["id"] for r in result["payload"]["rows"]], [])
        self.assertEqual(set(self.lake.objects.read_trace), expected)
        self.lake.objects.set_available(by_partition[(0, 3, 0, 1)]["digest"], True)
        wider = self.query("not-or-wider", snapshot["id"],
                           ["not", ["is_null", "device"]], 0, 1)
        self.assertEqual({r["id"] for r in wider["payload"]["rows"]}, {"x"})


class TestDocumentedBoundaries(PartitionCase):
    def test_manual_spec_examples_and_no_conservative_reads(self):
        self.use(TIME_SPEC)
        receipt = self.lake.write("A", [row("three", 3, 1, 4)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        self.assertEqual(snapshot["files"][0]["partition"], [0, 3])
        empty = self.query("wide", snapshot["id"],
                           ["and", ["ge", "time", 8], ["lt", "time", 10]])
        self.assertEqual(empty["payload"]["rows"], [])
        self.assertEqual(self.lake.objects.read_trace, [])
        # NOT(time<8 OR device IS NULL): within the domain every time is < 8
        # (3 or 7), and null device would also make the OR true.
        self.lake.objects.read_trace.clear()
        negated = self.query("negated", snapshot["id"],
                             ["not", ["or", ["lt", "time", 8], ["is_null", "device"]]])
        self.assertEqual(negated["payload"]["rows"], [])
        self.assertEqual(self.lake.objects.read_trace, [])
        # The same partition still holds device-null possibilities, so an OR
        # with device IS NULL must read the file and select the actual row.
        both = self.query("either", snapshot["id"],
                          ["or", ["eq", "time", 3], ["is_null", "device"]])
        self.assertEqual([r["id"] for r in both["payload"]["rows"]], ["three"])
        self.assertEqual(set(self.lake.objects.read_trace), {snapshot["files"][0]["digest"]})
        # device IS NULL alone is satisfiable inside the partition domain even
        # though the only stored row has device 1: read, filter, empty.
        self.lake.objects.read_trace.clear()
        null_device = self.query("null-device", snapshot["id"], ["is_null", "device"])
        self.assertEqual(null_device["payload"]["rows"], [])
        self.assertEqual(set(self.lake.objects.read_trace), {snapshot["files"][0]["digest"]})

    def test_negative_floor_and_mod_boundaries(self):
        self.use(TIME_SPEC)
        self.lake.write("A", [row("m1", -1), row("m3", 3), row("m7", 7)])
        snapshot = self.lake.snapshot()
        partitions = sorted((f["partition"] for f in snapshot["files"]), key=repr)
        self.assertEqual(partitions, [[-1, 3], [0, 3]])
        self.assertEqual(self.query("q", snapshot["id"], ["eq", "time", -1],
                                    -1, 0)["payload"]["rows"][0]["id"], "m1")
        self.assertEqual(self.lake.objects.read_trace, [
            next(f["digest"] for f in snapshot["files"] if f["partition"] == [-1, 3])])
        self.assertEqual(self.query("q0", snapshot["id"], None, 0, 1)["payload"]["rows"],
                         [{"batch_id": snapshot["batches"][0]["batch_id"], "ordinal": 1,
                           "id": "m3", "time": 3, "device": 1, "amount": 1},
                          {"batch_id": snapshot["batches"][0]["batch_id"], "ordinal": 2,
                           "id": "m7", "time": 7, "device": 1, "amount": 1}])

    def test_wide_time_partition_spans_days(self):
        wide = [{"column": "time", "transform": "floor", "width": 86400 * 10}]
        self.use(wide)
        receipt = self.lake.write("A", [row("d0", 10), row("d5", 5 * 86400 + 7),
                                        row("d-1", -1)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        self.assertLessEqual(len(snapshot["files"]), 3)
        result = self.query("day0", snapshot["id"], None, 0, 1)
        self.assertEqual([r["id"] for r in result["payload"]["rows"]], ["d0"])
        audit = self.lake.read_result(self.lake.reconcile("C", result["result_id"]))["payload"]
        self.assertTrue(audit["consistent"])
        self.assertEqual(audit["accounting"]["source_count"], 3)
        self.assertEqual(audit["accounting"]["reasons"]["outside_day"], 2)


class TestRepresentationInvariants(PartitionCase):
    def test_unregistered_spec_metadata_fails_instead_of_reading(self):
        self.use(TIME_SPEC)
        receipt = self.lake.write("A", [row("a", 3)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        orphan = dict(snapshot, specs={})
        job = self.lake.create_query("orphan", snapshot["id"], 0, 1)
        self.lake.queries.snapshots.snapshot = lambda sid=None, frozen=orphan: copy.deepcopy(frozen)
        self.addCleanup(setattr, self.lake.queries.snapshots, "snapshot",
                        SnapshotStore(self.lake.catalog).snapshot)
        with self.assertRaises(Unavailable):
            self.lake.run_query(job)
        self.assertIsNone(self.lake.catalog.db.execute(
            "SELECT 1 FROM results WHERE id LIKE 'r-%'").fetchone())

    def test_manifest_partition_mismatch_with_spec_swap(self):
        self.use(TIME_SPEC)
        receipt = self.lake.write("A", [row("a", 3)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        item, batch = snapshot["files"][0], snapshot["batches"][0]
        other = self.lake.register_spec([{"column": "time", "transform": "floor", "width": 10}])
        swapped = dict(item, spec_id=other)
        forged = dict(snapshot, files=[swapped],
                      specs={other: [{"column": "time", "transform": "floor", "width": 10}]})
        with self.assertRaises(Unavailable):
            read_manifest_file(swapped, batch, self.lake.objects, forged)

    def test_empty_day_range_reads_nothing_even_offline(self):
        self.use(FULL_SPEC)
        receipt = self.lake.write("A", [row("a", 3, 1), row("b", 8, 4)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        for item in snapshot["files"]:
            self.lake.objects.set_available(item["digest"], False)
        self.lake.objects.read_trace.clear()
        result = self.query("empty", snapshot["id"], None, 2, 2)
        self.assertEqual(result["payload"]["rows"], [])
        self.assertEqual(result["payload"]["groups"], [])
        self.assertEqual(self.lake.objects.read_trace, [])

    def test_spec_bound_to_batch_survives_default_switch(self):
        sid = self.use(TIME_SPEC)
        receipt = self.lake.write("A", [row("a", 3)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        self.use(FULL_SPEC)
        self.lake.write("B", [row("b", 3)])
        self.reopen()
        self.assertEqual(self.lake.snapshot(snapshot["id"]), snapshot)
        result = self.query("fixed", snapshot["id"])
        self.assertEqual([r["id"] for r in result["payload"]["rows"]], ["a"])
        self.assertEqual(set(self.lake.objects.read_trace), {snapshot["files"][0]["digest"]})
        self.assertEqual(self.lake.snapshot()["specs"][sid], TIME_SPEC)


class TestMixedAccountingAndFailure(PartitionCase):
    def test_mixed_order_groups_and_reconciliation(self):
        self.lake.write("legacy", [row("L1", 0, 7, 10 ** 30), row("L2", -1, 7, None)])
        self.use(FULL_SPEC)
        rows = [row("s1", 3, 1, 5), row("s2", 7, 4, -5), row("s3", 8, 1, None),
                row("s4", -1, 2, 2), row("s5", None, 1, 9), row("s6", 4, 4, 0)]
        self.lake.write("spec", rows)
        receipt = self.lake.write("tail", [row("t1", 3, 1, None), row("t2", 86399, 4, 10 ** 25)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        result = self.query("Q", snapshot["id"],
                            ["or", ["eq", "device", 1], ["eq", "device", 4]], -1, 1)
        expected = [r for r in [row("L1", 0, 7, 10 ** 30), row("L2", -1, 7, None)] + rows
                    + [row("t1", 3, 1, None), row("t2", 86399, 4, 10 ** 25)]
                    if reference_selected(["or", ["eq", "device", 1], ["eq", "device", 4]],
                                          r["time"], r["device"], -1, 1)]
        self.assertEqual([r["id"] for r in result["payload"]["rows"]], [r["id"] for r in expected])
        self.assertEqual(result["payload"]["groups"], [
            dict(device=1, row_count=3, amount_count=1, sum_amount=5),
            dict(device=4, row_count=3, amount_count=3, sum_amount=10 ** 25 - 5)])
        audit_id = self.lake.reconcile("audit", result["result_id"])
        body = self.lake.read_result(audit_id)["payload"]
        self.assertTrue(body["consistent"])
        self.assertEqual(body["discrepancies"], [])
        self.assertEqual(body["accounting"]["source_count"], 10)
        self.assertEqual(sum(body["accounting"]["reasons"].values()), 10)
        self.assertEqual(body["accounting"]["reasons"]["null_time"], 1)
        self.assertEqual(body["accounting"]["reasons"]["outside_day"], 0)
        self.assertEqual(body["accounting"]["reasons"]["predicate_false"], 3)
        self.assertEqual(body["accounting"]["selected_amount_null"], 2)
        self.lake.objects.read_trace.clear()
        self.assertEqual(self.lake.reconcile("audit", result["result_id"]), audit_id)
        self.assertEqual(self.lake.objects.read_trace, [])

    def test_cold_reads_for_query_but_full_sources_for_audit(self):
        self.use(TIME_SPEC)
        receipt = self.lake.write("A", [row("cold", 3, 1, 5), row("warm", 8, 1, 7)])
        snapshot = self.lake.snapshot(receipt["snapshot_id"])
        cold = next(f for f in snapshot["files"] if f["partition"] == [0, 3])
        self.lake.objects.set_available(cold["digest"], False)
        result = self.query("excluded", snapshot["id"],
                            ["and", ["ge", "time", 8], ["lt", "time", 10]])
        self.assertEqual([r["id"] for r in result["payload"]["rows"]], ["warm"])
        self.assertEqual(self.lake.objects.read_trace, [
            next(f for f in snapshot["files"] if f["partition"] == [0, 0])["digest"]])
        with self.assertRaises(Unavailable):
            self.lake.reconcile("audit", result["result_id"])
        self.assertIsNone(self.lake.catalog.db.execute(
            "SELECT 1 FROM receipts WHERE namespace='reconcile' AND request='audit'").fetchone())
        self.lake.objects.set_available(cold["digest"], True)
        audit = self.lake.read_result(self.lake.reconcile("audit", result["result_id"]))["payload"]
        self.assertTrue(audit["consistent"])
        self.assertEqual(audit["accounting"]["source_count"], 2)
        self.assertEqual(audit["accounting"]["reasons"]["predicate_false"], 1)

    def test_publication_and_result_rollback_with_specs(self):
        self.use(FULL_SPEC)
        self.lake.write("base", [row("b", 0, 1)])
        before = self.lake.snapshot()
        values = [row("a", 3, 1), row("b", 7, 4)]
        for stage in ("publication.batch", "publication.snapshot", "publication.head",
                      "receipt.acceptance", "publication.receipt", "catalog.before_commit"):
            def fail(current, chosen=stage):
                if current == chosen:
                    raise RuntimeError(chosen)
            self.lake.catalog.hook = fail
            with self.assertRaises(RuntimeError):
                self.lake.write("A", values)
            self.lake.catalog.hook = None
            self.assertEqual(self.lake.snapshot(), before)
            self.reopen()
            self.assertEqual(self.lake.snapshot(), before)
        receipt = self.lake.write("A", values)
        job = self.lake.create_query("Q", receipt["snapshot_id"], 0, 1)
        for stage in ("result.insert", "query.complete"):
            def fail(current, chosen=stage):
                if current == chosen:
                    raise RuntimeError(chosen)
            self.lake.catalog.hook = fail
            with self.assertRaises(RuntimeError):
                self.lake.run_query(job)
            self.lake.catalog.hook = None
            self.assertEqual(self.lake.queries.read_job(job)["status"], "pending")
        self.assertEqual(len(self.lake.read_result(self.lake.run_query(job))["payload"]["rows"]), 3)

    def test_v1_catalog_upgrades_without_rewriting(self):
        self.lake.write("legacy", [row("a", 0), row("b", None)])
        legacy = self.lake.snapshot()
        self.close()
        import json
        import sqlite3
        with sqlite3.connect(self.path / "catalog.sqlite") as db:
            db.row_factory = sqlite3.Row
            for stored in db.execute("SELECT id, body FROM snapshots").fetchall():
                body = json.loads(stored["body"])
                body.pop("specs", None)
                db.execute("UPDATE snapshots SET body=? WHERE id=?",
                           (json.dumps(body, sort_keys=True, separators=(",", ":")), stored["id"]))
            db.execute("DROP TABLE write_state")
            db.execute("DROP TABLE partition_specs")
            db.execute("PRAGMA user_version=1")
        self.lake = Lake(self.path)
        stored = self.lake.snapshot()
        self.assertNotIn("specs", stored)
        self.assertEqual(stored["id"], legacy["id"])
        self.assertEqual(stored["files"], legacy["files"])
        self.assertEqual(stored["batches"], legacy["batches"])
        sid = self.use(TIME_SPEC)
        self.lake.write("new", [row("c", 3)])
        snapshot = self.lake.snapshot()
        self.assertEqual(snapshot["files"][0], legacy["files"][0])
        self.assertNotIn("spec_id", snapshot["files"][0])
        self.assertEqual(snapshot["files"][-1]["spec_id"], sid)
        result = self.query("Q", snapshot["id"])
        self.assertEqual([r["id"] for r in result["payload"]["rows"]], ["a", "c"])
        audit = self.lake.read_result(self.lake.reconcile("C", result["result_id"]))["payload"]
        self.assertTrue(audit["consistent"])
        self.assertEqual(audit["accounting"]["source_count"], 3)


if __name__ == "__main__":
    unittest.main()
