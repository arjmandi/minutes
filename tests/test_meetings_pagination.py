"""Meeting-list paging + search (GET /api/meetings).

The list used to be a single hard-capped page of 100 with no way forward, so a user with more
history than that simply could not reach it. These tests pin the fix: the whole history is
reachable by following `next_cursor`, the walk is stable while new meetings arrive, and search is
the direct route to one old meeting.

Meetings are seeded straight into the DB (a real capture per meeting would be far too slow at these
counts) with explicit created_at values, so the ordering and its tie-break are exact.
DB + Redis required; skips otherwise.
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory
from app.db.models import Meeting, Platform
from app.main import app

PW = "Sup3r-Secret-Pass!"
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)  # fixed, so seeded rows never collide with real ones


def _seed(email: str, specs: list[tuple[str, str | None, datetime]]) -> None:
    """Create a user and their meetings. `specs` is (external_meeting_id, title, created_at)."""

    async def _run() -> None:
        engine = make_engine(get_settings().database_url)
        factory = make_session_factory(engine)
        try:
            async with factory() as db:
                user = await repo.create_user(db, email=email, password_hash=hash_password(PW))
                db.add_all(
                    Meeting(
                        platform=Platform.web,
                        external_meeting_id=ext,
                        title=title,
                        owner_id=user.id,
                        created_at=created_at,
                    )
                    for ext, title, created_at in specs
                )
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _series(prefix: str, n: int, *, title=lambda i: None) -> list[tuple[str, str | None, datetime]]:
    """n meetings, one per minute — index 0 is the OLDEST, so index n-1 is the newest."""
    return [(f"{prefix}-{i:04d}", title(i), EPOCH + timedelta(minutes=i)) for i in range(n)]


def _login(c: TestClient, email: str) -> None:
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200


def _walk(
    c: TestClient, *, limit: int, q: str | None = None
) -> tuple[list[dict], list[int | None]]:
    """Follow next_cursor to exhaustion. Returns (rows in order, the `total` of each page)."""
    rows: list[dict] = []
    totals: list[int | None] = []
    cursor: str | None = None
    for _ in range(200):  # a guard: a broken cursor must fail the test, not hang it
        params: dict[str, object] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if q is not None:
            params["q"] = q
        page = c.get("/api/meetings", params=params).json()
        rows.extend(page["items"])
        totals.append(page["total"])
        cursor = page["next_cursor"]
        if cursor is None:
            return rows, totals
    raise AssertionError("next_cursor never ran out")


def _ready(c: TestClient) -> None:
    if c.get("/readyz").status_code != 200:
        pytest.skip("datastores not ready")


def test_whole_history_is_reachable_past_the_old_100_cap():
    with TestClient(app) as c:
        _ready(c)
        tag = uuid.uuid4().hex[:8]
        email = f"page-{tag}@test.io"
        _seed(email, _series(f"pg-{tag}", 137))  # > the old hard cap of 100
        _login(c, email)

        rows, totals = _walk(c, limit=25)
        ids = [m["external_meeting_id"] for m in rows]

        assert len(ids) == 137, "paging must reach every meeting, not just the first page"
        assert len(set(ids)) == 137, "a row must not repeat across pages"
        assert ids == sorted(ids, reverse=True), "newest first, throughout the walk"
        assert totals[0] == 137, "the first page reports the full count"
        assert all(t is None for t in totals[1:]), "cursor-ed pages don't recount"


def test_default_page_is_bounded_and_carries_a_cursor():
    with TestClient(app) as c:
        _ready(c)
        tag = uuid.uuid4().hex[:8]
        email = f"dflt-{tag}@test.io"
        _seed(email, _series(f"df-{tag}", 60))
        _login(c, email)

        page = c.get("/api/meetings").json()
        assert len(page["items"]) == 50  # LIST_LIMIT_DEFAULT
        assert page["total"] == 60
        assert page["next_cursor"], "a truncated list must hand back a way forward"

        rest = c.get("/api/meetings", params={"cursor": page["next_cursor"]}).json()
        assert len(rest["items"]) == 10
        assert rest["next_cursor"] is None, "the last page ends the walk"


def test_last_page_never_hands_back_a_dead_cursor():
    """An exactly-full final page must not advertise a next page that turns out to be empty."""
    with TestClient(app) as c:
        _ready(c)
        tag = uuid.uuid4().hex[:8]
        email = f"exact-{tag}@test.io"
        _seed(email, _series(f"ex-{tag}", 20))
        _login(c, email)

        page = c.get("/api/meetings", params={"limit": 20}).json()
        assert len(page["items"]) == 20
        assert page["next_cursor"] is None


def test_walk_is_stable_when_a_meeting_arrives_mid_paging():
    """The reason this is keyset- and not offset-paginated: a capture creates a meeting while the
    user is scrolling. With OFFSET 20 the newcomer shifts the window and page 2 re-serves the row
    the user just read; a keyset cursor is pinned to a position, so it cannot."""
    with TestClient(app) as c:
        _ready(c)
        tag = uuid.uuid4().hex[:8]
        email = f"live-{tag}@test.io"
        _seed(email, _series(f"lv-{tag}", 40))
        _login(c, email)

        first = c.get("/api/meetings", params={"limit": 20}).json()
        page1 = [m["external_meeting_id"] for m in first["items"]]
        assert page1 == [f"lv-{tag}-{i:04d}" for i in range(39, 19, -1)]

        # A meeting the user owns is created between the two page fetches (this is exactly what a
        # live capture's `hello` / a consent claim does) — and it lands at the TOP of the list.
        claimed = c.post(
            "/api/meetings/consent",
            json={"platform": "web", "external_meeting_id": f"lv-{tag}-new", "status": "granted"},
        )
        assert claimed.status_code == 200

        second = c.get("/api/meetings", params={"cursor": first["next_cursor"]}).json()
        page2 = [m["external_meeting_id"] for m in second["items"]]
        assert page2 == [f"lv-{tag}-{i:04d}" for i in range(19, -1, -1)]
        assert set(page1).isdisjoint(page2), "no row may be served twice"
        assert second["next_cursor"] is None


def test_created_at_ties_are_broken_so_no_row_is_lost():
    """Two meetings can share a created_at (bulk import, same-instant upserts). Without the id
    tie-break the keyset would loop or skip one of them."""
    with TestClient(app) as c:
        _ready(c)
        tag = uuid.uuid4().hex[:8]
        email = f"tie-{tag}@test.io"
        same = EPOCH + timedelta(hours=5)
        _seed(
            email,
            [
                (f"tie-{tag}-a", "A", same),
                (f"tie-{tag}-b", "B", same),
                (f"tie-{tag}-c", "C", same),
                (f"tie-{tag}-older", "older", same - timedelta(minutes=1)),
            ],
        )
        _login(c, email)

        rows, _ = _walk(c, limit=1)  # one row per page: every step crosses the tie
        ids = [m["external_meeting_id"] for m in rows]
        assert sorted(ids) == sorted(
            [f"tie-{tag}-a", f"tie-{tag}-b", f"tie-{tag}-c", f"tie-{tag}-older"]
        )
        assert len(ids) == len(set(ids))
        assert ids[-1] == f"tie-{tag}-older", "the tie must not reorder across created_at"


def test_search_reaches_an_old_meeting_directly():
    with TestClient(app) as c:
        _ready(c)
        tag = uuid.uuid4().hex[:8]
        email = f"find-{tag}@test.io"
        specs = _series(f"fd-{tag}", 120, title=lambda i: f"Standup {i}")
        specs[0] = (f"fd-{tag}-old", "Quarterly Board Review", EPOCH)  # the oldest row
        _seed(email, specs)
        _login(c, email)

        hit = c.get("/api/meetings", params={"q": "quarterly board"}).json()
        assert hit["total"] == 1
        assert [m["title"] for m in hit["items"]] == ["Quarterly Board Review"]
        assert hit["next_cursor"] is None

        # Case-insensitive, and matches the external meeting id too.
        assert c.get("/api/meetings", params={"q": "QUARTERLY"}).json()["total"] == 1
        by_ext = c.get("/api/meetings", params={"q": f"fd-{tag}-old"}).json()
        assert [m["external_meeting_id"] for m in by_ext["items"]] == [f"fd-{tag}-old"]

        # A search wider than a page still pages, and its total is the filtered count.
        rows, totals = _walk(c, limit=30, q="standup")
        assert totals[0] == 119 and len(rows) == 119

        empty = c.get("/api/meetings", params={"q": "nothing matches this"}).json()
        assert empty["items"] == [] and empty["total"] == 0 and empty["next_cursor"] is None


def test_search_wildcards_are_literal():
    """A user typing % or _ is searching for that character, not writing a LIKE pattern."""
    with TestClient(app) as c:
        _ready(c)
        tag = uuid.uuid4().hex[:8]
        email = f"wild-{tag}@test.io"
        _seed(
            email,
            [
                (f"wc-{tag}-a", "Budget 50% cut", EPOCH),
                (f"wc-{tag}-b", "Budget review", EPOCH + timedelta(minutes=1)),
                (f"wc-{tag}-c", "snake_case naming", EPOCH + timedelta(minutes=2)),
                (f"wc-{tag}-d", "snakeXcase naming", EPOCH + timedelta(minutes=3)),
            ],
        )
        _login(c, email)

        assert c.get("/api/meetings", params={"q": "50%"}).json()["total"] == 1
        assert c.get("/api/meetings", params={"q": "%"}).json()["total"] == 1  # not "everything"
        assert c.get("/api/meetings", params={"q": "snake_case"}).json()["total"] == 1


def test_limit_is_clamped_and_a_bad_cursor_is_rejected():
    with TestClient(app) as c:
        _ready(c)
        tag = uuid.uuid4().hex[:8]
        email = f"clamp-{tag}@test.io"
        _seed(email, _series(f"cl-{tag}", 5))
        _login(c, email)

        # A nonsense limit is clamped, never a 422 mid-scroll: <1 floors at one row, and an
        # oversized one is capped at LIST_LIMIT_MAX (here: the 5 rows that exist).
        assert len(c.get("/api/meetings", params={"limit": 0}).json()["items"]) == 1
        assert len(c.get("/api/meetings", params={"limit": -3}).json()["items"]) == 1
        assert len(c.get("/api/meetings", params={"limit": 100000}).json()["items"]) == 5

        # An empty cursor is simply absent: start at the newest.
        assert len(c.get("/api/meetings", params={"cursor": ""}).json()["items"]) == 5

        tampered = base64.urlsafe_b64encode(b"not-a-date|not-a-uuid").decode().rstrip("=")
        for bad in ("!!!", "Zm9v", "  ", tampered):  # unparseable, no separator, junk, tampered
            r = c.get("/api/meetings", params={"cursor": bad})
            assert r.status_code == 422, (bad, r.status_code)
            assert r.json()["detail"] == "invalid cursor"


def test_paging_and_search_stay_owner_scoped():
    with TestClient(app) as c:
        _ready(c)
        tag = uuid.uuid4().hex[:8]
        mine, theirs = f"mine-{tag}@test.io", f"theirs-{tag}@test.io"
        _seed(mine, _series(f"mn-{tag}", 30, title=lambda i: "Shared Title"))
        _seed(theirs, _series(f"th-{tag}", 30, title=lambda i: "Shared Title"))
        _login(c, mine)

        rows, totals = _walk(c, limit=10)
        assert totals[0] == 30
        assert all(m["external_meeting_id"].startswith(f"mn-{tag}") for m in rows)

        hits, hit_totals = _walk(c, limit=10, q="Shared Title")
        assert hit_totals[0] == 30
        assert all(m["external_meeting_id"].startswith(f"mn-{tag}") for m in hits)
