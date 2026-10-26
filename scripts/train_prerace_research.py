"""Research-only win model trained from pre-race feature bundles and separate labels.

This is NOT a production prediction model: manual capture times and labels are
not independently verified. The sealed 2026 holdout is never loaded. No
betting, EV computation, or automatic prediction is enabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from scripts.validate_prerace_features import (
    ALLOWED, CATEGORICAL, aware_time, validate_feature_bundle,
)

NUMERIC = sorted(ALLOWED - CATEGORICAL - {"horse_number"})
CATS = sorted(CATEGORICAL)
REQUIRED_LABEL_KEYS = {"race_id", "horse_number", "won"}


def _load_dataset(bundles_dir, labels_path):
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    if not isinstance(labels, list) or not labels:
        raise ValueError("Labels must be a nonempty list")
    by_key = {}
    for row in labels:
        if not isinstance(row, dict) or set(row) != REQUIRED_LABEL_KEYS:
            raise ValueError("Each label must contain only race_id, horse_number, won")
        race, horse, won = row["race_id"], row["horse_number"], row["won"]
        if not isinstance(race, str) or not race.strip() or type(horse) is not int or type(won) is not bool:
            raise ValueError("Invalid label")
        key = (race, horse)
        if key in by_key:
            raise ValueError("Duplicate label")
        by_key[key] = int(won)
    dataset, seen_races, used_labels = [], set(), set()
    paths = sorted(Path(bundles_dir).glob("*.json"))
    if not paths:
        raise ValueError("No feature bundles found")
    for path in paths:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        start = aware_time(bundle["race_start_time"])
        if start.year >= 2026:
            raise ValueError("2026+ data is sealed and prohibited for model development")
        validate_feature_bundle(bundle, as_of_time=bundle["received_at"])
        race = bundle["race_id"]
        if race in seen_races:
            raise ValueError("Duplicate race bundle")
        seen_races.add(race)
        winners = 0
        for features in bundle["features"]:
            key = (race, features["horse_number"])
            if key not in by_key:
                raise ValueError("Missing result label for horse")
            used_labels.add(key)
            won = by_key[key]
            winners += won
            dataset.append((start, race, features, won))
        if winners != 1:
            raise ValueError("Exactly one winner required per complete race")
    if set(by_key) != used_labels:
        raise ValueError("Labels include horses or races absent from bundles")
    dataset.sort(key=lambda row: (row[0], row[1], row[2]["horse_number"]))
    return dataset, paths


def train_research_model(bundles_dir, labels_path, *, train_before, validate_before, min_races=20):
    train_cutoff, val_cutoff = aware_time(train_before), aware_time(validate_before)
    if not train_cutoff < val_cutoff or val_cutoff.year >= 2026:
        raise ValueError("Require chronological train/validation cutoffs before sealed 2026")
    if type(min_races) is not int or min_races < 2:
        raise ValueError("min_races must be >= 2")
    dataset, paths = _load_dataset(bundles_dir, labels_path)
    train = [row for row in dataset if row[0] < train_cutoff]
    valid = [row for row in dataset if train_cutoff <= row[0] < val_cutoff]
    excluded = [row for row in dataset if row[0] >= val_cutoff]
    if len({r[1] for r in train}) < min_races or len({r[1] for r in valid}) < min_races:
        raise ValueError("Insufficient complete races in train or validation window")
    if len({r[3] for r in train}) < 2 or len({r[3] for r in valid}) < 2:
        raise ValueError("Both classes required")
    # No result columns, horse ID, odds, or race date can enter X.
    fields = NUMERIC + CATS
    X_train = [[r[2][key] for key in fields] for r in train]
    X_valid = [[r[2][key] for key in fields] for r in valid]
    y_train = np.asarray([r[3] for r in train])
    y_valid = np.asarray([r[3] for r in valid])
    numeric_idx = list(range(len(NUMERIC)))
    cat_idx = list(range(len(NUMERIC), len(fields)))
    transformer = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median")),
                           ("scale", StandardScaler())]), numeric_idx),
        ("cat", OneHotEncoder(handle_unknown="ignore"), cat_idx),
    ])
    model = Pipeline([
        ("features", transformer),
        ("classifier", LogisticRegression(max_iter=1000, random_state=0)),
    ])
    model.fit(X_train, y_train)
    prob = model.predict_proba(X_valid)[:, 1]
    if not np.all(np.isfinite(prob)):
        raise ValueError("Nonfinite probabilities")
    # Each horse's output is a binary win score, NOT a calibrated race-level
    # win distribution. It must not be used to calculate betting EV.
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    digest.update(Path(labels_path).read_bytes())
    return {
        "status": "RESEARCH_ONLY_UNCERTIFIED",
        "bet_status": "NO_BET",
        "asof_certified": False,
        "production_inference_allowed": False,
        "holdout_2026_accessed": False,
        "train_races": len({r[1] for r in train}),
        "validation_races": len({r[1] for r in valid}),
        "train_horses": len(train),
        "validation_horses": len(valid),
        "excluded_post_validation_horses": len(excluded),
        "train_before": train_cutoff.isoformat(),
        "validate_before": val_cutoff.isoformat(),
        "features": fields,
        "brier_score": float(brier_score_loss(y_valid, prob)),
        "log_loss": float(log_loss(y_valid, prob, labels=[0, 1])),
        "roc_auc": float(roc_auc_score(y_valid, prob)),
        "input_sha256": digest.hexdigest(),
        "warning": "Manual provenance unverified; binary scores not calibrated race win probabilities; no EV or bets",
    }


def main():
    parser = argparse.ArgumentParser(description="Research-only time-split pre-race model")
    parser.add_argument("--bundles-dir", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--train-before", required=True)
    parser.add_argument("--validate-before", required=True)
    parser.add_argument("--min-races", type=int, default=20)
    parser.add_argument("--out")
    args = parser.parse_args()
    result = train_research_model(args.bundles_dir, args.labels,
                                  train_before=args.train_before,
                                  validate_before=args.validate_before,
                                  min_races=args.min_races)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
