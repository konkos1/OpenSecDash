#!/bin/sh
set -e

# Docker bind mounts are often created as root-owned host directories. The
# container starts as root only long enough to make persistent paths writable,
# then drops privileges before running the app.
mkdir -p /data

# Some setups still bind-mount individual read-only files under /data. A plain
# `chown -R` aborts the whole entrypoint under `set -e` as soon as it hits one
# of those, because it can't chown a read-only mount. Only /data ownership
# itself is required for the app to run; failures on individual read-only
# paths underneath are expected and non-fatal, so they're reported but ignored.
chown opensecdash:opensecdash /data
if ! chown -R opensecdash:opensecdash /data 2>/tmp/opensecdash-chown.log; then
    echo "Warning: could not change ownership of some paths under /data (likely read-only mounts):" >&2
    cat /tmp/opensecdash-chown.log >&2
fi
rm -f /tmp/opensecdash-chown.log

if [ "$#" -eq 0 ] || [ "$1" = "opensecdash" ]; then
    set -- uvicorn app.main:app \
        --host "${OSD_HOST}" \
        --port "${OSD_PORT}" \
        --no-proxy-headers
fi

# Execute the application directly as PID 1 so SIGTERM reaches Uvicorn and
# Docker can wait for its graceful shutdown instead of killing a child shell.
exec setpriv --reuid=opensecdash --regid=opensecdash --init-groups "$@"
