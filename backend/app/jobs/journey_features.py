"""
Computes JRNY_* career journey features for all active employees.
Reads from employee_snapshots (monthly history) and writes to employee_journey_features.

This is the MySQL-native replacement for empjourney_v4.py.

Run manually:   python -m app.jobs.journey_features
Azure Function: azure_functions/monthly_journey_features/  (timer trigger: 2nd of month)
"""

import logging
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Band hierarchy — higher number = more senior
BAND_ORDER = ["E2","E1","D2","D1","C3","C2","C1","B3","B2","B1","A5","A4","A3","A2","A1","T0"]
BAND_RANK  = {b: len(BAND_ORDER) - i for i, b in enumerate(BAND_ORDER)}

STAGNATION_MONTHS = 24
MIN_TENURE_FOR_STAGNATION = 24


def _band_rank(band_str: Optional[str]) -> int:
    if not band_str:
        return 0
    s = str(band_str).strip().upper()
    return BAND_RANK.get(s, 0)


def _months_between(d1: date, d2: date) -> float:
    return (d2.year - d1.year) * 12 + (d2.month - d1.month)


def compute_journey_features_for_employee(
    emp_id: str,
    snapshots: list[dict],
    today: date,
) -> dict:
    """
    Given a list of monthly snapshots for one employee (sorted oldest→newest),
    compute all JRNY_* features and return them as a dict.

    Each snapshot dict has keys matching employee_snapshots columns.
    """
    if not snapshots:
        return _empty_features(emp_id)

    # Sort by snapshot_month ascending
    snaps = sorted(snapshots, key=lambda s: s["snapshot_month"])
    first = snaps[0]

    # ── Tenure ────────────────────────────────────────────────────────────────
    try:
        from dateutil import parser as dp
        doj = dp.parse(first.get("dateofjoining", ""), dayfirst=True).date()
    except Exception:
        doj = first["snapshot_month"]
    tenure_months = _months_between(doj, today)

    # ── Count changes between consecutive snapshots ───────────────────────────
    def _count_col_changes(col: str) -> tuple[int, Optional[float]]:
        """Return (change_count, months_since_last_change)."""
        count = 0
        last_change_month: Optional[date] = None
        prev_val = snaps[0].get(col)
        for snap in snaps[1:]:
            cur_val = snap.get(col)
            if cur_val and cur_val != prev_val and prev_val is not None:
                count += 1
                last_change_month = snap["snapshot_month"]
            prev_val = cur_val
        months_since = (
            _months_between(last_change_month, today)
            if last_change_month else None
        )
        return count, months_since

    band_changes,     months_since_band     = _count_col_changes("band_level")
    dept_changes,     months_since_dept     = _count_col_changes("department")
    div_changes,      _                     = _count_col_changes("division")
    loc_changes,      _                     = _count_col_changes("work_location")
    mgr_changes,      months_since_mgr      = _count_col_changes("rm_empcode")
    rg_changes,       months_since_rg       = _count_col_changes("resource_group")
    role_changes,     _                     = _count_col_changes("role")
    unit_changes,     _                     = _count_col_changes("unit")

    # ── Promotions vs lateral moves ───────────────────────────────────────────
    promotions = 0
    lateral_moves = 0
    months_since_promotion: Optional[float] = None
    time_to_first_promotion: Optional[float] = None

    prev_rank = _band_rank(snaps[0].get("band_level"))
    prev_band = snaps[0].get("band_level")
    for snap in snaps[1:]:
        cur_band = snap.get("band_level")
        cur_rank = _band_rank(cur_band)
        if cur_band and cur_band != prev_band and prev_band is not None:
            if cur_rank > prev_rank:
                # Band rank increased = promotion
                promotions += 1
                months_since_promotion = _months_between(snap["snapshot_month"], today)
                if time_to_first_promotion is None:
                    time_to_first_promotion = _months_between(doj, snap["snapshot_month"])
            else:
                lateral_moves += 1
        prev_rank = cur_rank
        prev_band = cur_band

    # ── Rotation = dept change or resource group change ───────────────────────
    months_since_rotation: Optional[float] = None
    if months_since_dept is not None and months_since_rg is not None:
        months_since_rotation = min(months_since_dept, months_since_rg)
    elif months_since_dept is not None:
        months_since_rotation = months_since_dept
    elif months_since_rg is not None:
        months_since_rotation = months_since_rg

    # ── Last movement = min(promotion, rotation) ──────────────────────────────
    last_movement: Optional[float] = None
    candidates = [x for x in [months_since_promotion, months_since_rotation] if x is not None]
    if candidates:
        last_movement = min(candidates)

    # ── Diversity counts ──────────────────────────────────────────────────────
    bands_held = len({s.get("band_level") for s in snaps if s.get("band_level")})
    depts_visited = len({s.get("department") for s in snaps if s.get("department")})
    roles_held = len({s.get("role") for s in snaps if s.get("role")})

    # ── Stagnation flags ──────────────────────────────────────────────────────
    has_enough_tenure = tenure_months >= MIN_TENURE_FOR_STAGNATION
    no_rotation_2yr = (months_since_rotation is None or months_since_rotation >= STAGNATION_MONTHS)
    no_promotion_2yr = (months_since_promotion is None or months_since_promotion >= STAGNATION_MONTHS)
    is_stagnant = has_enough_tenure and no_rotation_2yr and no_promotion_2yr

    # Frozen at band: 3+ years at the current band (36+ months)
    current_band = snaps[-1].get("band_level")
    frozen_band_months = 0
    for snap in reversed(snaps):
        if snap.get("band_level") == current_band:
            frozen_band_months += 1
        else:
            break
    frozen_at_band = frozen_band_months >= 36

    # Recently moved: any promotion or rotation in last 12 months
    recently_moved = any(
        x is not None and x <= 12
        for x in [months_since_promotion, months_since_rotation]
    )

    return {
        "emp_id":                           emp_id,
        "jrny_promotions":                  promotions,
        "jrny_lateral_moves":               lateral_moves,
        "jrny_band_changes":                band_changes,
        "jrny_dept_changes":                dept_changes,
        "jrny_division_changes":            div_changes,
        "jrny_location_changes":            loc_changes,
        "jrny_manager_changes":             mgr_changes,
        "jrny_resource_group_changes":      rg_changes,
        "jrny_role_changes":                role_changes,
        "jrny_unit_changes":                unit_changes,
        "jrny_months_since_last_promotion":  months_since_promotion,
        "jrny_months_since_last_rotation":   months_since_rotation,
        "jrny_months_since_last_manager_change": months_since_mgr,
        "jrny_last_movement_months":         last_movement,
        "jrny_time_to_first_promotion":      time_to_first_promotion,
        "jrny_bands_held":                   max(bands_held, 1),
        "jrny_departments_visited":          max(depts_visited, 1),
        "jrny_roles_held":                   max(roles_held, 1),
        "jrny_is_stagnant":                  int(is_stagnant),
        "jrny_no_rotation_2yr":              int(no_rotation_2yr),
        "jrny_no_promotion_2yr":             int(no_promotion_2yr),
        "jrny_recently_moved":               int(recently_moved),
        "jrny_frozen_at_band":               int(frozen_at_band),
        "computed_at":                       datetime.utcnow(),
    }


