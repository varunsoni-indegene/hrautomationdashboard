"""
Pydantic models that define exactly what the API returns.
The Angular frontend types are generated from these.
"""

from datetime import date
from typing import Optional
from pydantic import BaseModel, Field


# ── Auth / manager context ────────────────────────────────────────────────────
class ManagerContext(BaseModel):
    emp_id: Optional[str] = None
    name: Optional[str] = None
    email: str
    team_size: int
    is_manager: bool


class AuthMeResponse(BaseModel):
    manager: ManagerContext
    message: str


# ── Team member (list view) ───────────────────────────────────────────────────
class TeamMember(BaseModel):
    emp_id: str
    emp_name: Optional[str] = None
    designation: Optional[str] = None
    band_level: Optional[str] = None
    department: Optional[str] = None
    unit: Optional[str] = None
    division: Optional[str] = None
    resource_group: Optional[str] = None
    work_location: Optional[str] = None
    employee_status: Optional[str] = None
    dateofjoining: Optional[str] = None
    tenure_months: Optional[float] = None
    tenure_band: Optional[str] = None
    # Risk fields (from employee_risk_scores)
    risk_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    risk_category: Optional[str] = None   # 'High' | 'Medium' | 'Low'
    risk_band: Optional[str] = None       # 'Infant (<3m)' | 'Tenure Cliff' etc.
    # Journey fields (from employee_journey_features)
    is_stagnant: Optional[bool] = None
    months_since_promotion: Optional[float] = None
    promotions_total: Optional[int] = None


class TeamMembersResponse(BaseModel):
    manager: ManagerContext
    total: int
    page: int
    page_size: int
    members: list[TeamMember]
    # Filter options (for dropdown population in frontend)
    available_filters: Optional[dict] = None


# ── Team summary (dashboard KPIs) ────────────────────────────────────────────
class TeamSummary(BaseModel):
    rm_empcode: str
    manager_name: Optional[str] = None
    period_date: Optional[date] = None
    team_size: int
    active_count: int
    left_count_ttm: int
    ttm_attrition_pct: float
    ttm_vol_attrition_pct: float
    ttm_invol_attrition_pct: float
    avg_tenure_months: float
    infant_count: int
    infant_attrition_pct: float
    stagnant_count: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    avg_risk_score: float


class TeamSummaryResponse(BaseModel):
    manager: ManagerContext
    summary: TeamSummary


# ── Individual employee detail ────────────────────────────────────────────────
class EmployeeDetail(BaseModel):
    emp_id: str
    emp_name: Optional[str] = None
    email_id: Optional[str] = None
    designation: Optional[str] = None
    band_level: Optional[str] = None
    department: Optional[str] = None
    unit: Optional[str] = None
    division: Optional[str] = None
    resource_group: Optional[str] = None
    work_location: Optional[str] = None
    entity: Optional[str] = None
    gender: Optional[str] = None
    employee_status: Optional[str] = None
    dateofjoining: Optional[str] = None
    do_resignation: Optional[str] = None
    do_relieving: Optional[str] = None
    resignation_type: Optional[str] = None
    indegene_exp: Optional[str] = None
    total_exp: Optional[str] = None
    # Computed
    tenure_months: Optional[float] = None
    tenure_band: Optional[str] = None
    # Risk
    risk_score: Optional[float] = None
    risk_category: Optional[str] = None
    risk_band: Optional[str] = None
    top_risk_factor_1: Optional[str] = None
    top_risk_factor_2: Optional[str] = None
    top_risk_factor_3: Optional[str] = None
    # Journey
    is_stagnant: Optional[bool] = None
    months_since_promotion: Optional[float] = None
    months_since_rotation: Optional[float] = None
    promotions_total: Optional[int] = None
    lateral_moves_total: Optional[int] = None
    jrny_manager_changes: Optional[int] = None
    jrny_dept_changes: Optional[int] = None


class EmployeeDetailResponse(BaseModel):
    manager: ManagerContext
    employee: EmployeeDetail


# ── At-risk register ──────────────────────────────────────────────────────────
class AtRiskEmployee(BaseModel):
    emp_id: str
    emp_name: Optional[str] = None
    designation: Optional[str] = None
    band_level: Optional[str] = None
    department: Optional[str] = None
    tenure_months: Optional[float] = None
    risk_score: float
    risk_category: str
    risk_band: Optional[str] = None
    top_risk_factor_1: Optional[str] = None
    top_risk_factor_2: Optional[str] = None


class AtRiskResponse(BaseModel):
    manager: ManagerContext
    total_at_risk: int
    high_risk: int
    medium_risk: int
    employees: list[AtRiskEmployee]


# ── Attrition trend (quarterly) ───────────────────────────────────────────────
class AttritionTrendPoint(BaseModel):
    quarter: str              # e.g. "Q1 FY25"
    ttm_attrition_pct: float
    vol_attrition_pct: float
    headcount: int


class AttritionTrendResponse(BaseModel):
    manager: ManagerContext
    trend: list[AttritionTrendPoint]


# ── Generic error ─────────────────────────────────────────────────────────────
class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None