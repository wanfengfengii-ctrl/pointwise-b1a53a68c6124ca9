"""Public finite-domain oracles and integration examples; no V2 implementation."""
import copy
import json
import sys
import tempfile
import unittest
from itertools import product
from pathlib import Path
from run import Lake, NotFound, Unavailable

DEMO = False
TIME_SPEC = [
    {"column": "time", "transform": "floor", "width": 10},
    {"column": "time", "transform": "mod", "modulus": 4},
]
FULL_SPEC = TIME_SPEC + [
    {"column": "device", "transform": "floor", "width": 6},
    {"column": "device", "transform": "mod", "modulus": 3},
]


def row(name, time, device=1, amount=1):
    return dict(id=name, time=time, device=device, amount=amount)


def truth(p, t, d):
    # 0 FALSE, 1 TRUE, 2 UNKNOWN. Literal SQL tables, independent of obslake.
    if p is None:
        return 1
    op = p[0]
    if op == "not":
        return (1, 0, 2)[truth(p[1], t, d)]
    if op in ("and", "or"):
        tables = {"and": ((0, 0, 0), (0, 1, 2), (0, 2, 2)),
                  "or": ((0, 1, 2), (1, 1, 1), (2, 1, 2))}
        return tables[op][truth(p[1], t, d)][truth(p[2], t, d)]
    value = t if p[1] == "time" else d
    if op == "is_null":
        return int(value is None)
    if value is None:
        return 2
    c = p[2]
    return int({"lt": value < c, "le": value <= c, "eq": value == c,
                "ne": value != c, "ge": value >= c, "gt": value > c}[op])


def selected(p, t, d, start, end):
    return t is not None and start * 86400 <= t < end * 86400 and truth(p, t, d) == 1


