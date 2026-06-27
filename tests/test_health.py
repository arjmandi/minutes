"""App-boot smoke: lifespan wires datastores and probes respond.

``TestClient`` as a context manager runs the lifespan (engine + redis). ``/readyz`` actually
touches Postgres (SELECT 1) and Redis (ping); it is skipped if those aren't up.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_healthz():
    with TestClient(app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_readyz():
    with TestClient(app) as client:
        resp = client.get("/readyz")
        body = resp.json()
        if resp.status_code != 200:
            pytest.skip(f"datastores not ready: {body}")
        assert body["ready"] is True
        assert body["checks"]["db"] == "ok"
        assert body["checks"]["redis"] == "ok"
