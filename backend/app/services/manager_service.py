"""
THE access-control layer.

Every API endpoint that returns employee data MUST call get_team_emp_ids()
before running any query. This guarantees a manager can only see their own
team — not other managers' teams.

Key design decisions matching your data:
  - Manager identification is by rm_empcode (manager's emp_id), NOT by name.
  - We also support lookup by email_id when the manager logs in.
  - The hierarchy is: manager's emp_id → employees where rm_empcode = that id.
  - No Manager table exists; managers are just employees who appear in rm_empcode.
"""

import logging
from functools import lru_cache
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Active statuses — employees in these statuses belong to the team
ACTIVE_STATUSES = ("Active", "Serving Notice")


def get_manager_emp_id_by_email(email: str, db: Session) -> str | None:
    """
    Given a manager's login email, return their emp_id.
    This is the first thing called after token validation.

    Why rm_empcode not email for team lookup:
      Your data has 'rm_empcode' as the FK from employee to manager.
      Using emp_id (not email) as the join key is safer because emails
      can change (e.g. name change, company merger) while emp_ids are stable.
    """
    result = db.execute(
        text("""
            SELECT emp_id
            FROM employee_details
            WHERE LOWER(TRIM(email_id)) = LOWER(TRIM(:email))
            LIMIT 1
        """),
        {"email": email}
    ).fetchone()

    if not result:
        logger.warning("No employee found with email: %s", email)
        return None

    return result[0]


def get_team_emp_ids(manager_emp_id: str, db: Session) -> list[str]:
    """
    Return emp_ids of ALL direct reports for a given manager emp_id.
    Only returns Active or Serving Notice employees.

    This is called at the start of every data endpoint.
    An empty list means the user is not a manager of any active team.
    """
    rows = db.execute(
        text("""
            SELECT emp_id
            FROM employee_details
            WHERE rm_empcode = :manager_id
              AND employee_status IN ('Active', 'Serving Notice')
              AND emp_id IS NOT NULL
              AND TRIM(emp_id) != ''
        """),
        {"manager_id": manager_emp_id}
    ).fetchall()

    return [row[0] for row in rows]


def get_manager_context(email: str, db: Session) -> dict:
    """
    Full manager context — emp_id, name, team size — returned in every
    API response so the frontend knows who is logged in.

    Returns:
        {
          "emp_id": "AH00005",
          "name": "Stephen Kimm",
          "email": "stephen.kimm@icon.in",
          "team_size": 3,
          "is_manager": True
        }
    """
    manager_emp_id = get_manager_emp_id_by_email(email, db)

    if not manager_emp_id:
        return {
            "emp_id": None,
            "name": None,
            "email": email,
            "team_size": 0,
            "is_manager": False,
        }

    # Get manager's own details
    mgr = db.execute(
        text("""
            SELECT emp_id, emp_name, email_id
            FROM employee_details
            WHERE emp_id = :emp_id
            LIMIT 1
        """),
        {"emp_id": manager_emp_id}
    ).fetchone()

    team_ids = get_team_emp_ids(manager_emp_id, db)

    return {
        "emp_id": mgr[0] if mgr else manager_emp_id,
        "name": mgr[1] if mgr else None,
        "email": mgr[2] if mgr else email,
        "team_size": len(team_ids),
        "is_manager": len(team_ids) > 0,
    }


def assert_employee_in_team(
    target_emp_id: str,
    manager_emp_id: str,
    db: Session
) -> bool:
    """
    Security check: verify that target_emp_id is a direct report of manager_emp_id.
    Call this before returning any individual employee detail.

    Returns True if authorised. Raises nothing — let the router decide.
    """
    result = db.execute(
        text("""
            SELECT 1
            FROM employee_details
            WHERE emp_id = :emp_id
              AND rm_empcode = :manager_id
            LIMIT 1
        """),
        {"emp_id": target_emp_id, "manager_id": manager_emp_id}
    ).fetchone()
    return result is not None