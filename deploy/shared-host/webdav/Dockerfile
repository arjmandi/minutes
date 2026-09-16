# Caddy 2.8 with the WebDAV handler (http.handlers.webdav) compiled in via xcaddy, for the
# optional "webdav" profile of the shared-host deployment.
FROM caddy:2.8-builder AS builder
RUN xcaddy build --with github.com/mholt/caddy-webdav

FROM caddy:2.8
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
