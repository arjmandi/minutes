"""Mint a capability token from the CLI (the credential capture clients / the viewer use).

Run inside the running backend so it uses the deployed MINUTES_AUTH_SECRET, e.g.:

    docker compose exec -T backend python -m app.mint_token --meetings '*' --ttl 86400
    docker compose exec -T backend python -m app.mint_token --meetings meet:standup-42 --ttl 3600
    docker compose exec -T backend python -m app.mint_token --meetings '*' --admin   # erasure scope

`--meetings` takes one or more `platform:external_meeting_id` scopes, or `*` for all. Tokens cannot
be revoked before they expire — prefer narrow scopes and short TTLs.
"""

from __future__ import annotations

import argparse

from app.auth.tokens import issue_capability_token
from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Mint a minutes capability token.")
    parser.add_argument("--principal", default="operator", help="token subject / who it is for")
    parser.add_argument(
        "--meetings",
        nargs="+",
        default=["*"],
        metavar="SCOPE",
        help="one or more platform:external_meeting_id scopes, or '*' for all",
    )
    parser.add_argument(
        "--ttl", type=int, default=86400, help="lifetime in seconds (default 1 day)"
    )
    parser.add_argument(
        "--admin", action="store_true", help="grant the admin flag (required for erasure)"
    )
    args = parser.parse_args()

    settings = get_settings()
    token = issue_capability_token(
        principal=args.principal,
        secret=settings.auth_secret,
        algorithm=settings.auth_algorithm,
        ttl_s=args.ttl,
        meetings=args.meetings,
        admin=args.admin,
    )
    print(token)


if __name__ == "__main__":
    main()
