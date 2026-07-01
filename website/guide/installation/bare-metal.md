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
```

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
