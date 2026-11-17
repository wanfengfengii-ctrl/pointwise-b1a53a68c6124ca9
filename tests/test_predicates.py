import unittest
from obslake.records import Observation
from obslake.predicates import freeze_predicate, evaluate_predicate, matches_predicate, Truth

class TestPredicates(unittest.TestCase):
    def test_three_valued_logic(self):
        # Truth tables are literal independent SQL tables, not production operators.
        tables = {
            'and': [[Truth.TRUE,Truth.FALSE,Truth.UNKNOWN],[Truth.FALSE,Truth.FALSE,Truth.FALSE],[Truth.UNKNOWN,Truth.FALSE,Truth.UNKNOWN]],
            'or': [[Truth.TRUE,Truth.TRUE,Truth.TRUE],[Truth.TRUE,Truth.FALSE,Truth.UNKNOWN],[Truth.TRUE,Truth.UNKNOWN,Truth.UNKNOWN]]}
        atoms = [['eq','time',1],['eq','time',0],['eq','device',7]]
        row = Observation('x',1,None,0)
        for op, table in tables.items():
            for i,left in enumerate(atoms):
                for j,right in enumerate(atoms):
                    self.assertIs(evaluate_predicate(freeze_predicate([op,left,right]),row),table[i][j])
        for atom,expected in zip(atoms,[Truth.FALSE,Truth.TRUE,Truth.UNKNOWN]):
            self.assertIs(evaluate_predicate(freeze_predicate(['not',atom]),row),expected)
        self.assertFalse(matches_predicate(freeze_predicate(['not',['eq','device',7]]),row))
        self.assertTrue(matches_predicate(freeze_predicate(['is_null','device']),row))

    def test_comparisons_and_invalid_trees(self):
        row = Observation('x',-1,8,0)
        expected = {'lt':True,'le':True,'eq':False,'ne':True,'ge':False,'gt':False}
        for op,answer in expected.items():
            self.assertEqual(matches_predicate(freeze_predicate([op,'time',0]),row),answer)
        self.assertTrue(matches_predicate(freeze_predicate(None),row))
        bad = [['eq','amount',1],['eq','time',True],['is_null','id'],['not'],['and',None],['unknown'],{},[],42,['eq',[],1]]
        cycle = ['not',None]; cycle[1] = cycle; bad.append(cycle)
        for predicate in bad:
            with self.assertRaises(ValueError):
                freeze_predicate(predicate)
        shared = ['eq','time',0]
        self.assertEqual(freeze_predicate(['and',shared,shared]), freeze_predicate(['and',['eq','time',0],['eq','time',0]]))
        deep = None
        for _ in range(5000):
            deep = ['not',deep]
        self.assertTrue(matches_predicate(freeze_predicate(deep),row))
