# Quickstart

Docker Compose is the recommended way to run OpenSecDash.

docker-compose.yml example:

```yml
services:
  opensecdash:
    image: konkos1/opensecdash:latest
    container_name: opensecdash
    ports:
      - "8765:8000"
    volumes:
      - opensecdash-data:/data
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
    restart: unless-stopped

volumes:
  opensecdash-data:
```
Start the app:

```bash
docker compose up -d
```

Open the app:

```text
https://dash.example.com
```

The container listens on port `8000` internally. The example compose file maps it to host port `8765` to avoid common homelab port-conflicts.

This is the minimal setup. For plugin log mounts, host requirements, database sizing, and logging configuration, see the full [Docker installation guide](../installation/docker.md).

## First start

A new installation starts with internal sign-in enabled, so the first visit shows a
one-time setup page instead of the dashboard. There you create the first Admin account.

Finishing that setup requires a reverse proxy: the request has to arrive over HTTPS on
external port 443, from a proxy named explicitly in `OSD_TRUSTED_PROXIES`, under the
hostname you enter. Set the proxy up before the first visit; see
[Reverse proxy](../installation/reverse-proxy.md) and
[Authentication](../configuration/authentication.md#first-time-setup-new-installations).

Afterwards OpenSecDash redirects you to the login page and you sign in with the account
you just created — the setup itself does not sign you in.

::: tip Just trying it out on `http://localhost:8765`?
Without a reverse proxy the setup cannot be completed. To run OpenSecDash open on
purpose — for a local trial, or because a VPN or an authentication proxy already protects
it — add `OSD_AUTH_DISABLED=true` to the environment and restart. Every visitor who can
reach the instance then has full access; see
[Deliberately running without internal sign-in](../configuration/authentication.md#deliberately-running-without-internal-sign-in).
:::

Updating an existing installation changes nothing about its sign-in state: it stays
enabled where it was enabled, and stays open where it was open — with a permanent prompt
to decide.

## First steps

1. Open **Settings**.
2. Enable the plugins you want to use.
3. Configure datasource paths or API credentials.
4. Open **Diagnostics** to check plugin health.
5. Leave **Action simulation** enabled while testing CrowdSec actions.
6. Optionally configure SMTP notifications and send a test message.
7. Optionally add more users, or configure single sign-on, under **Settings → Sign-in & users**.
8. Use **Dashboard**, **Events**, **IP Explorer**, and **Assets** depending on enabled plugins.

## Security note

Do not expose OpenSecDash directly to the public internet. Put it behind a VPN or a
trusted authentication reverse proxy. Internal sign-in protects a new installation from
the first visit; an updated installation keeps whatever it had. See the
[authentication guide](../configuration/authentication.md) for both cases.
