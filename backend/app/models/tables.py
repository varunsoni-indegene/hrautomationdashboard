"""
All SQLAlchemy ORM table definitions.

Tables:
  EXISTING (your current MySQL table):
    employee_details          - the master employee table you already have

  NEW (created by scripts/run_migrations.py):
    employee_snapshots        - monthly point-in-time copy of each employee's attributes
    employee_journey_features - JRNY_* features computed from snapshots
    employee_risk_scores      - ML attrition risk score per employee (weekly)
    team_analytics            - pre-computed team KPIs per manager (weekly)
    employee_analytics        - pre-computed per-employee metrics (weekly)
    ml_models                 - serialised Logistic Regression + calibrator (monthly)
"""

from datetime import datetime, date
from sqlalchemy import (
    Column, String, Integer, Float, Text, Date, DateTime,
    SmallInteger, LargeBinary, JSON, Index, UniqueConstraint
)
from app.core.database import Base

# EXISTING TABLE  (mirrors current employee_details exactly)

class EmployeeDetails(Base):
    __tablename__ = "employee_details"
    # We need at least one primary key for SQLAlchemy. emp_id is not declared
    # PK in your DDL, so we use a composite surrogate. In practice, use emp_id
    # as the logical identifier everywhere in application code.
    emp_id                          = Column(String(135), primary_key=True)
    user_id                         = Column(String(150))
    emp_name                        = Column(String(300))
    rm_check_role                   = Column(String(135))
    dateofjoining                   = Column(String(150))   # stored as dd-Mon-yy string
    gender                          = Column(String(135))
    designation                     = Column(String(300))
    band                            = Column(String(135))
    level                           = Column(String(33))
    band_level                      = Column(String(135))
    division                        = Column(String(255))
    unit                            = Column(String(255))
    department                      = Column(String(255))
    resource_group                  = Column(String(255))
    resource_type                   = Column(String(255))
    acc_reg_proj                    = Column(Text)
    skill                           = Column(Text)          # mediumtext in MySQL
    work_location                   = Column(String(255))
    work_unit                       = Column(String(255))
    email_id                        = Column(String(255))   # employee's own email
    employee_status                 = Column(String(135))   # 'Active', 'Left', 'Serving Notice'
    reporting_change_check          = Column(String(135))
    do_resignation                  = Column(String(135))   # date string dd-Mon-yy
    do_relieving                    = Column(String(135))   # date string dd-Mon-yy
    settlement_status               = Column(Text)
    employer_reason_forleaving      = Column(Text)
    resignation_type                = Column(Text)          # 'Voluntary', 'Involuntary', etc.
    employee_reason_forleaving      = Column(Text)
    type                            = Column(Text)
    role                            = Column(String(300))
    reporting_manager               = Column(String(300))   # manager's name
    rm_empcode                      = Column(String(135))   # manager's emp_id — USE THIS for lookup
    reviewing_manager               = Column(String(300))
    rvm_empcode                     = Column(String(135))
    bu_lead                         = Column(String(300))
    bu_lead_id                      = Column(String(135))
    bu_head                         = Column(String(300))
    bu_head_id                      = Column(String(135))
    position_status                 = Column(String(300))
    source                          = Column(Text)
    recruiter                       = Column(String(255))
    referred_by_empcode             = Column(String(135))
    referred_by_empname             = Column(String(255))
    referral_amount_eligible        = Column(String(135))
    payment_remarks                 = Column(Text)
    period_of_payment               = Column(Text)
    job_code                        = Column(Text)
    edu_code_series                 = Column(Text)
    graduation                      = Column(Text)
    post_graduation1                = Column(Text)
    post_graduation1_specialization = Column(Text)
    post_graduation1_university_details = Column(Text)
    post_graduation2                = Column(Text)
    post_graduation2_specialization = Column(Text)
    post_graduation2_university_details = Column(Text)
    diploma                         = Column(Text)
    extra_certifications_or_diplomas = Column(Text)
    psycometric_profile             = Column(Text)
    last_company_worked             = Column(Text)
    dob                             = Column(String(135))
    as_on                           = Column(Text)
    age                             = Column(String(135))
    age_code_seried                 = Column(Text)
    years_of_exp                    = Column(Text)
    indegene_exp                    = Column(String(150))   # tenure at this org (years as string)
    total_exp                       = Column(String(150))
    ind_exp_code_series             = Column(String(150))
    overexp_code_series             = Column(String(150))
    mobile_number                   = Column(String(255))
    father_name                     = Column(String(300))
    finance_code                    = Column(Text)
    actual_doj                      = Column(String(135))
    campus_batch                    = Column(String(135))
    entity                          = Column(String(255))
    shift_details                   = Column(String(300))
    effectivedate                   = Column(String(255))
    status                          = Column(String(135))
    created_by                      = Column(String(255))
    created_date                    = Column(String(300))
    modified_by                     = Column(String(255))
    modified_date                   = Column(String(300))
    contract_end_date               = Column(String(150))
    extended_region                 = Column(Text)
    extended_employee_cc_id         = Column(Text)
    extended_emp_base_location      = Column(Text)
    extended_city_category          = Column(Text)
    extended_pin_code               = Column(Text)
    extended_entity                 = Column(Text)
    extended_emp_email_group        = Column(Text)
    extended_appointment_letter     = Column(Text)
    extended_nda                    = Column(Text)
    extended_bgv                    = Column(Text)
    extended_aadhar_number          = Column(Text)
    extended_permanent_address      = Column(Text)
    extended_present_address        = Column(Text)
    extended_permanent_address_pin_code = Column(Text)
    extended_remarks                = Column(Text)
    extended_currency               = Column(Text)
    extended_nationality            = Column(Text)
    extended_sub_department         = Column(Text)
    extended_hrbp_name              = Column(Text)
    extended_position_code          = Column(Text)
    extended_sub_department_code    = Column(String(300))
    extended_alternate_num          = Column(Text)
    extended_nameasperaadhaar       = Column(Text)
    extended_employee_reasons       = Column(Text)
    extended_hrbp_id                = Column(Text)
    extended_finance_lead           = Column(Text)
    extended_finance_leadid         = Column(Text)


