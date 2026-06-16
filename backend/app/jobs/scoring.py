"""
app/jobs/scoring.py  +  app/jobs/team_analytics.py
---------------------------------------------------
Weekly jobs that populate employee_risk_scores and team_analytics.
Run every Sunday night after the DB is current.

Run manually:
    python -m app.jobs.scoring
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

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SCORING JOB
# ─────────────────────────────────────────────────────────────────────────────

def _risk_category(score: float) -> str:
    if score >= 0.65:
        return "High"
    if score >= 0.35:
        return "Medium"
    return "Low"


def _risk_band(tenure_months: float) -> str:
    if tenure_months < 3:
        return "Infant (<3m)"
    if tenure_months < 12:
        return "Early Joiner (3-12m)"
    if 12 <= tenure_months <= 24:
        return "Tenure Cliff (12-24m)"
    if 36 <= tenure_months <= 48:
        return "Secondary Cliff (36-48m)"
    if tenure_months > 60:
        return "Long-Tenure (60m+)"
    return "General"


def _parse_date_safe(val) -> Optional[date]:
    if not val or str(val).lower() in ("nan", "null", "none", ""):
        return None
    try:
        return dateparser.parse(str(val), dayfirst=True).date()
    except Exception:
        return None


def _tenure_months(doj_str, ref_date: date) -> float:
    doj = _parse_date_safe(doj_str)
    if not doj:
        return 0.0
    return max(0.0, round((ref_date - doj).days / 30.44, 1))


def _top_factors_from_lr(lr_model, scaler, feature_names: list[str], x_row: np.ndarray) -> list[str]:
    """
    Return top-3 feature names by contribution magnitude for this employee.
    Contribution = |coefficient| * |scaled_feature_value|
    """
    try:
        x_scaled = scaler.transform(x_row.reshape(1, -1))[0]
        coef = np.abs(lr_model.coef_[0])
        contributions = coef * np.abs(x_scaled)
        top_idx = np.argsort(contributions)[::-1][:3]
        # Make factor names human-readable
        readable = []
        for i in top_idx:
            name = feature_names[i] if i < len(feature_names) else f"feature_{i}"
            readable.append(name.replace("_", " ").replace("jrny ", "").title())
        return readable
    except Exception:
        return []


def run_scoring_job(db: Session) -> dict:
    """
    Score all active employees using the current active LR model.
    Upserts into employee_risk_scores for today's date.
    """
    from app.jobs.ml_training import build_feature_matrix

    logger.info("Scoring job started.")
    today = date.today()

    # ── Load active model ─────────────────────────────────────────────────────
    model_row = db.execute(
        text("""
            SELECT id, model_object, scaler_object, calibrator_object, feature_names
            FROM ml_models
            WHERE is_active = 1 AND segment = 'All'
            ORDER BY trained_at DESC
            LIMIT 1
        """)
    ).fetchone()

    if not model_row:
        msg = "No active ML model found. Run ml_training job first."
        logger.error(msg)
        return {"status": "error", "reason": msg}

    model_id = model_row[0]

    def _from_bytes(b):
        if not b:
            return None
        return joblib.load(io.BytesIO(bytes(b)))

    lr_model   = _from_bytes(model_row[1])
    scaler     = _from_bytes(model_row[2])
    calibrator = _from_bytes(model_row[3])

    # Feature names stored as string repr of list — eval carefully
    try:
        import ast
        feature_names = ast.literal_eval(model_row[4])
    except Exception:
        feature_names = []

    logger.info("Loaded model id=%d, features=%d", model_id, len(feature_names))

    # ── Load active employees ─────────────────────────────────────────────────
    rows = db.execute(
        text("""
            SELECT
                e.emp_id, e.dateofjoining, e.employee_status,
                e.gender, e.age, e.band_level, e.entity, e.resource_group,
                e.indegene_exp, e.total_exp,
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
            WHERE e.employee_status IN ('Active', 'Serving Notice')
              AND e.emp_id IS NOT NULL AND TRIM(e.emp_id) != ''
        """)
    ).fetchall()

    col_names = [
        "emp_id","dateofjoining","employee_status","gender","age","band_level",
        "entity","resource_group","indegene_exp","total_exp",
        "jrny_promotions","jrny_lateral_moves","jrny_band_changes",
        "jrny_dept_changes","jrny_manager_changes","jrny_resource_group_changes",
        "jrny_months_since_last_promotion","jrny_months_since_last_rotation",
        "jrny_months_since_last_manager_change","jrny_last_movement_months",
        "jrny_time_to_first_promotion","jrny_bands_held","jrny_departments_visited",
        "jrny_is_stagnant","jrny_no_rotation_2yr","jrny_no_promotion_2yr",
        "jrny_recently_moved","jrny_frozen_at_band",
    ]
    df = pd.DataFrame(rows, columns=col_names)
    logger.info("Scoring %d active employees.", len(df))

    if df.empty:
        return {"status": "ok", "scored": 0}

    # ── Build feature matrix ───────────────────────────────────────────────────
    X_df, built_feature_names = build_feature_matrix(df, today)

    # Align columns to what the model was trained on
    if feature_names:
        for col in feature_names:
            if col not in X_df.columns:
                X_df[col] = 0
        X_df = X_df[feature_names]

    X = X_df.fillna(0).values
    X_scaled = scaler.transform(X)

    # ── Predict ───────────────────────────────────────────────────────────────
    raw_proba = lr_model.predict_proba(X_scaled)[:, 1]
    if calibrator is not None:
        proba = calibrator.predict(raw_proba)
    else:
        proba = raw_proba

    # ── Write to employee_risk_scores ──────────────────────────────────────────
    scored = 0
    for idx, row in df.iterrows():
        emp_id = row["emp_id"]
        score  = float(np.clip(proba[idx], 0.0, 1.0))
        tenure = _tenure_months(row["dateofjoining"], today)
        factors = _top_factors_from_lr(lr_model, scaler, X_df.columns.tolist(), X[idx])

        db.execute(
            text("""
                INSERT INTO employee_risk_scores (
                    emp_id, scored_date, attrition_risk_score, risk_category,
                    risk_band, top_risk_factor_1, top_risk_factor_2, top_risk_factor_3,
                    model_segment, model_id, created_at
                ) VALUES (
                    :emp_id, :scored_date, :score, :category,
                    :risk_band, :f1, :f2, :f3,
                    'All', :model_id, :now
                )
                ON DUPLICATE KEY UPDATE
                    attrition_risk_score = VALUES(attrition_risk_score),
                    risk_category        = VALUES(risk_category),
                    risk_band            = VALUES(risk_band),
                    top_risk_factor_1    = VALUES(top_risk_factor_1),
                    top_risk_factor_2    = VALUES(top_risk_factor_2),
                    top_risk_factor_3    = VALUES(top_risk_factor_3),
                    model_id             = VALUES(model_id),
                    created_at           = VALUES(created_at)
            """),
            {
                "emp_id": emp_id,
                "scored_date": today,
                "score": round(score, 4),
                "category": _risk_category(score),
                "risk_band": _risk_band(tenure),
                "f1": factors[0] if len(factors) > 0 else None,
                "f2": factors[1] if len(factors) > 1 else None,
                "f3": factors[2] if len(factors) > 2 else None,
                "model_id": model_id,
                "now": datetime.utcnow(),
            }
        )
        scored += 1

    db.commit()
    result = {"status": "ok", "scored": scored, "date": str(today)}
    logger.info("Scoring job complete: %s", result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# TEAM ANALYTICS JOB
# ─────────────────────────────────────────────────────────────────────────────

def run_team_analytics_job(db: Session) -> dict:
    """
    Compute team-level KPIs for every manager and write to team_analytics.
    Called after the scoring job completes.
    """
    logger.info("Team analytics job started.")
    today = date.today()

    # Get all unique managers who have at least one active report
    mgr_rows = db.execute(
        text("""
            SELECT DISTINCT rm_empcode, reporting_manager
            FROM employee_details
            WHERE rm_empcode IS NOT NULL
              AND TRIM(rm_empcode) != ''
              AND employee_status IN ('Active', 'Serving Notice')
        """)
    ).fetchall()

    processed = 0
    for mgr_row in mgr_rows:
        rm_empcode     = mgr_row[0]
        manager_name   = mgr_row[1]

        # Active team members
        team_rows = db.execute(
            text("""
                SELECT e.emp_id, e.dateofjoining, e.do_relieving,
                       e.employee_status, e.resignation_type,
                       r.attrition_risk_score, r.risk_category,
                       j.jrny_is_stagnant
                FROM employee_details e
                LEFT JOIN employee_risk_scores r
                    ON e.emp_id = r.emp_id
                    AND r.scored_date = (SELECT MAX(scored_date) FROM employee_risk_scores)
                LEFT JOIN employee_journey_features j ON e.emp_id = j.emp_id
                WHERE e.rm_empcode = :mgr
                  AND e.employee_status IN ('Active', 'Serving Notice')
            """),
            {"mgr": rm_empcode}
        ).fetchall()

        # Exits in trailing 12 months (for this manager's historical team)
        ttm_exits = db.execute(
            text("""
                SELECT COUNT(*) FROM employee_details
                WHERE rm_empcode = :mgr
                  AND employee_status IN ('Left','Resigned','Terminated','Inactive')
                  AND do_relieving IS NOT NULL
                  AND STR_TO_DATE(do_relieving, '%d-%b-%y') >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH)
            """),
            {"mgr": rm_empcode}
        ).scalar() or 0

        active_count = len(team_rows)
        if active_count == 0:
            continue

        # Compute tenure for each member
        tenures = []
        for r in team_rows:
            t = _tenure_months(r[1], today)
            tenures.append(t)

        avg_tenure    = sum(tenures) / len(tenures) if tenures else 0.0
        infant_count  = sum(1 for t in tenures if t < 12)
        stagnant_count = sum(1 for r in team_rows if r[7] == 1)

        # Risk counts
        risk_scores = [r[5] for r in team_rows if r[5] is not None]
        high_risk   = sum(1 for r in team_rows if r[6] == "High")
        med_risk    = sum(1 for r in team_rows if r[6] == "Medium")
        low_risk    = sum(1 for r in team_rows if r[6] == "Low")
        avg_risk    = sum(risk_scores) / len(risk_scores) if risk_scores else 0.0

        # TTM attrition % = exits / avg_headcount * 100
        ttm_atr_pct = round(ttm_exits / max(active_count, 1) * 100, 2)

        db.execute(
            text("""
                INSERT INTO team_analytics (
                    rm_empcode, manager_name, period_date, team_size, active_count,
                    left_count_ttm, ttm_attrition_pct, ttm_vol_attrition_pct,
                    ttm_invol_attrition_pct, avg_tenure_months, infant_count,
                    infant_attrition_pct, stagnant_count, high_risk_count,
                    medium_risk_count, low_risk_count, avg_risk_score, created_at
                ) VALUES (
                    :mgr, :name, :period, :team_size, :active,
                    :left_ttm, :ttm_atr, 0.0, 0.0, :avg_tenure, :infant,
                    0.0, :stagnant, :high, :med, :low, :avg_risk, :now
                )
                ON DUPLICATE KEY UPDATE
                    manager_name          = VALUES(manager_name),
                    team_size             = VALUES(team_size),
                    active_count          = VALUES(active_count),
                    left_count_ttm        = VALUES(left_count_ttm),
                    ttm_attrition_pct     = VALUES(ttm_attrition_pct),
                    avg_tenure_months     = VALUES(avg_tenure_months),
                    infant_count          = VALUES(infant_count),
                    stagnant_count        = VALUES(stagnant_count),
                    high_risk_count       = VALUES(high_risk_count),
                    medium_risk_count     = VALUES(medium_risk_count),
                    low_risk_count        = VALUES(low_risk_count),
                    avg_risk_score        = VALUES(avg_risk_score),
                    created_at            = VALUES(created_at)
            """),
            {
                "mgr": rm_empcode, "name": manager_name, "period": today,
                "team_size": active_count, "active": active_count,
                "left_ttm": ttm_exits, "ttm_atr": ttm_atr_pct,
                "avg_tenure": round(avg_tenure, 1), "infant": infant_count,
                "stagnant": stagnant_count, "high": high_risk,
                "med": med_risk, "low": low_risk, "avg_risk": round(avg_risk, 4),
                "now": datetime.utcnow(),
            }
        )
        processed += 1

    db.commit()
    result = {"status": "ok", "managers_processed": processed}
    logger.info("Team analytics job complete: %s", result)
    return result


# ── Combined weekly job entry point ───────────────────────────────────────────
def run_weekly_jobs(db: Session) -> dict:
    """Entry point for the Azure Function timer trigger (every Sunday)."""
    scoring_result       = run_scoring_job(db)
    team_analytics_result = run_team_analytics_job(db)
    return {"scoring": scoring_result, "team_analytics": team_analytics_result}


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        result = run_weekly_jobs(db)
        print(f"\nResult: {result}")
    finally:
        db.close()