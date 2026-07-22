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
