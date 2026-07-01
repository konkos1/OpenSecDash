# Quickstart

Docker Compose is the recommended way to run OpenSecDash.

```bash
cp docker-compose.example.yml docker-compose.yml
docker compose up -d
```

Open the app:

```text
http://localhost:8765
```

The container listens on port `8000` internally. The example compose file maps it to host port `8765`.

## First steps

1. Open **Settings**.
2. Enable the plugins you want to use.
3. Configure datasource paths or API credentials.
4. Open **Diagnostics** to check plugin health.
5. Use **Events**, **IP Explorer**, and **Assets** depending on enabled plugins.

## Security note

OpenSecDash currently has no built-in user management or authentication. Do not expose it directly to the public internet. Put it behind a VPN or a trusted authentication reverse proxy.
