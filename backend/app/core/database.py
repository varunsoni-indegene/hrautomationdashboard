"""
SQLAlchemy async-compatible engine and session factory.
All routers receive a DB session via FastAPI's Depends(get_db).
"""

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool
from tenacity import retry, stop_after_attempt, wait_fixed
import logging

from app.core.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


# ── Engine ────────────────────────────────────────────────────────────────────
# pool_pre_ping=True:  checks connection health before handing it to a request.
#                      Prevents "MySQL server has gone away" errors on idle connections.
# pool_recycle=1800:   recycle connections every 30 min (MySQL default wait_timeout is 8h,
#                      but Azure MySQL can be lower in some tiers).
# pool_size=10:        10 persistent connections in the pool.
# max_overflow=20:     allow burst up to 30 total connections under load.
engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=10,
    max_overflow=20,
    echo=settings.is_dev,   # logs SQL in development, silent in production
)

# ── Session factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ── Base class for all SQLAlchemy models ──────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── FastAPI dependency ────────────────────────────────────────────────────────
def get_db():
    """
    Yield a database session and guarantee it is closed after the request,
    even if an exception is raised.

    Usage in a router:
        from app.core.database import get_db
        from sqlalchemy.orm import Session

        @router.get("/something")
        def my_endpoint(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Health check helper ───────────────────────────────────────────────────────
@retry(stop=stop_after_attempt(5), wait=wait_fixed(2))
def check_db_connection() -> bool:
    """
    Called at app startup to verify the database is reachable.
    Retries 5 times with 2-second waits before giving up.
    """
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    logger.info("Database connection verified.")
    return True