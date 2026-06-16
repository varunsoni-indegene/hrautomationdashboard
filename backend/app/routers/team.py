"""
Team-level endpoints. Every endpoint:
  1. Validates the Bearer token (via get_current_user_email dependency).
  2. Resolves the manager's emp_id from their email.
  3. Fetches only THAT manager's team data.
  4. Applies any requested filters.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.core.security import get_current_user_email
from app.services.manager_service import (
    get_manager_emp_id_by_email,
    get_team_emp_ids,
    get_manager_context,
    assert_employee_in_team,
)
from app.schemas.responses import (
    TeamSummaryResponse,
    TeamMembersResponse,
    TeamMember,
    ManagerContext,
)

router = APIRouter(prefix="/team", tags=["Team"])


def _parse_tenure_months(dateofjoining: Optional[str]) -> Optional[float]:
    """Parse dd-Mon-yy or dd-Mon-yyyy → tenure in months from today."""
    if not dateofjoining:
        return None
    from dateutil import parser as dateparser
    from datetime import date
    try:
        doj = dateparser.parse(dateofjoining, dayfirst=True).date()
        today = date.today()
        return round((today - doj).days / 30.44, 1)
    except Exception:
        return None


def _tenure_band(months: Optional[float]) -> Optional[str]:
    if months is None:
        return None
    if months < 3:
        return "0-3m"
    if months < 6:
        return "3-6m"
    if months < 12:
        return "6-12m"
    if months < 24:
        return "12-24m"
    if months < 36:
        return "24-36m"
    if months < 60:
        return "36-60m"
    return "60m+"


@router.get("/summary", response_model=TeamSummaryResponse)
def get_team_summary(
    email: str = Depends(get_current_user_email),
    db: Session = Depends(get_db),
):
    """
    Team dashboard KPIs: team size, TTM attrition %, headcount at-risk counts.
    Reads from the pre-computed team_analytics table (populated every Sunday).
    Falls back to live computation if the table is empty.
    """
    mgr_ctx = get_manager_context(email, db)
    if not mgr_ctx["is_manager"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active team found for this account.",
        )

    manager_emp_id = mgr_ctx["emp_id"]

    # Try pre-computed table first
    row = db.execute(
        text("""
            SELECT rm_empcode, manager_name, period_date, team_size, active_count,
                   left_count_ttm, ttm_attrition_pct, ttm_vol_attrition_pct,
                   ttm_invol_attrition_pct, avg_tenure_months, infant_count,
                   infant_attrition_pct, stagnant_count, high_risk_count,
                   medium_risk_count, low_risk_count, avg_risk_score
            FROM team_analytics
            WHERE rm_empcode = :mgr_id
            ORDER BY period_date DESC
            LIMIT 1
        """),
        {"mgr_id": manager_emp_id}
    ).fetchone()

    if row:
        from app.schemas.responses import TeamSummary
        summary = TeamSummary(
            rm_empcode=row[0], manager_name=row[1], period_date=row[2],
            team_size=row[3] or 0, active_count=row[4] or 0,
            left_count_ttm=row[5] or 0, ttm_attrition_pct=row[6] or 0.0,
            ttm_vol_attrition_pct=row[7] or 0.0, ttm_invol_attrition_pct=row[8] or 0.0,
            avg_tenure_months=row[9] or 0.0, infant_count=row[10] or 0,
            infant_attrition_pct=row[11] or 0.0, stagnant_count=row[12] or 0,
            high_risk_count=row[13] or 0, medium_risk_count=row[14] or 0,
            low_risk_count=row[15] or 0, avg_risk_score=row[16] or 0.0,
        )
    else:
        # Fallback: compute live from employee_details
        summary = _compute_summary_live(manager_emp_id, db)

    return TeamSummaryResponse(
        manager=ManagerContext(**mgr_ctx),
        summary=summary,
    )


@router.get("/members", response_model=TeamMembersResponse)
def get_team_members(
    department: Optional[str] = Query(None, description="Filter by department name"),
    unit: Optional[str] = Query(None, description="Filter by unit"),
    band_level: Optional[str] = Query(None, description="Filter by band level e.g. A1, B2"),
    risk_category: Optional[str] = Query(None, description="High | Medium | Low"),
    tenure_band: Optional[str] = Query(None, description="e.g. 12-24m"),
    is_stagnant: Optional[bool] = Query(None, description="Filter stagnant employees"),
    search: Optional[str] = Query(None, description="Search by name"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    email: str = Depends(get_current_user_email),
    db: Session = Depends(get_db),
):
    """
    Paginated list of team members with filters.
    All filters are applied in SQL — no data leaks to frontend.
    """
    mgr_ctx = get_manager_context(email, db)
    if not mgr_ctx["is_manager"]:
        raise HTTPException(status_code=403, detail="No active team found.")

    manager_emp_id = mgr_ctx["emp_id"]

    # Build dynamic WHERE clause
    where_clauses = [
        "e.rm_empcode = :manager_id",
        "e.employee_status IN ('Active', 'Serving Notice')",
    ]
    params: dict = {"manager_id": manager_emp_id}

    if department:
        where_clauses.append("e.department = :department")
        params["department"] = department
    if unit:
        where_clauses.append("e.unit = :unit")
        params["unit"] = unit
    if band_level:
        where_clauses.append("e.band_level = :band_level")
        params["band_level"] = band_level
    if search:
        where_clauses.append("e.emp_name LIKE :search")
        params["search"] = f"%{search}%"
    if risk_category:
        where_clauses.append("ers.risk_category = :risk_category")
        params["risk_category"] = risk_category
    if is_stagnant is not None:
        where_clauses.append("ejf.jrny_is_stagnant = :stagnant")
        params["stagnant"] = 1 if is_stagnant else 0

    where_sql = " AND ".join(where_clauses)
    offset = (page - 1) * page_size

    # Count query
    count_sql = f"""
        SELECT COUNT(*)
        FROM employee_details e
        LEFT JOIN employee_risk_scores ers
            ON e.emp_id = ers.emp_id
            AND ers.scored_date = (SELECT MAX(scored_date) FROM employee_risk_scores)
        LEFT JOIN employee_journey_features ejf ON e.emp_id = ejf.emp_id
        WHERE {where_sql}
    """
    total = db.execute(text(count_sql), params).scalar() or 0

    # Main query
    main_sql = f"""
        SELECT
            e.emp_id, e.emp_name, e.designation, e.band_level,
            e.department, e.unit, e.division, e.resource_group,
            e.work_location, e.employee_status, e.dateofjoining,
            ers.attrition_risk_score, ers.risk_category, ers.risk_band,
            ejf.jrny_is_stagnant, ejf.jrny_months_since_last_promotion,
            ejf.jrny_promotions
        FROM employee_details e
        LEFT JOIN employee_risk_scores ers
            ON e.emp_id = ers.emp_id
            AND ers.scored_date = (SELECT MAX(scored_date) FROM employee_risk_scores)
        LEFT JOIN employee_journey_features ejf ON e.emp_id = ejf.emp_id
        WHERE {where_sql}
        ORDER BY e.emp_name
        LIMIT :limit OFFSET :offset
    """
    params["limit"] = page_size
    params["offset"] = offset

    rows = db.execute(text(main_sql), params).fetchall()

    members = []
    for r in rows:
        tenure_m = _parse_tenure_months(r[10])
        tband = _tenure_band(tenure_m)
        # Apply tenure_band filter in Python (after computing) if requested
        if tenure_band and tband != tenure_band:
            continue
        members.append(TeamMember(
            emp_id=r[0], emp_name=r[1], designation=r[2], band_level=r[3],
            department=r[4], unit=r[5], division=r[6], resource_group=r[7],
            work_location=r[8], employee_status=r[9], dateofjoining=r[10],
            tenure_months=tenure_m, tenure_band=tband,
            risk_score=r[11], risk_category=r[12], risk_band=r[13],
            is_stagnant=bool(r[14]) if r[14] is not None else None,
            months_since_promotion=r[15], promotions_total=r[16],
        ))

    # Fetch available filter values for this manager's team (for dropdowns)
    filter_rows = db.execute(
        text("""
            SELECT DISTINCT department, unit, band_level
            FROM employee_details
            WHERE rm_empcode = :mgr AND employee_status IN ('Active', 'Serving Notice')
        """),
        {"mgr": manager_emp_id}
    ).fetchall()
    depts = sorted({r[0] for r in filter_rows if r[0]})
    units = sorted({r[1] for r in filter_rows if r[1]})
    bands = sorted({r[2] for r in filter_rows if r[2]})

    return TeamMembersResponse(
        manager=ManagerContext(**mgr_ctx),
        total=total,
        page=page,
        page_size=page_size,
        members=members,
        available_filters={
            "departments": depts,
            "units": units,
            "band_levels": bands,
            "risk_categories": ["High", "Medium", "Low"],
            "tenure_bands": ["0-3m", "3-6m", "6-12m", "12-24m", "24-36m", "36-60m", "60m+"],
        },
    )


def _compute_summary_live(manager_emp_id: str, db: Session):
    """
    Fallback: compute team summary directly from employee_details.
    Used when team_analytics hasn't been populated yet.
    """
    from app.schemas.responses import TeamSummary
    from datetime import date

    rows = db.execute(
        text("""
            SELECT emp_id, emp_name, employee_status, dateofjoining,
                   do_relieving, resignation_type
            FROM employee_details
            WHERE rm_empcode = :mgr
        """),
        {"mgr": manager_emp_id}
    ).fetchall()

    today = date.today()
    active = [r for r in rows if r[2] in ("Active", "Serving Notice")]
    tenures = [_parse_tenure_months(r[3]) for r in active]
    tenures = [t for t in tenures if t is not None]
    avg_tenure = sum(tenures) / len(tenures) if tenures else 0.0
    infant_count = sum(1 for t in tenures if t < 12)

    return TeamSummary(
        rm_empcode=manager_emp_id,
        manager_name=None,
        period_date=today,
        team_size=len(active),
        active_count=len(active),
        left_count_ttm=0,
        ttm_attrition_pct=0.0,
        ttm_vol_attrition_pct=0.0,
        ttm_invol_attrition_pct=0.0,
        avg_tenure_months=round(avg_tenure, 1),
        infant_count=infant_count,
        infant_attrition_pct=0.0,
        stagnant_count=0,
        high_risk_count=0,
        medium_risk_count=0,
        low_risk_count=0,
        avg_risk_score=0.0,
    )