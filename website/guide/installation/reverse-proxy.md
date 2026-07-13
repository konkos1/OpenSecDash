# Reverse proxy

OpenSecDash should be kept internal or placed behind authentication.

Recommended options:

- VPN access only
- Authentik
- Authelia
- Pocket ID
- another trusted forward-auth / SSO layer

## Example traefik dynamic configuration

```yml
http:
  routers:
    opensecdash:
      rule: "Host(`opensecdash.example.com`)"
      entryPoints:
        - websecure
      service: opensecdash-service
      tls:
        certResolver: cloudflare
        options: default

  services:
    opensecdash-service:
      loadBalancer:
        servers:
          - url: "http://localhost:8765/"
```

## Example nginx location

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## Proxy headers

OpenSecDash reads `X-Forwarded-For`, `X-Forwarded-Proto`, and
`X-Forwarded-Host` so its logs and pages use the real client IP, the external HTTPS
scheme, and the public hostname.

These headers are accepted only when the direct connection comes from a trusted
proxy address. By default, OpenSecDash trusts loopback and private network ranges.
This fits typical homelab setups where the proxy runs on the same host or Docker
network.

Use `OSD_TRUSTED_PROXIES` to change this behavior:

```yaml
environment:
  # Trust only these reverse-proxy IPs or CIDRs.
  OSD_TRUSTED_PROXIES: 192.168.1.10,10.0.0.0/8
```

```yaml
environment:
  # Disable proxy-header processing entirely.
  OSD_TRUSTED_PROXIES: ""
```

```yaml
environment:
  # Trust every peer. Use only in an isolated network.
  OSD_TRUSTED_PROXIES: "*"
```

Traefik and Caddy set these headers automatically. The nginx example above already
sets them. If OpenSecDash is accessed directly on your LAN without a proxy and you
do not want proxy-header processing, set `OSD_TRUSTED_PROXIES` to an empty value.

## Public exposure

Do not expose OpenSecDash directly to the public internet without an authentication layer. It may display sensitive logs and action controls.
