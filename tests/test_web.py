"""The web SPA is served as a shell (/, /app, /shared/{token}) plus static assets under /assets.

Pure file serving (no datastores needed) — guards the StaticFiles mount + the share-link route the
public viewer depends on.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_spa_shell_and_assets_served():
    with TestClient(app) as c:
        for path in ("/", "/app", "/shared/anytoken"):
            r = c.get(path)
            assert r.status_code == 200
            assert "text/html" in r.headers["content-type"]
            assert "/assets/app.js" in r.text  # shell loads the SPA bundle

        js = c.get("/assets/app.js")
        assert js.status_code == 200 and "javascript" in js.headers["content-type"]
        for css in ("/assets/fs-styles.css", "/assets/app-kit.css"):
            assert c.get(css).status_code == 200
