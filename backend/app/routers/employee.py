"""
app/routers/auth.py  +  app/routers/employee.py  +  app/routers/predictions.py
Combined for brevity — split into separate files in production if needed.
"""

# ──────────────────────────────────────────────────────────────────────────────
# auth.py
# ──────────────────────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.database import get_db
from app.core.security import get_current_user_email
from app.services.manager_service import get_manager_context, assert_employee_in_team, get_manager_emp_id_by_email
from app.schemas.responses import AuthMeResponse, ManagerContext, EmployeeDetailResponse, EmployeeDetail, AtRiskResponse, AtRiskEmployee

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])

@auth_router.get("/me", response_model=AuthMeResponse)
def get_me(
    email: str = Depends(get_current_user_email),
    db: Session = Depends(get_db),
):
    """
    Called by Angular immediately after login.
    Returns the manager's context: their emp_id, name, team size.
    Frontend uses this to know if the user is a valid manager.
    """
    ctx = get_manager_context(email, db)
    return AuthMeResponse(
        manager=ManagerContext(**ctx),
        message="Authenticated successfully." if ctx["is_manager"]
                else "Login successful, but no active team found for this account.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# employee.py
# ──────────────────────────────────────────────────────────────────────────────
from typing import Optional

employee_router = APIRouter(prefix="/employee", tags=["Employee"])

def _parse_tenure_months(dateofjoining: Optional[str]) -> Optional[float]:
    if not dateofjoining:
        return None
    from dateutil import parser as dateparser
    from datetime import date
    try:
        doj = dateparser.parse(dateofjoining, dayfirst=True).date()
        return round((date.today() - doj).days / 30.44, 1)
    except Exception:
        return None

def _tenure_band(months: Optional[float]) -> Optional[str]:
    if months is None: return None
    if months < 3: return "0-3m"
    if months < 6: return "3-6m"
    if months < 12: return "6-12m"
    if months < 24: return "12-24m"
    if months < 36: return "24-36m"
    if months < 60: return "36-60m"
    return "60m+"


@employee_router.get("/{emp_id}", response_model=EmployeeDetailResponse)
def get_employee_detail(
    emp_id: str,
    email: str = Depends(get_current_user_email),
    db: Session = Depends(get_db),
):
    """
    Full profile of a single employee.
    SECURITY: verified that emp_id belongs to the requesting manager's team.
    """
    ctx = get_manager_context(email, db)
    if not ctx["is_manager"]:
        raise HTTPException(status_code=403, detail="No active team found.")

    manager_emp_id = ctx["emp_id"]

    # Security gate: is this employee in the manager's team?
    if not assert_employee_in_team(emp_id, manager_emp_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorised to view this employee's data.",
        )

    # Fetch employee base data
    emp_row = db.execute(
        text("""
            SELECT emp_id, emp_name, email_id, designation, band_level,
                   department, unit, division, resource_group, work_location,
                   entity, gender, employee_status, dateofjoining,
                   do_resignation, do_relieving, resignation_type,
                   indegene_exp, total_exp
            FROM employee_details
            WHERE emp_id = :emp_id
            LIMIT 1
        """),
        {"emp_id": emp_id}
    ).fetchone()

    if not emp_row:
        raise HTTPException(status_code=404, detail="Employee not found.")

    # Fetch latest risk score
    risk_row = db.execute(
        text("""
            SELECT attrition_risk_score, risk_category, risk_band,
                   top_risk_factor_1, top_risk_factor_2, top_risk_factor_3
            FROM employee_risk_scores
            WHERE emp_id = :emp_id
            ORDER BY scored_date DESC
            LIMIT 1
        """),
        {"emp_id": emp_id}
    ).fetchone()

    # Fetch journey features
    jrny_row = db.execute(
        text("""
            SELECT jrny_is_stagnant, jrny_months_since_last_promotion,
                   jrny_months_since_last_rotation, jrny_promotions,
                   jrny_lateral_moves, jrny_manager_changes, jrny_dept_changes
            FROM employee_journey_features
            WHERE emp_id = :emp_id
            LIMIT 1
        """),
        {"emp_id": emp_id}
    ).fetchone()

    tenure_m = _parse_tenure_months(emp_row[13])

    employee = EmployeeDetail(
        emp_id=emp_row[0], emp_name=emp_row[1], email_id=emp_row[2],
        designation=emp_row[3], band_level=emp_row[4], department=emp_row[5],
        unit=emp_row[6], division=emp_row[7], resource_group=emp_row[8],
        work_location=emp_row[9], entity=emp_row[10], gender=emp_row[11],
        employee_status=emp_row[12], dateofjoining=emp_row[13],
        do_resignation=emp_row[14], do_relieving=emp_row[15],
        resignation_type=emp_row[16], indegene_exp=emp_row[17],
        total_exp=emp_row[18],
        tenure_months=tenure_m, tenure_band=_tenure_band(tenure_m),
        # Risk
        risk_score=risk_row[0] if risk_row else None,
        risk_category=risk_row[1] if risk_row else None,
        risk_band=risk_row[2] if risk_row else None,
        top_risk_factor_1=risk_row[3] if risk_row else None,
        top_risk_factor_2=risk_row[4] if risk_row else None,
        top_risk_factor_3=risk_row[5] if risk_row else None,
        # Journey
        is_stagnant=bool(jrny_row[0]) if jrny_row and jrny_row[0] is not None else None,
        months_since_promotion=jrny_row[1] if jrny_row else None,
        months_since_rotation=jrny_row[2] if jrny_row else None,
        promotions_total=jrny_row[3] if jrny_row else None,
        lateral_moves_total=jrny_row[4] if jrny_row else None,
        jrny_manager_changes=jrny_row[5] if jrny_row else None,
        jrny_dept_changes=jrny_row[6] if jrny_row else None,
    )

    return EmployeeDetailResponse(manager=ManagerContext(**ctx), employee=employee)


# ──────────────────────────────────────────────────────────────────────────────
# predictions.py
# ──────────────────────────────────────────────────────────────────────────────
from typing import Optional
from fastapi import Query

predictions_router = APIRouter(prefix="/predictions", tags=["Predictions"])


@predictions_router.get("/at-risk", response_model=AtRiskResponse)
def get_at_risk_employees(
    risk_category: Optional[str] = Query(None, description="High | Medium | All"),
    department: Optional[str] = Query(None),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
    email: str = Depends(get_current_user_email),
    db: Session = Depends(get_db),
):
    """
    At-risk register for the manager's team.
    Returns employees ranked by attrition risk score (highest first).
    """
    ctx = get_manager_context(email, db)
    if not ctx["is_manager"]:
        raise HTTPException(status_code=403, detail="No active team found.")

    manager_emp_id = ctx["emp_id"]

    where_clauses = [
        "e.rm_empcode = :mgr_id",
        "e.employee_status IN ('Active', 'Serving Notice')",
        "ers.attrition_risk_score >= :min_score",
    ]
    params: dict = {"mgr_id": manager_emp_id, "min_score": min_score}

    if risk_category and risk_category != "All":
        where_clauses.append("ers.risk_category = :risk_cat")
        params["risk_cat"] = risk_category
    if department:
        where_clauses.append("e.department = :dept")
        params["dept"] = department

    where_sql = " AND ".join(where_clauses)

    rows = db.execute(
        text(f"""
            SELECT e.emp_id, e.emp_name, e.designation, e.band_level,
                   e.department, e.dateofjoining,
                   ers.attrition_risk_score, ers.risk_category, ers.risk_band,
                   ers.top_risk_factor_1, ers.top_risk_factor_2
            FROM employee_details e
            JOIN employee_risk_scores ers
                ON e.emp_id = ers.emp_id
                AND ers.scored_date = (SELECT MAX(scored_date) FROM employee_risk_scores)
            WHERE {where_sql}
            ORDER BY ers.attrition_risk_score DESC
        """),
        params
    ).fetchall()

    def _tenure(doj):
        t = _parse_tenure_months(doj)
        return t

    employees = [
        AtRiskEmployee(
            emp_id=r[0], emp_name=r[1], designation=r[2], band_level=r[3],
            department=r[4], tenure_months=_tenure(r[5]),
            risk_score=r[6], risk_category=r[7], risk_band=r[8],
            top_risk_factor_1=r[9], top_risk_factor_2=r[10],
        )
        for r in rows
    ]

    high = sum(1 for e in employees if e.risk_category == "High")
    med  = sum(1 for e in employees if e.risk_category == "Medium")

    return AtRiskResponse(
        manager=ManagerContext(**ctx),
        total_at_risk=len(employees),
        high_risk=high,
        medium_risk=med,
        employees=employees,
    )