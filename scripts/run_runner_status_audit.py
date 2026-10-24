"""Generate a read-only status evidence report. No labels are imputed."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.runner_status_audit import audit_rows

def main():
    root=Path(__file__).resolve().parents[1]
    df=audit_rows(root/'data'/'keiba.db')
    print(df.evidence_status.value_counts().to_string())
    output=root/'runner_status_unresolved.csv'
    df.loc[df.evidence_status.eq('UNRESOLVED_NO_TIME')].to_csv(output,index=False,encoding='utf-8-sig')
    print('Unresolved cases exported:',output)
if __name__=='__main__': main()
