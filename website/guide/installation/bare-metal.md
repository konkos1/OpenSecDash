# Bare-metal installation

Bare-metal installation is supported if you prefer running OpenSecDash directly on a Linux host. Docker is still recommended unless you explicitly want to manage Python, systemd, file paths, and permissions yourself.

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
sudo -u opensecdash python3 -m venv .venv
sudo -u opensecdash .venv/bin/pip install --upgrade pip
sudo -u opensecdash .venv/bin/pip install -e .
```

Create `/opt/opensecdash/backend/.env`:

```env
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
