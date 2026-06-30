---
sidebar_position: 2
title: "Creating & managing users"
---

# Creating & managing users

minutes has **no public signup**. You — the operator — create every account from the command line on the server, using the built-in `app.admin` CLI that ships inside the backend container.

This page covers creating, password-resetting, deleting, and listing users, and what to hand a new person so they can start transcribing.

:::note
All commands run *inside* the running `backend` container via `docker compose exec`, so run them from your deploy directory (`deploy/single-box`, where your `docker-compose.yml` and `.env` live). The stack must already be up — see [Deploy](/admin/deploy).
:::

## The admin CLI

The CLI is a Python module, `app.admin`, with four subcommands:

| Command | What it does |
| --- | --- |
| `create-user` | Create a user (the **first** user created is automatically an admin) |
| `set-password` | Set a user's password and revoke all their existing sessions |
| `delete-user` | Permanently delete a user |
| `list-users` | List all users with their role, status, and creation date |

Passwords are **always read interactively** at a prompt — they are never passed on the command line, so they never leak into your shell history or the process list.

:::tip Give the command a TTY
Because the password prompt is interactive, run `docker compose exec` **with** a TTY (the default). Do **not** add `-T` for the password-setting commands, or the prompt won't appear. If you're connecting over SSH, use `ssh -t` so the remote TTY is allocated:

```bash
ssh -t you@your-server
# ...then run the docker compose exec commands below
```
:::

## Create a user

```bash
docker compose exec backend python -m app.admin create-user --email you@org.com
```

You'll be prompted for the password twice:

```text
New password:
Confirm password:
created user you@org.com (admin=True)
```

The **first user ever created** is automatically made an admin, regardless of flags — so your very first `create-user` produces your admin account (note `admin=True` above). Every user after that is a regular user unless you pass `--admin`:

```bash
docker compose exec backend python -m app.admin create-user --email teammate@org.com --admin
```

If the email already exists, the command refuses and exits non-zero (`user already exists: ...`).

### Password requirements

Passwords are validated on the backend (not just in the browser), so the same policy applies here and in the web app's password-change form. A password must be:

- **at least 12 characters** long, and
- contain **at least 3 of these 4 character classes**: lowercase, uppercase, digit, symbol.

If the two prompts don't match, or the password is too weak, the command prints the reason and exits without creating the user — just run it again.

## Set (reset) a password

Use this to reset a forgotten password or rotate one. You'll get the same interactive double prompt as `create-user`:

```bash
docker compose exec backend python -m app.admin set-password --email you@org.com
```

```text
New password:
Confirm password:
password updated for you@org.com (existing sessions revoked)
```

:::warning Sessions are revoked
Setting a password **revokes all of that user's existing sessions** — including the device token their browser extension uses. After a reset, the user must sign in again in both the web app and the extension popup.
:::

## List users

```bash
docker compose exec backend python -m app.admin list-users
```

This one has no prompt, so it's fine to add `-T`. Output is tab-separated — email, role (`admin` or `user`), a `[disabled]` marker for inactive accounts, and the ISO-8601 creation timestamp:

```text
you@org.com	admin	2026-06-01T09:14:33.120000+00:00
teammate@org.com	user	2026-06-02T11:02:10.880000+00:00
```

If there are no users yet, it prints `(no users)`.

## Delete a user

```bash
docker compose exec backend python -m app.admin delete-user --email teammate@org.com
```

```text
deleted user teammate@org.com
```

:::danger Deletion is permanent
`delete-user` removes the account immediately. For erasing a specific meeting's data (rather than removing the account), an owner or admin can delete a single meeting from the web app; see the erasure note in [Limitations](/reference/limitations). If the email doesn't exist, the command prints `no such user: ...` and exits non-zero.
:::

## What to hand a new user

Once the account exists, send the new user three things so they can get going:

1. **The server URL** — your HTTPS address, e.g. `https://your-domain`. They use it two ways: **open it in a browser** for the web app (the landing page's **Login** button takes them to `/app`, or they can go straight to `https://your-domain/app`), and **paste it into the extension's Settings → Server URL** (gear icon in the popup). It must be the full `https://` domain — the extension needs TLS for `wss://`, so a bare IP won't work.
2. **Their email and the password you set** — the same credentials they sign in with in both the web app and the extension popup. Encourage them to change the password once they're in (Settings → Account).
3. **Where to get the extension** — the Chrome "capture" extension that streams the meeting tab's audio. Once it's on the Chrome Web Store, send them the store link; until then, they load it unpacked (see [Installing the extension](/users/extension)).

:::tip Bring-your-own keys
New accounts start with no API keys. Live capture works immediately using the **server's** Soniox key. But a user must add their **own** Anthropic key in the web app's Settings to get translation, and their **own** Soniox key to transcribe uploaded audio files. See the [Configuration reference](/admin/configuration) and the [Web app](/users/web-app) guide for details.
:::

Point them at the end-user guides next:

- **[Using the web app](/users/web-app)** — signing in, running a live meeting, translation, and export.
- **[Installing the extension](/users/extension)** — loading the capture extension and pointing it at your server.
