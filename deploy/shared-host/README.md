# Minutes on the shared Oddproof host

Production runs on the Oddproof host behind the **Oddproof edge proxy**
(`arjmandi/oddproof-edge`), which terminates TLS for `gettheminutes.com` and forwards to
this project's `web` container (alias `minutes-web`) over the shared `oddproof-edge`
Docker network. This project publishes no host ports. `deploy/single-box/` remains the
self-contained recipe for anyone hosting Minutes alone on a small VPS.

## How a deploy happens

Pushing a tag `v*` (or running the workflow by hand) runs `.github/workflows/deploy.yml`:
it builds the backend image to GHCR, builds the documentation site, joins the tailnet as a
`tag:ci` node, syncs this directory to `deploy@host:~/minutes/`, syncs the docs build to
`~/minutes/docs/`, and runs `deploy.sh` with the image tag. The script backs up the
database, runs migrations, restarts services in dependency order and records the tag in
`.image-tag` for rollback. The job targets the `production` GitHub environment; add
required reviewers there to make each deploy a manual approval.

Repository secrets: `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`, `DEPLOY_SSH_KEY`,
`DEPLOY_HOST`. Application values live only in `~/minutes/.env` on the host
(`.env.example`); the deploy never touches that file.

## One-time host preparation

```sh
mkdir -p ~/minutes/landing ~/minutes/docs ~/minutes/backups
cp .env.example ~/minutes/.env && chmod 600 ~/minutes/.env   # then fill it in
```

`~/minutes/landing/` holds the marketing landing (static files with an `index.html`);
without it the app is served at `/`. The edge project must be running.

## Moving from the single-box host

1. Old host: `docker compose exec -T postgres pg_dump -U minutes -Fc minutes > minutes.dump`
   and mirror the `minutes-audio` bucket (`mc mirror`).
2. New host, after the first deploy created the volumes:
   `docker compose exec -T postgres pg_restore -U minutes -d minutes --clean --if-exists < minutes.dump`,
   copy the bucket back, `docker compose restart backend scheduler`.
3. Lower the DNS TTL a day ahead, point `gettheminutes.com` at the new host at cutover, keep
   the old box a week for rollback. The capture extension and the PWA keep working because
   the hostname does not change.

## Optional WebDAV add-on

`COMPOSE_PROFILES=webdav` in `~/minutes/.env` starts a small Caddy with the WebDAV module
serving `~/minutes/webdav/data` at `https://gettheminutes.com/webdav/` behind basic auth
(`webdav/Caddyfile.example`). The image builds on the host from `webdav/Dockerfile`; nothing
in the product depends on it.
