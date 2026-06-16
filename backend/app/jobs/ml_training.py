"""
app/jobs/ml_training.py
------------------------
Monthly Logistic Regression training job.

What it does:
  1. Pulls employees (active + left) from employee_details + journey features
  2. Builds the feature matrix
  3. Trains Logistic Regression (label = voluntary attrition)
  4. Applies isotonic calibration so risk scores are real probabilities
  5. Evaluates AUC on a temporal test set
  6. If AUC >= ML_AUC_GATE: deactivates old model, saves new one to ml_models
  7. Logs the result

Run manually:   python -m app.jobs.ml_training
Azure Function: azure_functions/monthly_ml_retrain/ (timer: 3rd of month)
"""

import io
import logging
from datetime import date, datetime
from typing import Optional

import joblib
import numpy as np
import pandas as pd
from dateutil import parser as dateparser
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
ACTIVE_STATUSES   = {"active", "serving notice"}
LEFT_STATUSES     = {"left", "resigned", "terminated", "inactive", "separated",
                      "relieved", "absconded", "attrited", "closed"}
VOLUNTARY_TYPES   = {"voluntary", "vol"}
INFANT_MONTHS     = 12
MIN_TRAIN_ROWS    = 50
MIN_TRAIN_POS     = 5

# Band hierarchy for rank encoding
BAND_ORDER = ["E2","E1","D2","D1","C3","C2","C1","B3","B2","B1","A5","A4","A3","A2","A1","T0"]
BAND_RANK  = {b: i for i, b in enumerate(reversed(BAND_ORDER))}  # T0=0, A1=1, ..., E2=15


# ── Feature engineering ───────────────────────────────────────────────────────
def _parse_date_safe(val) -> Optional[date]:
    if not val or str(val).lower() in ("nan", "null", "none", ""):
        return None
    try:
        return dateparser.parse(str(val), dayfirst=True).date()
    except Exception:
        return None


def _tenure_months(doj: Optional[date], ref_date: date) -> float:
    if not doj:
        return 0.0
    return max(0.0, round((ref_date - doj).days / 30.44, 1))


def _tenure_cliff_distance(tenure_m: float) -> float:
    """Distance to the nearest known high-attrition tenure cliff (6,12,24,36,60m)."""
    cliffs = [6, 12, 24, 36, 60]
    return min(abs(tenure_m - c) for c in cliffs)


def _age_safe(age_str) -> Optional[float]:
    try:
        return float(str(age_str).split()[0])
    except Exception:
        return None


