# Debug reports

The Diagnostics page can create a debug ZIP package.

The package includes:

- settings with known sensitive values redacted
- plugin state
- diagnostics
- datasource state
- database counts
- recent actions
- app log tail if file logging is enabled

Review every file before attaching it to a public issue. Hostnames, public IPs, asset names, and log payloads may still be sensitive in your environment.

## Docker logs

Docker installs log to stdout/stderr by default. If file logging is disabled, the debug ZIP explains how to collect Docker logs:

```bash
docker compose logs opensecdash --tail=500
```
