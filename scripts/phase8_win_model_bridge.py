"""Phase 8: generate Phase 7-compatible win-probability CSV from the WIN model.

Only accepts precomputed, pre-race features; does not scrape, backfill, or bet.
Never use a top-three probability as a win probability.
"""
import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def build_prediction_rows(features, model, *, prediction_at, cutoff_at,
                          model_id, source):
    """Pure adapter; model must expose predict_win_race()."""
    from src.paper_trading import utc_time
    import pandas as pd
    pred, cutoff = utc_time(prediction_at), utc_time(cutoff_at)
    if pred > cutoff:
        raise ValueError('prediction timestamp exceeds cutoff')
    if not model_id or not model_id.strip() or not source or not source.strip():
        raise ValueError('model_id and source are required')
    if not {'race_id', 'horse_number'}.issubset(features.columns):
        raise ValueError('race_id and horse_number are required')
    if features.empty:
        raise ValueError('empty feature input')
    if features[['race_id','horse_number']].isna().any().any():
        raise ValueError('missing race or horse identifier')
    if features.duplicated(['race_id','horse_number']).any():
        raise ValueError('duplicate race/horse features')
    results = []
    for race_id, group in features.groupby('race_id', sort=False):
        # Predict separately for each race: win normalization must not cross races.
        output = model.predict_win_race(group.copy(), normalize_to_one=True)
        if 'pred_win_prob' not in output:
            raise ValueError('win model did not return pred_win_prob')
        if len(output) != len(group):
            raise ValueError('model changed number of horses')
        for _, row in output.iterrows():
            probability = float(row['pred_win_prob'])
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError('invalid win probability')
            horse = int(row['horse_number'])
            if horse < 1 or horse > 28:
                raise ValueError('invalid horse number')
            results.append(dict(race_id=str(race_id), horse_number=horse,
                prediction_at=pred, cutoff_at=cutoff,
                win_probability=probability, model_id=model_id, source=source))
    return results

def export_predictions(features_path, output_path, model_path, prediction_at,
                       cutoff_at, source, *, feature_cutoff_at=None):
    """Fail closed unless feature snapshot provenance is supplied and <= prediction."""
    from src.paper_trading import utc_time
    from src.model import KeibaAIWinModel
    import pandas as pd
    if not feature_cutoff_at:
        raise ValueError('feature_cutoff_at required; cannot prove feature snapshot is pre-race')
    if utc_time(feature_cutoff_at) > utc_time(prediction_at):
        raise ValueError('feature snapshot newer than prediction timestamp')
    path = Path(features_path)
    if path.suffix.lower() == '.parquet':
        features = pd.read_parquet(path)
    elif path.suffix.lower() == '.csv':
        features = pd.read_csv(path, dtype={'race_id': str})
    else:
        raise ValueError('features must be .csv or .parquet')
    if not Path(model_path).is_file():
        raise FileNotFoundError(model_path)
    # Require the project's authenticated model loading mechanism.
    model = KeibaAIWinModel(model_path=Path(model_path))
    model.load()
    model_hash = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
    model_id = 'win-v2-28-sha256:' + model_hash[:16]
    rows = build_prediction_rows(features, model, prediction_at=prediction_at,
                                 cutoff_at=cutoff_at, model_id=model_id, source=source)
    output = Path(output_path)
    if output.exists():
        raise FileExistsError('refusing to overwrite prediction snapshot')
    if not output.parent.exists():
        raise FileNotFoundError('output parent directory does not exist')
    with output.open('x', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['race_id','horse_number','prediction_at',
            'cutoff_at','win_probability','model_id','source'])
        writer.writeheader()
        writer.writerows(rows)
    return {'exported': len(rows), 'model_id': model_id, 'output': str(output)}

def main(argv=None):
    p = argparse.ArgumentParser(description='Phase 8 win-model -> Phase 7 prediction CSV')
    p.add_argument('--features', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--prediction-at', required=True)
    p.add_argument('--cutoff-at', required=True)
    p.add_argument('--feature-cutoff-at', required=True)
    p.add_argument('--source', required=True)
    args = p.parse_args(argv)
    print(json.dumps(export_predictions(args.features, args.output, args.model,
        args.prediction_at, args.cutoff_at, args.source,
        feature_cutoff_at=args.feature_cutoff_at), ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
