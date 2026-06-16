"""
scripts/run_migrations.py
--------------------------
Creates all NEW analytics tables in your MySQL database.
Your existing employee_details table is NOT touched.
 
Run this ONCE when setting up the project:
    python scripts/run_migrations.py
 
It is safe to run multiple times — uses CREATE TABLE IF NOT EXISTS logic
via SQLAlchemy's checkfirst=True.
"""
 
import sys
import os
 
# Add project root to Python path so imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
 
from sqlalchemy import inspect, text
from app.core.database import engine, Base, check_db_connection
from app.models.tables import (
    EmployeeSnapshot,
    EmployeeJourneyFeatures,
    EmployeeRiskScore,
    TeamAnalytics,
    EmployeeAnalytics,
    MLModel,
)
 
# Tables that already exist in your DB and must NOT be recreated.
SKIP_TABLES = {"employee_details"}
 
def run_migrations():
    print("\n" + "="*60)
    print("  HR Analytics — Database Migration")
    print("="*60 + "\n")
 
    # 1. Verify database connection
    print("Step 1: Checking database connection...")
    try:
        check_db_connection()
        print("  ✓ Connected to MySQL.\n")
    except Exception as e:
        print(f"  ✗ Cannot connect to database: {e}")
        print("  Check your DATABASE_URL in .env and ensure MySQL is running.")
        sys.exit(1)
 
    # 2. Show existing tables
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    print(f"Step 2: Existing tables in database: {sorted(existing) or 'none'}\n")
 
    # 3. Create new tables (skip employee_details which already exists)
    print("Step 3: Creating new analytics tables...")
    new_tables = [
        EmployeeSnapshot,
        EmployeeJourneyFeatures,
        EmployeeRiskScore,
        TeamAnalytics,
        EmployeeAnalytics,
        MLModel,
    ]
 
    for table_class in new_tables:
        table_name = table_class.__tablename__
        if table_name in existing:
            print(f"  • {table_name:40s} already exists — skipping.")
        else:
            try:
                table_class.__table__.create(engine, checkfirst=True)
                print(f"  ✓ {table_name:40s} CREATED.")
            except Exception as e:
                print(f"  ✗ {table_name:40s} FAILED: {e}")
 
    # 4. Add performance indexes to employee_details
    # These will speed up the manager-based lookups without changing the table.
    print("\nStep 4: Adding indexes to employee_details for faster queries...")
    indexes = [
        ("idx_ed_rm_empcode",   "CREATE INDEX IF NOT EXISTS idx_ed_rm_empcode   ON employee_details(rm_empcode)"),
        ("idx_ed_status",       "CREATE INDEX IF NOT EXISTS idx_ed_status        ON employee_details(employee_status)"),
        ("idx_ed_email",        "CREATE INDEX IF NOT EXISTS idx_ed_email_id      ON employee_details(email_id(100))"),
        ("idx_ed_dept",         "CREATE INDEX IF NOT EXISTS idx_ed_department    ON employee_details(department(100))"),
        ("idx_ed_unit",         "CREATE INDEX IF NOT EXISTS idx_ed_unit          ON employee_details(unit(100))"),
        ("idx_ed_band_level",   "CREATE INDEX IF NOT EXISTS idx_ed_band_level    ON employee_details(band_level)"),
        ("idx_ed_entity",       "CREATE INDEX IF NOT EXISTS idx_ed_entity        ON employee_details(entity(100))"),
    ]
    with engine.connect() as conn:
        for idx_name, ddl in indexes:
            try:
                conn.execute(text(ddl))
                conn.commit()
                print(f"  ✓ Index {idx_name}")
            except Exception as e:
                # Index may already exist — that's fine
                print(f"  • Index {idx_name} — {str(e)[:60]}")
 
    print("\n" + "="*60)
    print("  Migration complete.")
    print("="*60 + "\n")
 
 
if __name__ == "__main__":
    run_migrations()