"""Admin CLI for minutes — run on the server, e.g.:

    docker compose exec -T backend python -m app.admin create-user --email you@org.com [--admin]
    docker compose exec -T backend python -m app.admin set-password --email you@org.com
    docker compose exec -T backend python -m app.admin delete-user --email you@org.com
    docker compose exec -T backend python -m app.admin list-users

Passwords are read interactively (never on argv), enforced against the backend password policy.
The FIRST user created is automatically an admin. There is no public signup; an admin creates users.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.auth.passwords import WeakPassword, hash_password, validate_password
from app.config import get_settings
from app.db import repo
from app.db.base import make_engine, make_session_factory


def _prompt_password() -> str | None:
    pw = getpass.getpass("New password: ")
    if getpass.getpass("Confirm password: ") != pw:
        print("passwords do not match", file=sys.stderr)
        return None
    try:
        validate_password(pw)
    except WeakPassword as exc:
        print(f"weak password: {exc}", file=sys.stderr)
        return None
    return pw


async def _create_user(email: str, admin: bool) -> int:
    engine = make_engine(get_settings().database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as db:
            if await repo.get_user_by_email(db, email) is not None:
                print(f"user already exists: {email}", file=sys.stderr)
                return 1
            first = (await repo.count_users(db)) == 0
        pw = _prompt_password()
        if pw is None:
            return 1
        async with factory() as db:
            user = await repo.create_user(
                db, email=email, password_hash=hash_password(pw), is_admin=admin or first
            )
            await db.commit()
            print(f"created user {user.email} (admin={user.is_admin})")
        return 0
    finally:
        await engine.dispose()


async def _set_password(email: str) -> int:
    engine = make_engine(get_settings().database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as db:
            user = await repo.get_user_by_email(db, email)
            if user is None:
                print(f"no such user: {email}", file=sys.stderr)
                return 1
        pw = _prompt_password()
        if pw is None:
            return 1
        async with factory() as db:
            user = await repo.get_user_by_email(db, email)
            user.password_hash = hash_password(pw)
            await repo.revoke_user_tokens(db, user_id=user.id)
            await db.commit()
            print(f"password updated for {email} (existing sessions revoked)")
        return 0
    finally:
        await engine.dispose()


async def _delete_user(email: str) -> int:
    engine = make_engine(get_settings().database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as db:
            user = await repo.get_user_by_email(db, email)
            if user is None:
                print(f"no such user: {email}", file=sys.stderr)
                return 1
            await repo.delete_user(db, user.id)
            await db.commit()
            print(f"deleted user {email}")
        return 0
    finally:
        await engine.dispose()


async def _list_users() -> int:
    engine = make_engine(get_settings().database_url)
    factory = make_session_factory(engine)
    try:
        async with factory() as db:
            users = await repo.list_users(db)
        if not users:
            print("(no users)")
        for u in users:
            flags = "admin" if u.is_admin else "user"
            active = "" if u.is_active else " [disabled]"
            print(f"{u.email}\t{flags}{active}\t{u.created_at.isoformat()}")
        return 0
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.admin", description="minutes admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user", help="create a user (first user is auto-admin)")
    cu.add_argument("--email", required=True)
    cu.add_argument("--admin", action="store_true", help="grant admin")
    sp = sub.add_parser("set-password", help="set a user's password (revokes their sessions)")
    sp.add_argument("--email", required=True)
    du = sub.add_parser("delete-user", help="delete a user")
    du.add_argument("--email", required=True)
    sub.add_parser("list-users", help="list users")
    args = parser.parse_args()

    if args.cmd == "create-user":
        rc = asyncio.run(_create_user(args.email, args.admin))
    elif args.cmd == "set-password":
        rc = asyncio.run(_set_password(args.email))
    elif args.cmd == "delete-user":
        rc = asyncio.run(_delete_user(args.email))
    else:
        rc = asyncio.run(_list_users())
    sys.exit(rc)


if __name__ == "__main__":
    main()
