# Updating

Back up the persistent `/data` volume before a major upgrade. It contains the SQLite database, encrypted settings, instance branding files, and—unless you supply `OSD_SECRET_KEY` yourself—the local encryption key needed to read stored secrets.

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

## Upgrading from v0.3.1 or earlier

The releases after `v0.3.1` add database migrations for notifications, saved views, personal preferences, instance branding, and optional internal users. These migrations run automatically with the default `AUTO_MIGRATE=true` setting. Internal authentication remains disabled until an administrator explicitly enables it, so the upgrade does not create a surprise login requirement.

### CrowdSec breaking change: migrate from `cscli` to LAPI

::: danger Action required for former `cscli` mode
OpenSecDash no longer launches a configurable `cscli` executable. Existing connection-mode and executable-path settings are ignored. Configure a CrowdSec LAPI URL, machine login, and password before relying on decision synchronization or real ban/unban actions.
:::

Create dedicated machine credentials on the CrowdSec host:

```bash
sudo cscli machines add opensecdash --auto -f /tmp/opensecdash-lapi.yaml
sudo cat /tmp/opensecdash-lapi.yaml
sudo rm /tmp/opensecdash-lapi.yaml
```

Enter the resulting `url`, `login`, and `password` under **Settings → CrowdSec**. The `cscli` command above is used only once on the CrowdSec host to create credentials; OpenSecDash itself no longer mounts or executes that binary.

The subprocess mode was removed for security reasons. It required mounting a host executable and CrowdSec configuration into the OpenSecDash container, while a configurable executable path created an unnecessary code-execution primitive if settings access could be combined with control over a suitable executable. LAPI provides the same required behavior with dedicated, revocable credentials and no executable or CrowdSec configuration mounts.

See the [CrowdSec plugin guide](../plugins/crowdsec.md) for network examples, URL validation rules, and connection diagnostics.

## Verify after updating

After the container or service restarts:

1. Open **Diagnostics** and confirm database migrations are healthy.
2. Check every enabled datasource and plugin diagnostic.
3. If CrowdSec is enabled, confirm `plugin · crowdsec` reports the log reader and `crowdsec · lapi` reports decision synchronization separately.
4. Send a test notification if SMTP is enabled.
5. Reload an open browser tab so versioned JavaScript and CSS assets are refreshed.
