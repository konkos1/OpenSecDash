# Troubleshooting

## Start with Diagnostics

Open the Diagnostics page and check:

- plugin status
- datasource status
- database migration status
- recent actions

## Create a debug report

Use **Diagnostics → Download debug report** and review the ZIP before sharing it.

## Docker logs

```bash
docker compose logs opensecdash --tail=500
```

## Health checks

`GET /health` is the liveness check. It only confirms that the application process can answer HTTP and does not access the database.

`GET /ready` is the readiness check. After startup has completed, it performs one read-only `SELECT 1` database ping. It does not run migrations, seed defaults, rotate secrets, or perform event maintenance. A database error returns `503 Service Unavailable` without database paths or exception details.

## Locked out of the web UI

If optional internal sign-in is enabled and no administrator can sign in, use the
`OSD_AUTH_DISABLED=true` recovery switch to temporarily open the UI, reset access, or
repair a changed authentication hostname. Restrict network access while the switch is
active: every client that can reach OpenSecDash has full access. Remove the variable and
restart after the repair. See
[Authentication](../configuration/authentication.md#locked-out-of-the-web-ui).

## Single sign-on does not work

Sign-in errors are intentionally generic in the browser. The container log carries the
error class, and **Diagnostics → Single sign-on (OIDC)** shows the stored result of the
last provider check without contacting the provider.

| What you see | Usual cause |
| --- | --- |
| "Single sign-on is not available right now." | The provider is not enabled or not completely configured, the authentication hostname is missing, or the request did not arrive through the trusted HTTPS/443 proxy boundary. |
| "The sign-in took too long. Please start again." | The ten-minute transaction cookie expired, the container restarted mid-sign-in, the browser dropped the cookie, or the same provider response was replayed. |
| "Signing in with the provider did not work." | The provider was unreachable, its TLS certificate is not trusted inside the container, the token exchange failed, or the ID token was rejected — wrong issuer, wrong audience, wrong or missing nonce, expired token, or an unsafe signature algorithm. |
| "This provider account cannot sign in to OpenSecDash." | The provider account is not linked and automatic user creation is off, or the linked local user is deactivated. |
| The provider itself refuses with a redirect URI error | The registered redirect URL does not match `https://<your hostname>/auth/oidc/callback`. |

Check in this order:

1. Does **Settings → Sign-in & users** report a successful last provider check? Use
   **Check and save provider** to repeat it.
2. Does the registered redirect URL match the value shown in Settings exactly?
3. Can the container reach the provider and trust its certificate? Provider connections
   use the container's trust store, so a private CA has to be trusted there — mount a
   PEM bundle and set `SSL_CERT_FILE`, or add the CA to your own derived image.
   OpenSecDash has no option to skip certificate verification.
4. Is the discovery URL an HTTPS address without credentials, query, or fragment, and
   does it live on the same host as the issuer it declares? Loopback and cloud metadata
   addresses are rejected on purpose; private homelab addresses are allowed.
5. Did the issuer change at the provider? A changed issuer needs a new provider check,
   and existing links stay with the old issuer.
6. Is the browser reaching OpenSecDash under the configured hostname over HTTPS on port
   443? A different hostname or a missing forwarded header stops sign-in before any
   request leaves the container.

If nobody can sign in because password sign-in is off, use the emergency switch
described under
[Provider outage or misconfiguration](../configuration/authentication.md#provider-outage-or-misconfiguration).

## First import of a large existing log

Setting a log path (or enabling a log-based plugin) for the first time makes OpenSecDash read through the whole existing file, not just new lines going forward. A small/fresh log finishes almost instantly; a log that already has months of history can take a while to fully import. That import runs in bounded batches in the background instead of all at once, so the UI stays responsive and usable while it catches up.

A banner near the top of every page shows while a plugin is still catching up on a backlog, with a rough progress percentage, and disappears automatically once it reaches the end of the file. GeoIP country/city/ASN/ISP lookups for the imported events are filled in afterwards at their own pace, so they may briefly show as unknown right after a large first import.

## Proxmox guest visibility

If the Proxmox plugin imports nodes but no guests, check the plugin diagnostic message and verify the API token can see `qemu` or `lxc` entries from:

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=opensecdash@pve!inventory=TOKEN_SECRET' \
  'https://pve.example.local:8006/api2/json/cluster/resources'
```
