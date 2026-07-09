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

exec setpriv --reuid=opensecdash --regid=opensecdash --init-groups sh -c "$*"
