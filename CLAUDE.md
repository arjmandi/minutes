# minutes (public mirror)

Self-hosted live transcription + translation for browser audio (Google Meet, Teams, webinars,
recordings): a Chrome-extension capture client feeds an ingest WebSocket, a FastAPI backend
transcribes via Soniox and translates via an LLM, and a web app streams the live transcript. See
`README.md` for the product pitch and quickstart.

**⚠️ This repo is a downstream mirror, not the primary dev repo.** The actual development happens in
the private repo `minutes-private` (`arjmandi/minutes-private`) — product code is mirrored FROM there
TO here, manually, by Mohsen. **But this is the repo the live server deploys from**: `gettheminutes.com`
runs `/opt/minutes`, which clones `github.com/arjmandi/minutes.git` on `main` and ships via
`git pull && docker compose up -d --build` **run by hand on the box** — there is no CI and no
auto-deploy-on-push. A merge here does not go live until Mohsen does that step.

**Practical consequence for you:** if a task here is a genuine product bug fix (not docs/README/
self-host-story work), say so plainly in your summary — the identical fix likely also needs applying
in `minutes-private`, and you cannot do that from this worktree.

## Commands

```sh
uv sync                                   # Python 3.12 venv + deps
uv run pytest                             # full suite — needs local postgres + redis (see below)
uv run ruff check . && uv run ruff format --check .
uv run mypy .                             # if the task touches typed code
```

If postgres/redis are not already up locally, **report that and don't chase it** — starting the full
stack is out of scope for a headless code-change task (see Constraints).

## Layout

```
app/                 FastAPI backend — main.py, config.py (MINUTES_* settings), db/, session/,
                      admission/ (Redis admission cap), api/ (health + capture ingest WS)
app/web/              product web UI served by the backend (transcript viewer, PWA)
capture/extension/    Chrome extension — tab + mic capture, feeds the ingest WebSocket
capture/driver/       Playwright bot-join automation (Meet/Teams) — NEVER run this, see below
docs-site/            Docusaurus docs, built + served at /docs
deploy/single-box/    the compose stack + Caddyfile that IS live today — reference only
migrations/           Alembic (async) schema migrations
scripts/              ingest_wav.py (safe, local pipeline test) · soniox_live_check.py (real
                      billable API call — NEVER run this, see below)
```

## Conventions

- Python 3.12 (`requires-python = ">=3.12,<3.13"`), ruff (line-length 100, `py312`,
  `migrations/versions` excluded from style rules — those are machine-generated).
- Tests: pytest + pytest-asyncio in `tests/`; admission tests need Redis running.
- "Application layout, not a distributable package" (`pyproject.toml`) — no `pip install -e`.

## Constraints that are not obvious from the code

- **This is a live, paid-adjacent production service with a real signed-in bot account** —
  treat every command as if it could touch it, because several genuinely can:
  - `scripts/soniox_live_check.py` makes a **real, billable** Soniox connection (its own docstring
    says so). Never run it.
  - `capture/driver/join.mjs` **launches Chrome, signs in the bot account, and joins a real Meet/
    Teams meeting.** Never run it, `npm run join`, or any Playwright invocation in that directory.
    `capture/driver/.profiles/` holds the bot's persisted login — never read it.
  - Never `ssh` anywhere. The deploy box (`gettheminutes.com`, `/opt/minutes`) is a real server;
    nothing here should ever reach it. Denied in settings, but don't attempt a workaround either.
  - `docker compose up -d postgres redis` (local dev deps) is fine; the deploy incantation
    `docker compose up -d --build` is not something you ever run — that's a production build.
- **Never run `uv run alembic upgrade`.** Add new migration files as code if a schema change is
  needed; don't apply them. Never hand-edit an already-committed migration file — add a new revision.
- **Audio/transcript data is real user content** (meetings, potentially GDPR-relevant per the
  README's framing) — nothing here should ever be about real recorded audio; test fixtures only.
- `site/`, `design/`, `planning/`, `docs/` (architecture specs), and `OPERATIONS.md` — the deployment
  reference, including real server access details — **live only in `minutes-private`, not here.**
  Don't invent them or assume they exist in this checkout.

## Working under agentd

You may be running unattended, triggered by a board move or an issue comment.

- **Read `.claude/FLEET-RULES.md` first.** Never chain a probe in front of the command you need;
  scratch files go in `.scratch/`, never `/tmp`; the browser (where equipped) is read-and-verify only.
  The daemon refreshes this file from pado on every run.
- You are in a **git worktree** on branch `agent/issue-N`. Stay in it.
- **Commit** your work in logical units, referencing the issue number.
- **Do not push and do not open a PR.** The daemon does both; `git push` is denied on purpose.
- **Journal entries are per-issue files** — a NEW `docs/agent-journal/<issue>-<slug>.md`, only if this
  run taught you something a future agent would otherwise rediscover the hard way. Most runs need none.
- End your final message with a 3–6 line summary suitable for a GitHub comment, then `STATUS: DONE`
  or `STATUS: BLOCKED` with the reason. The daemon parses those tokens — spelling matters.
- **Don't close the issue or merge a PR on your own judgment.** `STATUS: DONE` means "ready for
  review". The one exception is an explicit instruction in the thread ("close it", "merge it").
- **Board writes.** Comments on `arjmandi/me` must START with `<!-- agentd -->` or they read as an
  instruction from Mohsen and re-trigger an agent. Link every comment's URL in your summary.
- **This route has no headless browser.** Browser-bound work (which here would mean the capture
  extension or the bot driver — both denied anyway) ends `STATUS: BLOCKED`.
- If the task is underspecified, prefer `STATUS: BLOCKED` with a specific question over guessing.