def build_feature_matrix(df: pd.DataFrame, ref_date: date) -> tuple[pd.DataFrame, list[str]]:
    """
    Build the feature matrix from a DataFrame of employees.
    Returns (feature_df, feature_names) — feature_df has no NaN (filled with 0).
    """
    features = pd.DataFrame()

    # ── Tenure features ───────────────────────────────────────────────────────
    doj_dates = df["dateofjoining"].apply(_parse_date_safe)
    tenure = doj_dates.apply(lambda d: _tenure_months(d, ref_date))
    features["tenure_months"]         = tenure
    features["tenure_months_sq"]      = tenure ** 2
    features["tenure_cliff_distance"] = tenure.apply(_tenure_cliff_distance)
    features["is_infant"]             = (tenure < INFANT_MONTHS).astype(int)
    features["is_tenure_cliff_12_24"] = ((tenure >= 12) & (tenure <= 24)).astype(int)
    features["is_tenure_cliff_36_48"] = ((tenure >= 36) & (tenure <= 48)).astype(int)

    # ── Age ───────────────────────────────────────────────────────────────────
    features["age"] = df.get("age", pd.Series(dtype=str)).apply(_age_safe).fillna(0)

    # ── Band encoding ─────────────────────────────────────────────────────────
    features["band_rank"] = (
        df.get("band_level", pd.Series(dtype=str))
        .str.strip().str.upper()
        .map(lambda b: BAND_RANK.get(b, 0))
    )

    # ── Categorical one-hot ───────────────────────────────────────────────────
    for col, prefix in [("gender","gender"), ("entity","entity"), ("resource_group","rg")]:
        if col in df.columns:
            dummies = pd.get_dummies(
                df[col].fillna("Unknown").str.strip().str.title(),
                prefix=prefix, drop_first=False, dtype=int,
            )
            features = pd.concat([features, dummies], axis=1)

    # ── Journey features (from employee_journey_features join) ────────────────
    jrny_cols = [
        "jrny_promotions", "jrny_lateral_moves", "jrny_band_changes",
        "jrny_dept_changes", "jrny_manager_changes", "jrny_resource_group_changes",
        "jrny_months_since_last_promotion", "jrny_months_since_last_rotation",
        "jrny_months_since_last_manager_change", "jrny_last_movement_months",
        "jrny_time_to_first_promotion", "jrny_bands_held", "jrny_departments_visited",
        "jrny_is_stagnant", "jrny_no_rotation_2yr", "jrny_no_promotion_2yr",
        "jrny_recently_moved", "jrny_frozen_at_band",
    ]
    for col in jrny_cols:
        if col in df.columns:
            features[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # ── Previous experience ───────────────────────────────────────────────────
    for col in ["indegene_exp", "total_exp"]:
        if col in df.columns:
            features[col] = pd.to_numeric(
                df[col].astype(str).str.extract(r"([\d.]+)")[0],
                errors="coerce"
            ).fillna(0)

    features = features.fillna(0)
    return features, list(features.columns)


# ── Main training function ─────────────────────────────────────────────────────
def run_ml_training_job(db: Session) -> dict:
    """
    Trains Logistic Regression and saves to ml_models if AUC passes the gate.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split

    logger.info("ML training job started.")
    today = date.today()
    ref_date = today  # features computed as of today

    # ── 1. Load data ──────────────────────────────────────────────────────────
    rows = db.execute(
        text("""
            SELECT
                e.emp_id, e.dateofjoining, e.do_relieving, e.employee_status,
                e.resignation_type, e.gender, e.age, e.band_level, e.entity,
                e.resource_group, e.indegene_exp, e.total_exp,
                j.jrny_promotions, j.jrny_lateral_moves, j.jrny_band_changes,
                j.jrny_dept_changes, j.jrny_manager_changes,
                j.jrny_resource_group_changes,
                j.jrny_months_since_last_promotion,
                j.jrny_months_since_last_rotation,
                j.jrny_months_since_last_manager_change,
                j.jrny_last_movement_months, j.jrny_time_to_first_promotion,
                j.jrny_bands_held, j.jrny_departments_visited,
                j.jrny_is_stagnant, j.jrny_no_rotation_2yr,
                j.jrny_no_promotion_2yr, j.jrny_recently_moved,
                j.jrny_frozen_at_band
            FROM employee_details e
            LEFT JOIN employee_journey_features j ON e.emp_id = j.emp_id
            WHERE e.emp_id IS NOT NULL AND TRIM(e.emp_id) != ''
        """)
    ).fetchall()

    col_names = [
        "emp_id","dateofjoining","do_relieving","employee_status","resignation_type",
        "gender","age","band_level","entity","resource_group","indegene_exp","total_exp",
        "jrny_promotions","jrny_lateral_moves","jrny_band_changes","jrny_dept_changes",
        "jrny_manager_changes","jrny_resource_group_changes",
        "jrny_months_since_last_promotion","jrny_months_since_last_rotation",
        "jrny_months_since_last_manager_change","jrny_last_movement_months",
        "jrny_time_to_first_promotion","jrny_bands_held","jrny_departments_visited",
        "jrny_is_stagnant","jrny_no_rotation_2yr","jrny_no_promotion_2yr",
        "jrny_recently_moved","jrny_frozen_at_band",
    ]
    df = pd.DataFrame(rows, columns=col_names)
    logger.info("Loaded %d employees for training.", len(df))

    # ── 2. Build label (voluntary attrition only) ──────────────────────────────
    status_lower = df["employee_status"].fillna("").str.lower().str.strip()
    res_type_lower = df["resignation_type"].fillna("").str.lower().str.strip()

    is_left     = status_lower.isin(LEFT_STATUSES)
    is_voluntary = res_type_lower.str.contains("vol", na=False) | (res_type_lower == "")

    # Label = 1 if employee left voluntarily
    # Involuntary exits are excluded from training (they're a different process)
    modeling_mask = status_lower.isin(ACTIVE_STATUSES) | (is_left & is_voluntary)
    df_model = df[modeling_mask].copy()
    df_model["label"] = (
        df_model["employee_status"].fillna("").str.lower().str.strip().isin(LEFT_STATUSES)
    ).astype(int)

    n_total = len(df_model)
    n_positives = int(df_model["label"].sum())
    logger.info("Modeling cohort: %d total, %d voluntary exits (positives).", n_total, n_positives)

    if n_total < MIN_TRAIN_ROWS or n_positives < MIN_TRAIN_POS:
        msg = f"Insufficient data: {n_total} rows, {n_positives} exits. Need ≥{MIN_TRAIN_ROWS}/{MIN_TRAIN_POS}."
        logger.warning(msg)
        return {"status": "skipped", "reason": msg}

    # ── 3. Build feature matrix ───────────────────────────────────────────────
    X_df, feature_names = build_feature_matrix(df_model, ref_date)
    X = X_df.values
    y = df_model["label"].values

    # ── 4. Temporal train/test split ──────────────────────────────────────────
    # Use do_relieving as the date for exits, dateofjoining as proxy for actives.
    # Sort by "latest known date" — exits by relieving date, actives by DOJ.
    def _sort_date(row) -> date:
        d = _parse_date_safe(row.get("do_relieving")) or _parse_date_safe(row.get("dateofjoining"))
        return d or date(2000, 1, 1)

    dates = df_model.apply(lambda r: _sort_date(r.to_dict()), axis=1)
    order = np.argsort(dates.values, kind="stable")
    split_idx = int(len(order) * 0.8)   # 80% train, 20% test
    train_idx = order[:split_idx]
    test_idx  = order[split_idx:]

    X_train, X_test = X[train_idx], X[test_idx]
    y_train, y_test = y[train_idx], y[test_idx]

    if y_train.sum() < MIN_TRAIN_POS or y_test.sum() < 2:
        msg = f"Not enough positives in split: train_pos={y_train.sum()}, test_pos={y_test.sum()}"
        logger.warning(msg)
        return {"status": "skipped", "reason": msg}

    # ── 5. Scale features ──────────────────────────────────────────────────────
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    # ── 6. Train Logistic Regression ──────────────────────────────────────────
    lr = LogisticRegression(
        C=1.0,
        class_weight="balanced",   # critical: handles the class imbalance
        max_iter=1000,
        random_state=42,
        solver="lbfgs",
    )
    lr.fit(X_train_s, y_train)
    raw_proba = lr.predict_proba(X_test_s)[:, 1]
    auc = roc_auc_score(y_test, raw_proba)
    logger.info("Logistic Regression AUC on test set: %.4f", auc)

    # ── 7. Isotonic calibration ────────────────────────────────────────────────
    calibrator = None
    if len(y_test) >= 30 and len(set(y_test)) > 1:
        try:
            calibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            calibrator.fit(raw_proba, y_test.astype(int))
            cal_proba = calibrator.predict(raw_proba)
            logger.info("Isotonic calibration applied. AUC unchanged: %.4f", roc_auc_score(y_test, cal_proba))
        except Exception as e:
            logger.warning("Calibration failed, continuing without: %s", e)
            calibrator = None

    # ── 8. AUC gate ───────────────────────────────────────────────────────────
    if auc < settings.ML_AUC_GATE:
        msg = f"AUC {auc:.4f} is below gate {settings.ML_AUC_GATE}. Model NOT saved."
        logger.warning(msg)
        return {
            "status": "rejected",
            "auc": round(auc, 4),
            "reason": msg,
            "train_n": int(len(y_train)),
            "train_positives": int(y_train.sum()),
        }

    # ── 9. Serialise models ───────────────────────────────────────────────────
    def _to_bytes(obj) -> bytes:
        buf = io.BytesIO()
        joblib.dump(obj, buf)
        return buf.getvalue()

    model_bytes    = _to_bytes(lr)
    scaler_bytes   = _to_bytes(scaler)
    calib_bytes    = _to_bytes(calibrator) if calibrator else None

    # ── 10. Deactivate old model, save new one ────────────────────────────────
    db.execute(text("UPDATE ml_models SET is_active = 0 WHERE segment = 'All'"))
    db.execute(
        text("""
            INSERT INTO ml_models (
                segment, trained_at, model_object, scaler_object,
                calibrator_object, feature_names, auc, train_n,
                train_positives, is_active, notes
            ) VALUES (
                'All', :trained_at, :model_bytes, :scaler_bytes,
                :calib_bytes, :feature_names, :auc, :train_n,
                :train_pos, 1, :notes
            )
        """),
        {
            "trained_at": datetime.utcnow(),
            "model_bytes": model_bytes,
            "scaler_bytes": scaler_bytes,
            "calib_bytes": calib_bytes,
            "feature_names": str(feature_names),   # stored as JSON string
            "auc": round(auc, 4),
            "train_n": int(len(y_train)),
            "train_pos": int(y_train.sum()),
            "notes": f"Auto-retrain {today}. AUC={auc:.4f}. Features={len(feature_names)}.",
        }
    )
    db.commit()

    result = {
        "status": "saved",
        "auc": round(auc, 4),
        "train_n": int(len(y_train)),
        "train_positives": int(y_train.sum()),
        "test_n": int(len(y_test)),
        "test_positives": int(y_test.sum()),
        "features": len(feature_names),
        "calibrated": calibrator is not None,
    }
    logger.info("ML training job complete: %s", result)
    return result


# ── Standalone runner ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        result = run_ml_training_job(db)
        print(f"\nResult: {result}")
    finally:
        db.close()