# Bare-metal installation

Bare-metal installation is supported if you prefer running OpenSecDash directly on a Linux host. Docker is still recommended unless you explicitly want to manage Python, systemd, file paths, and permissions yourself.

Install Python 3.13 and the officially pinned `uv` 0.11.23 release before starting.

## Host requirements

Minimum for a small homelab instance:

| Resource | Minimum | Recommended |
| --- | --- | --- |
| CPU | 1 vCPU | 2 vCPU |
| RAM | 512 MB | 1 GB+ |
| Storage | 1 GB free | SSD with several GB free, depending on log volume and retention |

OpenSecDash is lightweight, but storage usage depends on imported event volume, configured retention, and debug/log output.

As a rough guide, measured on SQLite after `VACUUM` (events plus their indexes and rollups):

| Events currently stored | Approximate database size |
| --- | --- |
| A few thousand (light homelab use) | A few MB |
| 10,000 | ~10 MB |
| 100,000 | ~100 MB |
| 1,000,000 | ~1 GB |
| 10,000,000 (very many) | ~10 GB |

Rule of thumb: **~1 KB per stored event**. This is about how many events are currently kept (bounded by the `Retention days` setting), not how many were ever imported - daily/monthly rollups used for historical charts and dashboards stay tiny (a few KB per day) even after old raw events are cleaned up by retention. A busy, public-facing Traefik access log can easily produce tens of thousands of events a day, so size `Retention days` and storage accordingly.

## Placement

OpenSecDash is easiest to operate when it can read relevant log files locally. For bare-metal installs, consider running it on the same guest/host as Traefik, GeoBlock, CrowdSec, and similar tools, or ensure their log files are available through shared storage or another log shipping approach.

If OpenSecDash runs separately, make sure file permissions and paths still allow the `opensecdash` user to read the configured log files.

## Layout

A typical setup uses:

- user: `opensecdash`
- code: `/opt/opensecdash`
- data: `/var/lib/opensecdash`
- logs: `/var/log/opensecdash`

## Install

```bash
sudo useradd --system --home /opt/opensecdash --shell /usr/sbin/nologin opensecdash
sudo mkdir -p /opt/opensecdash /var/lib/opensecdash /var/log/opensecdash
sudo chown -R opensecdash:opensecdash /opt/opensecdash /var/lib/opensecdash /var/log/opensecdash

cd /opt/opensecdash
sudo -u opensecdash git clone https://github.com/konkos1/OpenSecDash.git .
cd /opt/opensecdash/backend
sudo -u opensecdash uv lock --check
sudo -u opensecdash uv sync --frozen --no-dev
```

Create `/opt/opensecdash/backend/.env`:

```ini
DATABASE_URL=sqlite:////var/lib/opensecdash/opensecdash.db
AUTO_MIGRATE=true
LOG_FILE_ENABLED=true
LOG_FILE_PATH=/var/log/opensecdash/opensecdash.log
LOG_LEVEL=INFO
```

## Runtime options

Bare-metal installs are configured through `.env`, systemd, and the Settings page.

| Option | Recommended value | What it does |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:////var/lib/opensecdash/opensecdash.db` | Database connection string. The four slashes are important for an absolute SQLite path. |
| `AUTO_MIGRATE` | `true` | Runs Alembic migrations on startup. Set to `false` only if you manage migrations manually. |
| `LOG_FILE_ENABLED` | `true` for bare metal | Seeds whether OpenSecDash writes an additional file log. Service logs also go to journald. |
| `LOG_FILE_PATH` | `/var/log/opensecdash/opensecdash.log` | File log path when file logging is enabled. Ensure the `opensecdash` user can write there. |
| `LOG_LEVEL` | `INFO` | Use `DEBUG` only temporarily for troubleshooting. |

Logging settings are saved in the app database after initial setup. If you change logging environment variables later and the Settings page already has saved values, use the Settings page to change logging behavior.

The HTTP bind address and port are controlled by the uvicorn command in the systemd service:

```ini
ExecStart=/opt/opensecdash/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Common choices:

| Bind | Meaning |
| --- | --- |
| `--host 127.0.0.1 --port 8000` | Recommended behind a local reverse proxy. |
| `--host 0.0.0.0 --port 8000` | Listen on all interfaces. Use only on a trusted LAN or behind firewall/auth controls. |

## systemd service

Create `/etc/systemd/system/opensecdash.service`:

```ini
[Unit]
Description=OpenSecDash
After=network.target

[Service]
User=opensecdash
Group=opensecdash
WorkingDirectory=/opt/opensecdash/backend
EnvironmentFile=/opt/opensecdash/backend/.env
ExecStart=/opt/opensecdash/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now opensecdash
journalctl -u opensecdash -f
```

## Log rotation

When file logging is enabled, configure OS log rotation. Example `/etc/logrotate.d/opensecdash`:

```text
/var/log/opensecdash/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
```
