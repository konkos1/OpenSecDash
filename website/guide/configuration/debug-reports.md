# Debug reports

The Diagnostics page can create a debug ZIP package.

The package includes:

- settings with known sensitive values redacted and email addresses partially masked
- plugin state
- diagnostics
- datasource state
- database counts, including the newer authentication, notification, saved-view, and preference tables
- runtime mode for database migrations and trusted reverse proxies
- aggregate authentication state and session/user counts without usernames or hashes
- notification configuration state, delivery counts, and recent redacted failures without message payloads
- branding/PWA asset metadata without filenames or file contents
- aggregate saved-view, preference, and dashboard-layout state without names or filter contents
- recent actions
- app log tail if file logging is enabled

Review every file before attaching it to a public issue. Hostnames, public IPs, email addresses, asset names, and log payloads may still be sensitive in your environment.

## Docker logs

Docker installs log to stdout/stderr by default. If file logging is disabled, the debug ZIP explains how to collect Docker logs:

```bash
docker compose logs opensecdash --tail=500
```
