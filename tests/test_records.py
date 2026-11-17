import unittest
from tests.test_acceptance import LakeCase, record
from obslake.records import validate_record
from obslake.row_codec import encode_file, decode_file
from obslake.records import LocatedRow

class TestRecords(LakeCase):
    def test_strict_records_and_rejection_atomicity(self):
        bad = []
        for field in ('time','device','amount'):
            for value in (True,False,1.0,'1'):
                row = record(); row[field]=value; bad.append([row])
        row = record(); del row['amount']; bad.append([row])
        row = record(); row['extra']=1; bad.append([row])
        bad += [[record('')],[record(),record()],[None]]
        before = self.lake.snapshot()
        for rows in bad:
            with self.assertRaises(ValueError):
                self.lake.write('bad',rows)
            self.assertEqual(self.lake.snapshot(),before)
        for request in ('',None,True,1):
            with self.assertRaises(ValueError):
                self.lake.write(request,[record()])
        self.assertEqual(self.count('batches'),0)
        self.assertEqual(self.count('receipts'),0)

    def test_codec_arbitrary_integers_and_null(self):
        huge = 10**5000
        row = validate_record(record('unicode-岚湾',huge,-huge,huge+1))
        located = LocatedRow('batch',0,row)
        self.assertEqual(decode_file(encode_file((located,))),(located,))
        self.lake.write('huge',[row.as_dict()])
        self.reopen()
        result = self.query(start=huge//86400,end=huge//86400+1)['payload']
        self.assertEqual(result['rows'][0]['amount'],huge+1)
        self.assertEqual(result['groups'][0]['sum_amount'],huge+1)
        null = LocatedRow('batch',1,validate_record(record('n',None,None,None)))
        self.assertEqual(decode_file(encode_file((null,))),(null,))
        for data in (b'{}',b'{"version":1,"version":1,"rows":[]}',b'not json',
                     b'{"rows":[null],"version":1}',b'{"rows":[],"version":true}'):
            with self.assertRaises(ValueError):
                decode_file(data)
