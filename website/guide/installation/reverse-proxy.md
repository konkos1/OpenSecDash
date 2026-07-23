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
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Port $server_port;
}
```

## Proxy headers

OpenSecDash reads `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Forwarded-Host`, and
`X-Forwarded-Port` so its logs and pages use the real client IP and the external
request origin.

These headers are accepted only when the direct connection comes from a trusted
proxy address. By default, OpenSecDash trusts loopback and private network ranges.
This fits typical homelab setups where the proxy runs on the same host or Docker
network.

Use `OSD_TRUSTED_PROXIES` to change this behavior:

```yaml
environment:
  # Trust only these reverse-proxy IPs or CIDRs.
  OSD_TRUSTED_PROXIES: 192.168.1.10,172.20.0.5
```

Internal sign-in has a stricter boundary than general proxy-header processing:
`OSD_TRUSTED_PROXIES` must be set explicitly to the proxy IP or the narrowest practical
proxy network. The defaults and `*` do not qualify for enabling internal sign-in.
OpenSecDash also requires the trusted proxy to provide `X-Forwarded-Proto: https`,
`X-Forwarded-Port: 443`, and `X-Forwarded-Host`.

Only requests that pass this boundary receive OpenSecDash's HSTS header. Direct HTTP
health checks and auth-disabled HTTP access do not receive HSTS, so an incorrect proxy
configuration cannot pin an unvalidated direct hostname to HTTPS.

Do not configure an entire LAN or a broad private range such as `10.0.0.0/8`. Every
address in this setting is allowed to supply proxy headers. A compromised host or
container inside an overly broad trusted range could therefore spoof forwarded client
metadata. Prefer individual proxy IPs; use a CIDR only when the proxy address is dynamic,
and keep that network dedicated and as small as practical.

The **Diagnostics → Authentication transport** section validates these requirements for
the current request without exposing configured proxy IPs or network ranges.

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

The reverse proxy terminates TLS and is responsible for serving a certificate that is
valid for the configured OpenSecDash hostname. The browser validates the certificate;
OpenSecDash cannot inspect the proxy's server certificate after TLS has been terminated.

## Single sign-on redirect URL

When [single sign-on](../configuration/authentication.md#single-sign-on-oidc) is used,
the identity provider redirects the browser back to a fixed path on the OpenSecDash
hostname:

```text
https://dash.example.com/auth/oidc/callback
```

OpenSecDash builds this URL from the configured authentication hostname, not from
`X-Forwarded-Host`, so a wrong or missing forwarded host does not change where the
provider is told to redirect. Register exactly this URL with the provider, and make sure
the proxy passes the callback path through unchanged, without stripping the query string
that carries the provider's response. A mismatch between the registered redirect URL and
the configured hostname makes the provider reject the sign-in before OpenSecDash sees it.

The OpenSecDash container also has to reach the provider directly and trust its
certificate; that connection does not go through this reverse proxy.

## Public exposure

Do not expose OpenSecDash directly to the public internet without an authentication layer. It may display sensitive logs and action controls.
