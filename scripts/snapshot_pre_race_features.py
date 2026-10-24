"""Create a timestamped, hash-checked snapshot of supplied pre-race features."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.feature_provenance import snapshot_features

if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--race-id',required=True)
    a=p.parse_args()
    print(snapshot_features(a.source,a.output,a.race_id))
