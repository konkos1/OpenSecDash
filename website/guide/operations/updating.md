# Updating

For Docker installs, update the image and restart the container:

```bash
docker compose pull
docker compose up -d
```

Database migrations run automatically by default when `AUTO_MIGRATE=true`.

For bare-metal installs, pull the repository, update the virtual environment, and restart the systemd service.

```bash
cd /opt/opensecdash
git pull
cd backend
.venv/bin/pip install -e .
sudo systemctl restart opensecdash
```