# ─────────────────────────────────────────────────────────────────────────────
# NEW TABLE: employee_snapshots
# Monthly point-in-time record of each employee's attributes.
# The job `jobs/journey_features.py` reads this to compute JRNY_* features.
# ─────────────────────────────────────────────────────────────────────────────
class EmployeeSnapshot(Base):
    __tablename__ = "employee_snapshots"
    __table_args__ = (
        UniqueConstraint("snapshot_month", "emp_id", name="uq_snapshot_emp"),
        Index("idx_snap_emp_id", "emp_id"),
        Index("idx_snap_month", "snapshot_month"),
    )

    id               = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_month   = Column(Date, nullable=False)    # first day of the month: 2024-01-01
    emp_id           = Column(String(135), nullable=False)
    emp_name         = Column(String(300))
    email_id         = Column(String(255))
    employee_status  = Column(String(135))             # Active / Left / Serving Notice
    band_level       = Column(String(135))             # A1, A2, B1, B2, C1 ...
    band             = Column(String(135))
    level            = Column(String(33))
    department       = Column(String(255))
    division         = Column(String(255))
    unit             = Column(String(255))
    resource_group   = Column(String(255))
    designation      = Column(String(300))
    role             = Column(String(300))
    work_location    = Column(String(255))
    rm_empcode       = Column(String(135))             # manager emp_id at this point in time
    reporting_manager = Column(String(300))
    resignation_type = Column(Text)
    dateofjoining    = Column(String(150))
    do_relieving     = Column(String(135))
    entity           = Column(String(255))
    created_at       = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# NEW TABLE: employee_journey_features
# Computed by jobs/journey_features.py on the 2nd of every month.
# One row per active employee — upserted on each run.
# ─────────────────────────────────────────────────────────────────────────────
class EmployeeJourneyFeatures(Base):
    __tablename__ = "employee_journey_features"

    emp_id                              = Column(String(135), primary_key=True)
    jrny_promotions                     = Column(Integer, default=0)
    jrny_lateral_moves                  = Column(Integer, default=0)
    jrny_band_changes                   = Column(Integer, default=0)
    jrny_dept_changes                   = Column(Integer, default=0)
    jrny_division_changes               = Column(Integer, default=0)
    jrny_location_changes               = Column(Integer, default=0)
    jrny_manager_changes                = Column(Integer, default=0)
    jrny_resource_group_changes         = Column(Integer, default=0)
    jrny_role_changes                   = Column(Integer, default=0)
    jrny_unit_changes                   = Column(Integer, default=0)
    jrny_months_since_last_promotion    = Column(Float)
    jrny_months_since_last_rotation     = Column(Float)   # min(dept change, rg change)
    jrny_months_since_last_manager_change = Column(Float)
    jrny_last_movement_months           = Column(Float)   # min(promotion, rotation)
    jrny_time_to_first_promotion        = Column(Float)   # months from DOJ to first promotion
    jrny_bands_held                     = Column(Integer, default=1)
    jrny_departments_visited            = Column(Integer, default=1)
    jrny_roles_held                     = Column(Integer, default=1)
    jrny_is_stagnant                    = Column(SmallInteger, default=0)  # 0 or 1
    jrny_no_rotation_2yr                = Column(SmallInteger, default=0)
    jrny_no_promotion_2yr               = Column(SmallInteger, default=0)
    jrny_recently_moved                 = Column(SmallInteger, default=0)  # moved in last 12m
    jrny_frozen_at_band                 = Column(SmallInteger, default=0)  # 3+ yrs same band
    computed_at                         = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# NEW TABLE: employee_risk_scores
