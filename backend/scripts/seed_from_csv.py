"""
Loads a CSV file (your test.csv or a full export) into employee_details.
Use this ONLY in development to populate the DB without a live HR system.

Usage:
    python scripts/seed_from_csv.py --file test.csv
    python scripts/seed_from_csv.py --file test.csv --truncate   # clear table first
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal, check_db_connection
from app.models.tables import EmployeeDetails


def clean_value(val):
    """Convert NaN, 'NULL', 'null', 'None' → Python None; strip whitespace."""
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    s = str(val).strip()
    if s.lower() in ("nan", "null", "none", "", "-"):
        return None
    return s


def seed(filepath: str, truncate: bool = False):
    print(f"\nSeeding employee_details from: {filepath}")

    check_db_connection()
    df = pd.read_csv(filepath, dtype=str)
    print(f"  Loaded {len(df)} rows, {len(df.columns)} columns from CSV.")

    # Map CSV column names to model attribute names.
    # All column names are identical between CSV and DB in your case.
    col_names = [c.name for c in EmployeeDetails.__table__.columns]

    db: Session = SessionLocal()
    try:
        if truncate:
            print("  Truncating employee_details table...")
            db.query(EmployeeDetails).delete()
            db.commit()

        inserted = 0
        skipped = 0
        for _, row in df.iterrows():
            emp_id = clean_value(row.get("emp_id"))
            if not emp_id:
                skipped += 1
                continue

            # Check if row already exists
            existing = db.query(EmployeeDetails).filter_by(emp_id=emp_id).first()
            if existing and not truncate:
                skipped += 1
                continue

            kwargs = {}
            for col in col_names:
                if col in df.columns:
                    kwargs[col] = clean_value(row.get(col))

            obj = EmployeeDetails(**kwargs)
            db.merge(obj)   # INSERT or UPDATE based on primary key
            inserted += 1

        db.commit()
        print(f"  ✓ Inserted/updated {inserted} rows. Skipped {skipped}.")

    except Exception as e:
        db.rollback()
        print(f"  ✗ Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Path to CSV file")
    parser.add_argument("--truncate", action="store_true",
                        help="Clear the table before inserting")
    args = parser.parse_args()
    seed(args.file, args.truncate)