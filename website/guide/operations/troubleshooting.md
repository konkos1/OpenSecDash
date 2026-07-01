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

## Proxmox guest visibility

If the Proxmox plugin imports nodes but no guests, check the plugin diagnostic message and verify the API token can see `qemu` or `lxc` entries from:

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=opensecdash@pve!inventory=TOKEN_SECRET' \
  'https://pve.example.local:8006/api2/json/cluster/resources'
```