# Written by jobs/scoring.py every week (Sunday night).
# ─────────────────────────────────────────────────────────────────────────────
class EmployeeRiskScore(Base):
    __tablename__ = "employee_risk_scores"
    __table_args__ = (
        UniqueConstraint("emp_id", "scored_date", name="uq_risk_emp_date"),
        Index("idx_risk_emp_id", "emp_id"),
        Index("idx_risk_date", "scored_date"),
    )

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    emp_id              = Column(String(135), nullable=False)
    scored_date         = Column(Date, nullable=False)
    attrition_risk_score = Column(Float)          # 0.0 – 1.0 calibrated probability
    risk_category       = Column(String(20))      # 'High' | 'Medium' | 'Low'
    risk_band           = Column(String(50))      # 'Infant (<3m)' | 'Tenure Cliff (12-24m)' etc.
    top_risk_factor_1   = Column(String(200))
    top_risk_factor_2   = Column(String(200))
    top_risk_factor_3   = Column(String(200))
    model_segment       = Column(String(100))     # which LR model was used: 'All', 'Band:A1-A3' etc.
    model_id            = Column(Integer)         # FK to ml_models.id
    created_at          = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# NEW TABLE: team_analytics
# Pre-computed team KPIs per manager. Written by jobs/team_analytics.py weekly.
# ─────────────────────────────────────────────────────────────────────────────
class TeamAnalytics(Base):
    __tablename__ = "team_analytics"
    __table_args__ = (
        UniqueConstraint("rm_empcode", "period_date", name="uq_team_mgr_period"),
        Index("idx_team_rm", "rm_empcode"),
        Index("idx_team_period", "period_date"),
    )

    id                      = Column(Integer, primary_key=True, autoincrement=True)
    rm_empcode              = Column(String(135), nullable=False)  # manager's emp_id
    manager_name            = Column(String(300))
    period_date             = Column(Date, nullable=False)          # date analytics were computed
    team_size               = Column(Integer, default=0)
    active_count            = Column(Integer, default=0)
    left_count_ttm          = Column(Integer, default=0)           # exits in trailing 12 months
    ttm_attrition_pct       = Column(Float, default=0.0)
    ttm_vol_attrition_pct   = Column(Float, default=0.0)
    ttm_invol_attrition_pct = Column(Float, default=0.0)
    avg_tenure_months       = Column(Float, default=0.0)
    infant_count            = Column(Integer, default=0)           # tenure < 12m
    infant_attrition_pct    = Column(Float, default=0.0)
    stagnant_count          = Column(Integer, default=0)
    high_risk_count         = Column(Integer, default=0)
    medium_risk_count       = Column(Integer, default=0)
    low_risk_count          = Column(Integer, default=0)
    avg_risk_score          = Column(Float, default=0.0)
    created_at              = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# NEW TABLE: employee_analytics
# Per-employee metrics snapshot. Written weekly alongside team_analytics.
# ─────────────────────────────────────────────────────────────────────────────
class EmployeeAnalytics(Base):
    __tablename__ = "employee_analytics"
    __table_args__ = (
        UniqueConstraint("emp_id", "period_date", name="uq_emp_analytics_period"),
        Index("idx_ea_emp_id", "emp_id"),
        Index("idx_ea_period", "period_date"),
        Index("idx_ea_rm", "rm_empcode"),
    )

    id                      = Column(Integer, primary_key=True, autoincrement=True)
    emp_id                  = Column(String(135), nullable=False)
    rm_empcode              = Column(String(135))
    period_date             = Column(Date, nullable=False)
    tenure_months           = Column(Float, default=0.0)
    tenure_band             = Column(String(20))     # '0-3m' | '3-6m' | ... | '60m+'
    is_stagnant             = Column(SmallInteger, default=0)
    months_since_promotion  = Column(Float)
    months_since_rotation   = Column(Float)
    promotions_total        = Column(Integer, default=0)
    lateral_moves_total     = Column(Integer, default=0)
    current_risk_score      = Column(Float)
    current_risk_category   = Column(String(20))
    created_at              = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# NEW TABLE: ml_models
# Stores serialised Logistic Regression models.
# The scoring job always loads the row where is_active = 1.
# ─────────────────────────────────────────────────────────────────────────────
class MLModel(Base):
    __tablename__ = "ml_models"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    segment         = Column(String(100), default="All")   # 'All', 'Band:A1-A3', etc.
    trained_at      = Column(DateTime, default=datetime.utcnow)
    model_object    = Column(LargeBinary)        # joblib.dump() bytes of the LR model
    scaler_object   = Column(LargeBinary)        # StandardScaler bytes
    calibrator_object = Column(LargeBinary)      # IsotonicRegression bytes (can be NULL)
    feature_names   = Column(JSON)               # ordered list of feature column names
    auc             = Column(Float)
    train_n         = Column(Integer)
    train_positives = Column(Integer)
    is_active       = Column(SmallInteger, default=1)  # 1 = this model is used for scoring
    notes           = Column(Text)               # e.g. "auto-retrain 2024-12-01, AUC=0.71"