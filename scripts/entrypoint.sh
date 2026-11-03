#!/bin/sh
# Container entrypoint: make the data volume writable for the app user, then run the app as that user.
#
# Fly mounts volumes root-owned, so a plain `USER punch` image couldn't write to /data. The image
# therefore starts as root only long enough to create DATA_DIR and hand it to `punch`, then drops
# privileges with setpriv (util-linux) and execs the command. When the container is already started
# as a non-root user (e.g. `docker run --user`), it just execs the command.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
APP_USER="${APP_USER:-punch}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  # Only fix the top level (and anything not owned yet): a full chown of a big volume is slow.
  chown "$APP_USER:$APP_USER" "$DATA_DIR"
  find "$DATA_DIR" -maxdepth 2 ! -user "$APP_USER" -exec chown "$APP_USER:$APP_USER" {} + 2>/dev/null || true
  exec setpriv --reuid="$APP_USER" --regid="$APP_USER" --init-groups -- "$@"
fi
exec "$@"
