import sqlite3, tempfile, unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.runner_status_audit import audit_rows, apply_verified_status

class TestRunnerStatusAudit(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.db=Path(self.tmp.name)/'sample.db'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE races(race_id TEXT,date TEXT)')
            c.execute('CREATE TABLE race_results(race_id TEXT,horse_number INT,horse_id TEXT,horse_name TEXT,finish_order INT,time_str TEXT,odds REAL,popularity INT,margin_str TEXT)')
            c.execute("INSERT INTO races VALUES('R1','2026-01-01')")
            c.executemany('INSERT INTO race_results VALUES(?,?,?,?,?,?,?,?,?)',[
                ('R1',1,'H1','timed',1,'1:34.2',2.5,1,''),
                ('R1',2,'H2','ambiguous',15,'-',15,8,''),
                ('R1',3,'H3','unconfirmed',0,'',None,None,'')])
    def tearDown(self): self.tmp.cleanup()
    def test_ambiguous_not_assumed_scratch_or_dnf(self):
        df=audit_rows(self.db)
        self.assertEqual(df.evidence_status.tolist(),['TIMED_FINISH','UNRESOLVED_NO_TIME','NO_RESULT_RECORDED'])
        self.assertFalse(df.training_eligible.any())
    def test_explicit_verified_status_only(self):
        df=audit_rows(self.db)
        v=pd.DataFrame([dict(race_id='R1',horse_number=1,start_status='STARTED',verified_source='official_record',verified_at='2026-01-02T00:00:00+09:00'),dict(race_id='R1',horse_number=2,start_status='STARTED',verified_source='official_record',verified_at='2026-01-02T00:00:00+09:00')])
        out=apply_verified_status(df,v)
        self.assertEqual(out.training_eligible.tolist(),[True,False,False])
        v.loc[1,'start_status']='GUESS'
        with self.assertRaises(ValueError): apply_verified_status(df,v)
    def test_db_read_only(self):
        before=self.db.read_bytes(); audit_rows(self.db); self.assertEqual(before,self.db.read_bytes())

if __name__=='__main__': unittest.main()
