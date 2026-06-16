"""
Takes a point-in-time snapshot of all employees from employee_details
and inserts it into employee_snapshots for the current month.

Run this:
  • ONCE NOW to backfill the current state as your first snapshot.
  • Then AUTOMATICALLY on the 1st of every month (Azure Function or cron job).

Usage:
    python scripts/take_snapshot.py
    python scripts/take_snapshot.py --month 2025-06-01   # specific month
"""

import sys
import os
import argparse
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from sqlalchemy import text
from app.core.database import SessionLocal, check_db_connection


def take_snapshot(snapshot_month: date):
    print(f"\nTaking employee snapshot for month: {snapshot_month}")
    check_db_connection()
    db: Session = SessionLocal()

    try:
        # Read all employees from employee_details
        rows = db.execute(
            text("""
                SELECT emp_id, emp_name, email_id, employee_status,
                       band_level, band, level, department, division, unit,
                       resource_group, designation, role, work_location,
                       rm_empcode, reporting_manager, resignation_type,
                       dateofjoining, do_relieving, entity
                FROM employee_details
                WHERE emp_id IS NOT NULL AND TRIM(emp_id) != ''
            """)
        ).fetchall()

        print(f"  Found {len(rows)} employees.")

        inserted = updated = 0
        for row in rows:
            db.execute(
                text("""
                    INSERT INTO employee_snapshots (
                        snapshot_month, emp_id, emp_name, email_id, employee_status,
                        band_level, band, level, department, division, unit,
                        resource_group, designation, role, work_location,
                        rm_empcode, reporting_manager, resignation_type,
                        dateofjoining, do_relieving, entity
                    ) VALUES (
                        :month, :emp_id, :emp_name, :email_id, :status,
                        :band_level, :band, :level, :department, :division, :unit,
                        :resource_group, :designation, :role, :work_location,
                        :rm_empcode, :reporting_manager, :resignation_type,
                        :dateofjoining, :do_relieving, :entity
                    )
                    ON DUPLICATE KEY UPDATE
                        emp_name          = VALUES(emp_name),
                        email_id          = VALUES(email_id),
                        employee_status   = VALUES(employee_status),
                        band_level        = VALUES(band_level),
                        band              = VALUES(band),
                        level             = VALUES(level),
                        department        = VALUES(department),
                        division          = VALUES(division),
                        unit              = VALUES(unit),
                        resource_group    = VALUES(resource_group),
                        designation       = VALUES(designation),
                        role              = VALUES(role),
                        work_location     = VALUES(work_location),
                        rm_empcode        = VALUES(rm_empcode),
                        reporting_manager = VALUES(reporting_manager),
                        resignation_type  = VALUES(resignation_type),
                        do_relieving      = VALUES(do_relieving),
                        entity            = VALUES(entity)
                """),
                {
                    "month": snapshot_month,
                    "emp_id": row[0], "emp_name": row[1], "email_id": row[2],
                    "status": row[3], "band_level": row[4], "band": row[5],
                    "level": row[6], "department": row[7], "division": row[8],
                    "unit": row[9], "resource_group": row[10], "designation": row[11],
                    "role": row[12], "work_location": row[13], "rm_empcode": row[14],
                    "reporting_manager": row[15], "resignation_type": row[16],
                    "dateofjoining": row[17], "do_relieving": row[18], "entity": row[19],
                }
            )
            inserted += 1

        db.commit()
        print(f"  ✓ Snapshot complete: {inserted} rows written for {snapshot_month}.")

    except Exception as e:
        db.rollback()
        print(f"  ✗ Snapshot failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--month",
        default=None,
        help="Snapshot month as YYYY-MM-DD (defaults to first day of current month)"
    )
    args = parser.parse_args()

    if args.month:
        from datetime import datetime
        snapshot_month = datetime.strptime(args.month, "%Y-%m-%d").date()
    else:
        today = date.today()
        snapshot_month = date(today.year, today.month, 1)

    take_snapshot(snapshot_month)