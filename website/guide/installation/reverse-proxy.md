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

## Public exposure

Do not expose OpenSecDash directly to the public internet without an authentication layer. It may display sensitive logs and action controls.