def _empty_features(emp_id: str) -> dict:
    return {
        "emp_id": emp_id,
        "jrny_promotions": 0, "jrny_lateral_moves": 0, "jrny_band_changes": 0,
        "jrny_dept_changes": 0, "jrny_division_changes": 0, "jrny_location_changes": 0,
        "jrny_manager_changes": 0, "jrny_resource_group_changes": 0, "jrny_role_changes": 0,
        "jrny_unit_changes": 0, "jrny_months_since_last_promotion": None,
        "jrny_months_since_last_rotation": None, "jrny_months_since_last_manager_change": None,
        "jrny_last_movement_months": None, "jrny_time_to_first_promotion": None,
        "jrny_bands_held": 1, "jrny_departments_visited": 1, "jrny_roles_held": 1,
        "jrny_is_stagnant": 0, "jrny_no_rotation_2yr": 0, "jrny_no_promotion_2yr": 0,
        "jrny_recently_moved": 0, "jrny_frozen_at_band": 0,
        "computed_at": datetime.utcnow(),
    }


def run_journey_features_job(db: Session) -> dict:
    """
    Main entry point for the monthly job.
    Reads all snapshots, computes JRNY_* for every active employee,
    and upserts into employee_journey_features.

    Returns a summary dict for logging.
    """
    logger.info("Journey features job started.")
    today = date.today()

    # Get all active employees
    active_rows = db.execute(
        text("""
            SELECT emp_id FROM employee_details
            WHERE employee_status IN ('Active', 'Serving Notice')
              AND emp_id IS NOT NULL AND TRIM(emp_id) != ''
        """)
    ).fetchall()
    active_ids = [r[0] for r in active_rows]
    logger.info("Active employees to process: %d", len(active_ids))

    # Get all snapshots grouped by emp_id
    snap_rows = db.execute(
        text("""
            SELECT emp_id, snapshot_month, dateofjoining, band_level, department,
                   division, work_location, rm_empcode, resource_group, role,
                   unit, employee_status
            FROM employee_snapshots
            ORDER BY emp_id, snapshot_month
        """)
    ).fetchall()

    # Group snapshots by emp_id
    snap_map: dict[str, list[dict]] = {}
    cols = ["emp_id","snapshot_month","dateofjoining","band_level","department",
            "division","work_location","rm_empcode","resource_group","role",
            "unit","employee_status"]
    for row in snap_rows:
        d = dict(zip(cols, row))
        snap_map.setdefault(d["emp_id"], []).append(d)

    processed = 0
    errors = 0

    for emp_id in active_ids:
        try:
            snaps = snap_map.get(emp_id, [])

            # If no snapshots yet: seed one from employee_details directly
            if not snaps:
                emp_row = db.execute(
                    text("""
                        SELECT emp_id, dateofjoining, band_level, department,
                               division, work_location, rm_empcode, resource_group,
                               role, unit, employee_status
                        FROM employee_details WHERE emp_id = :eid LIMIT 1
                    """),
                    {"eid": emp_id}
                ).fetchone()
                if emp_row:
                    snaps = [{
                        "emp_id": emp_row[0], "snapshot_month": today,
                        "dateofjoining": emp_row[1], "band_level": emp_row[2],
                        "department": emp_row[3], "division": emp_row[4],
                        "work_location": emp_row[5], "rm_empcode": emp_row[6],
                        "resource_group": emp_row[7], "role": emp_row[8],
                        "unit": emp_row[9], "employee_status": emp_row[10],
                    }]

            features = compute_journey_features_for_employee(emp_id, snaps, today)

            # UPSERT into employee_journey_features
            db.execute(
                text("""
                    INSERT INTO employee_journey_features (
                        emp_id, jrny_promotions, jrny_lateral_moves, jrny_band_changes,
                        jrny_dept_changes, jrny_division_changes, jrny_location_changes,
                        jrny_manager_changes, jrny_resource_group_changes, jrny_role_changes,
                        jrny_unit_changes, jrny_months_since_last_promotion,
                        jrny_months_since_last_rotation, jrny_months_since_last_manager_change,
                        jrny_last_movement_months, jrny_time_to_first_promotion,
                        jrny_bands_held, jrny_departments_visited, jrny_roles_held,
                        jrny_is_stagnant, jrny_no_rotation_2yr, jrny_no_promotion_2yr,
                        jrny_recently_moved, jrny_frozen_at_band, computed_at
                    ) VALUES (
                        :emp_id, :jrny_promotions, :jrny_lateral_moves, :jrny_band_changes,
                        :jrny_dept_changes, :jrny_division_changes, :jrny_location_changes,
                        :jrny_manager_changes, :jrny_resource_group_changes, :jrny_role_changes,
                        :jrny_unit_changes, :jrny_months_since_last_promotion,
                        :jrny_months_since_last_rotation, :jrny_months_since_last_manager_change,
                        :jrny_last_movement_months, :jrny_time_to_first_promotion,
                        :jrny_bands_held, :jrny_departments_visited, :jrny_roles_held,
                        :jrny_is_stagnant, :jrny_no_rotation_2yr, :jrny_no_promotion_2yr,
                        :jrny_recently_moved, :jrny_frozen_at_band, :computed_at
                    )
                    ON DUPLICATE KEY UPDATE
                        jrny_promotions = VALUES(jrny_promotions),
                        jrny_lateral_moves = VALUES(jrny_lateral_moves),
                        jrny_band_changes = VALUES(jrny_band_changes),
                        jrny_dept_changes = VALUES(jrny_dept_changes),
                        jrny_division_changes = VALUES(jrny_division_changes),
                        jrny_location_changes = VALUES(jrny_location_changes),
                        jrny_manager_changes = VALUES(jrny_manager_changes),
                        jrny_resource_group_changes = VALUES(jrny_resource_group_changes),
                        jrny_role_changes = VALUES(jrny_role_changes),
                        jrny_unit_changes = VALUES(jrny_unit_changes),
                        jrny_months_since_last_promotion = VALUES(jrny_months_since_last_promotion),
                        jrny_months_since_last_rotation = VALUES(jrny_months_since_last_rotation),
                        jrny_months_since_last_manager_change = VALUES(jrny_months_since_last_manager_change),
                        jrny_last_movement_months = VALUES(jrny_last_movement_months),
                        jrny_time_to_first_promotion = VALUES(jrny_time_to_first_promotion),
                        jrny_bands_held = VALUES(jrny_bands_held),
                        jrny_departments_visited = VALUES(jrny_departments_visited),
                        jrny_roles_held = VALUES(jrny_roles_held),
                        jrny_is_stagnant = VALUES(jrny_is_stagnant),
                        jrny_no_rotation_2yr = VALUES(jrny_no_rotation_2yr),
                        jrny_no_promotion_2yr = VALUES(jrny_no_promotion_2yr),
                        jrny_recently_moved = VALUES(jrny_recently_moved),
                        jrny_frozen_at_band = VALUES(jrny_frozen_at_band),
                        computed_at = VALUES(computed_at)
                """),
                features,
            )
            processed += 1

        except Exception as e:
            logger.error("Journey features failed for %s: %s", emp_id, e)
            errors += 1

    db.commit()
    result = {"processed": processed, "errors": errors, "total": len(active_ids)}
    logger.info("Journey features job complete: %s", result)
    return result


# ── Standalone runner ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        result = run_journey_features_job(db)
        print(f"\nDone: {result}")
    finally:
        db.close()