def partition(spec, t, d):
    result = []
    for item in spec:
        v = t if item["column"] == "time" else d
        result.append(None if v is None else
                      (v // item["width"] if item["transform"] == "floor" else v % item["modulus"]))
    return result


def expected_groups(rows):
    devices = sorted({r["device"] for r in rows}, key=lambda d: (d is not None, d or 0))
    answer = []
    for d in devices:
        members = [r for r in rows if r["device"] == d]
        amounts = [r["amount"] for r in members if r["amount"] is not None]
        answer.append(dict(device=d, row_count=len(members), amount_count=len(amounts),
                           sum_amount=sum(amounts) if amounts else None))
    return answer


def finite_file_possible(item, spec, p, start, end):
    # Only for FULL_SPEC fixtures: both columns have small floor-bounded domains.
    domains = []
    for column in ("time", "device"):
        i = next(i for i, term in enumerate(spec)
                 if term["column"] == column and term["transform"] == "floor")
        q, width = item["partition"][i], spec[i]["width"]
        domains.append([None] if q is None else range(q * width, (q + 1) * width))
    return any(partition(spec, t, d) == item["partition"] and selected(p, t, d, start, end)
               for t, d in product(*domains))


def oracle_selfcheck():
    assert partition(TIME_SPEC, -1, None) == [-1, 3]
    assert partition(TIME_SPEC, None, None) == [None, None]
    assert truth(["not", ["eq", "device", 1]], 3, None) == 2
    assert truth(["or", ["eq", "time", 3], ["eq", "device", 1]], 3, None) == 1
    item = {"partition": [0, 3, 0, 1]}
    assert not finite_file_possible(item, FULL_SPEC, ["ge", "time", 8], 0, 1)
    assert finite_file_possible(item, FULL_SPEC, ["ge", "time", 7], 0, 1)
    assert not finite_file_possible(item, FULL_SPEC, None, 0, 0)
    assert expected_groups([row("x", 3, 1, None), row("y", 7, 1, 0)]) == [
        dict(device=1, row_count=2, amount_count=1, sum_amount=0)]
    print("REFERENCE_ORACLE_OK")


class MixedPartitions(unittest.TestCase):
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

    def query(self, name, snap, p=None, start=0, end=1):
        job = self.lake.create_query(name, snap, start, end, p)
        return self.lake.read_result(self.lake.run_query(job))

    def test_registry_replay_and_rollback(self):
        supplied = copy.deepcopy(TIME_SPEC)
        sid = self.lake.register_spec(supplied)
        supplied[0]["width"] = 999
        self.assertEqual(self.lake.register_spec(TIME_SPEC), sid)
        self.assertNotEqual(self.lake.register_spec(list(reversed(TIME_SPEC))), sid)
        self.lake.set_write_spec(sid)
        rows = [row("x", -1), row("y", 7), row("n", None)]
        receipt = self.lake.write("A", iter(rows))
        frozen = self.lake.snapshot()
        self.assertEqual([f["partition"] for f in frozen["files"]], [[-1, 3], [0, 3], [None, None]])
        self.assertEqual(frozen["specs"][sid], TIME_SPEC)
        for bad in ([], TIME_SPEC + [TIME_SPEC[0]], [{"column": "time", "transform": "floor", "width": True}],
                    [{"column": "device", "transform": "mod", "modulus": 17}]):
            with self.assertRaises(ValueError):
                self.lake.register_spec(bad)
        with self.assertRaises(NotFound):
            self.lake.set_write_spec("not-a-registered-spec")
        other = self.lake.register_spec([{"column": "device", "transform": "mod", "modulus": 5}])
        marker = RuntimeError("commit denied")
        def fail(stage):
            if stage == "catalog.before_commit":
                raise marker
        self.lake.catalog.hook = fail
        with self.assertRaises(RuntimeError):
            self.lake.set_write_spec(other)
        with self.assertRaises(RuntimeError):
            self.lake.register_spec([{"column": "time", "transform": "floor", "width": 17}])
        self.lake.catalog.hook = None
        self.reopen()
        self.lake.write("still-old-default", [row("z", 3)])
        latest = self.lake.snapshot()
        self.assertEqual(latest["files"][-1]["spec_id"], sid)
        self.lake.set_write_spec(other)
        before = self.lake.snapshot()
        self.assertEqual(self.lake.write("A", iter(rows)), receipt)
        self.assertEqual(self.lake.snapshot(), before)
        self.assertEqual(self.lake.snapshot(frozen["id"]), frozen)
        self.lake.write("new-default", [row("w", 7)])
        self.assertEqual(self.lake.snapshot()["files"][-1]["spec_id"], other)

    def test_cold_file_and_reopen(self):
        self.use(TIME_SPEC)
        receipt = self.lake.write("A", [row("only-three", 3)])
        snap = receipt["snapshot_id"]
        item = self.lake.snapshot(snap)["files"][0]
        self.lake.objects.set_available(item["digest"], False)
        impossible = ["and", ["ge", "time", 8], ["lt", "time", 10]]
        possible = ["and", ["ge", "time", 7], ["lt", "time", 10]]
        self.lake.objects.read_trace.clear()
        excluded = self.query("excluded", snap, impossible)
        self.assertEqual(excluded["payload"]["rows"], [])
        self.assertEqual(self.lake.objects.read_trace, [])
        job = self.lake.create_query("possible", snap, 0, 1, possible)
        before = self.lake.catalog.db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        self.reopen()
        with self.assertRaises(Unavailable):
            self.lake.run_query(job)
        self.assertEqual(self.lake.objects.read_trace, [item["digest"]])
        self.assertEqual(self.lake.catalog.db.execute("SELECT COUNT(*) FROM results").fetchone()[0], before)
        self.lake.objects.set_available(item["digest"], True)
        result = self.lake.read_result(self.lake.run_query(job))
        self.assertEqual(result["payload"]["rows"], [])
        self.assertEqual(self.lake.run_query(job), result["result_id"])
        if DEMO:
            print(json.dumps(dict(case="cold_partition", partition=item["partition"],
                excluded_rows=excluded["payload"]["rows"], excluded_reads=[],
                possible_offline="Unavailable", recovered_rows=result["payload"]["rows"])))

    def test_mixed_snapshot_fixed_query_and_accounting(self):
        old = [row("legacy", 0, 1, 9), row("old-negative", -1, 2, None)]
        self.lake.write("legacy", old)
        sid = self.use(FULL_SPEC)
        new = [row("x", 3, 1, 5), row("y", 7, 4, -5), row("z", 8, None, None),
               row("null-time", None, 1, 11), row("same-day", 4, 2, 0)]
        receipt = self.lake.write("new", new)
        snapshot = self.lake.snapshot()
        new_files = [f for f in snapshot["files"] if f["batch_id"] == receipt["batch_id"]]
        grouped = {}
        for ordinal, r in enumerate(new):
            key = tuple(partition(FULL_SPEC, r["time"], r["device"]))
            grouped.setdefault(key, []).append(ordinal)
        self.assertEqual([f["partition"] for f in new_files], [list(k) for k in grouped])
        self.assertEqual([f["ordinals"] for f in new_files], list(grouped.values()))
        self.assertTrue(all(f["spec_id"] == sid for f in new_files))
        predicate = ["or", ["eq", "device", 1], ["not", ["is_null", "device"]]]
        saved_predicate = copy.deepcopy(predicate)
        job = self.lake.create_query("fixed", snapshot["id"], 0, 1, predicate)
        predicate[1][2] = 999
        self.use([{"column": "time", "transform": "floor", "width": 3600}])
        self.lake.write("later", [row("must-not-appear", 7, 1, 1000)])
        self.reopen()
        self.assertEqual(self.lake.snapshot(snapshot["id"]), snapshot)
        result = self.lake.read_result(self.lake.run_query(job))
        expected = [r for r in old + new if selected(saved_predicate, r["time"], r["device"], 0, 1)]
        actual = result["payload"]["rows"]
        self.assertEqual([{k: r[k] for k in ("id", "time", "device", "amount")} for r in actual], expected)
        self.assertEqual(result["payload"]["groups"], expected_groups(expected))
        audit_id = self.lake.reconcile("audit", result["result_id"])
        audit = self.lake.read_result(audit_id)
        self.assertTrue(audit["payload"]["consistent"])
        self.assertEqual(audit["payload"]["accounting"]["source_count"], len(old + new))
        self.assertEqual(audit["payload"]["discrepancies"], [])
        self.reopen()
        self.assertEqual(self.lake.read_result(audit_id), audit)
        if DEMO:
            print(json.dumps(dict(case="mixed_fixed", expected=expected, actual=actual,
                groups=result["payload"]["groups"], accounting=audit["payload"]["accounting"]), sort_keys=True))

    def test_finite_domain_read_set(self):
        self.use(FULL_SPEC)
        rows = [row(str(i), t, d, None if i % 3 == 0 else i)
                for i, (t, d) in enumerate(product([-1, 3, 4, 7, 8, 10, 19, None], [None, -1, 1, 2, 4, 6]))]
        self.lake.write("grid", rows)
        snap = self.lake.snapshot()
        predicates = [None, ["eq", "time", 7], ["ne", "device", 1],
            ["not", ["or", ["lt", "time", 8], ["is_null", "device"]]],
            ["or", ["lt", "time", 0], ["is_null", "device"]],
            ["and", ["or", ["eq", "time", 3], ["eq", "device", 2]],
                    ["or", ["eq", "time", 7], ["eq", "device", 5]]]]
        for i, p in enumerate(predicates):
            for start, end in ((-1, 1), (0, 1), (0, 0)):
                expected_files = {f["digest"] for f in snap["files"]
                                  if finite_file_possible(f, FULL_SPEC, p, start, end)}
                self.lake.objects.read_trace.clear()
                result = self.query("grid-%s-%s-%s" % (i, start, end), snap["id"], p, start, end)
                self.assertEqual(set(self.lake.objects.read_trace), expected_files)
                expected = [r for r in rows if selected(p, r["time"], r["device"], start, end)]
                self.assertEqual([r["id"] for r in result["payload"]["rows"]], [r["id"] for r in expected])
                self.assertEqual(result["payload"]["groups"], expected_groups(expected))

    def test_unbounded_device_and_huge_integers(self):
        self.use([{"column": "device", "transform": "mod", "modulus": 16}])
        huge = 10 ** 5000
        receipt = self.lake.write("huge", [row("far", huge, 3, huge + 1), row("null-device", huge, None, None)])
        snap = self.lake.snapshot()
        required = next(f for f in snap["files"] if f["partition"] == [3])
        nullable = next(f for f in snap["files"] if f["partition"] == [None])
        self.lake.objects.set_available(nullable["digest"], False)
        day = huge // 86400
        self.lake.objects.read_trace.clear()
        result = self.query("huge-present", snap["id"], ["eq", "device", 3], day, day + 1)
        self.assertEqual(result["payload"]["groups"], expected_groups([row("far", huge, 3, huge + 1)]))
        self.assertEqual(self.lake.objects.read_trace, [required["digest"]])
        self.lake.objects.set_available(required["digest"], False)
        job = self.lake.create_query("unbounded", receipt["snapshot_id"], day, day + 1, ["gt", "device", huge])
        with self.assertRaises(Unavailable):
            self.lake.run_query(job)
        self.lake.objects.set_available(required["digest"], True)
        self.assertEqual(self.lake.read_result(self.lake.run_query(job))["payload"]["rows"], [])

    def test_new_route_failure_boundaries(self):
        self.use(FULL_SPEC)
        before = self.lake.snapshot()
        values = [row("a", 3), row("b", 7, 4)]
        def broken():
            yield values[0]
            raise RuntimeError("source disconnected")
        with self.assertRaises(RuntimeError):
            self.lake.write("broken", broken())
        self.assertEqual(self.lake.snapshot(), before)
        for stage in ("publication.head", "catalog.before_commit"):
            def fail(current, chosen=stage):
                if current == chosen:
                    raise RuntimeError(chosen)
            self.lake.catalog.hook = fail
            with self.assertRaises(RuntimeError):
                self.lake.write("A", values)
            self.lake.catalog.hook = None
            self.assertEqual(self.lake.snapshot(), before)
        receipt = self.lake.write("A", values)
        job = self.lake.create_query("Q", receipt["snapshot_id"], 0, 1)
        def fail_result(stage):
            if stage == "result.insert":
                raise RuntimeError("result rejected")
        self.lake.catalog.hook = fail_result
        with self.assertRaises(RuntimeError):
            self.lake.run_query(job)
        self.lake.catalog.hook = None
        self.assertEqual(self.lake.queries.read_job(job)["status"], "pending")
        self.assertEqual(len(self.lake.read_result(self.lake.run_query(job))["payload"]["rows"]), 2)


def main():
    global DEMO
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode == "oracle":
        oracle_selfcheck()
        return 0
    if mode == "verify":
        suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(MixedPartitions))
    elif mode == "demo":
        DEMO = True
        suite = unittest.TestSuite(MixedPartitions(name) for name in (
            "test_cold_file_and_reopen", "test_mixed_snapshot_fixed_query_and_accounting"))
    else:
        raise SystemExit("usage: partition_examples.py oracle|verify|demo")
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
