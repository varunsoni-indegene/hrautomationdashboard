"""
tests/test_health.py
---------------------
Basic smoke tests. Run with:   pytest tests/ -v
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock


# Patch DB and security so tests don't need a real MySQL connection
@pytest.fixture
def client():
    with patch("app.core.database.check_db_connection", return_value=True):
        from app.main import app
        with TestClient(app) as c:
            yield c


def test_health_endpoint(client):
    """Health check should always return 200."""
    with patch("app.main.check_db_connection", return_value=True):
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_team_summary_requires_auth(client):
    """Data endpoints must reject requests without a token."""
    resp = client.get("/api/v1/team/summary")
    assert resp.status_code in (401, 403)


def test_team_members_requires_auth(client):
    resp = client.get("/api/v1/team/members")
    assert resp.status_code in (401, 403)


def test_auth_me_requires_auth(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)