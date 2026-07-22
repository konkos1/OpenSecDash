# Proxmox Assets Plugin

The Proxmox Assets plugin imports Proxmox nodes, VMs, and LXCs into OpenSecDash. It can also create application assets from a hidden metadata block in Proxmox guest notes.

::: danger Protect Proxmox credentials and inventory
Do not configure or use the Proxmox integration unless OpenSecDash requires either
[internal sign-in](../configuration/authentication.md) or an external authentication
provider and is accessed exclusively through an HTTPS
[reverse proxy](../installation/reverse-proxy.md) with a browser-trusted certificate.
Even a read-only Proxmox token exposes sensitive infrastructure inventory and must not
be placed behind an unauthenticated or unencrypted dashboard.
:::

## Recommended Proxmox permissions

Create a dedicated read-only API token, for example:

- User: `opensecdash@pve`
- Token: `inventory`
- Token ID in OpenSecDash: `opensecdash@pve!inventory`

The token should only need read-only permissions such as:

- `VM.Audit` for reading VM/LXC metadata
- `Sys.Audit` for reading node/cluster metadata

OpenSecDash reads guests primarily via `/cluster/resources` and filters `qemu`/`lxc` resources client-side. It falls back to per-node QEMU/LXC endpoints only when cluster resources do not expose guests.

If the plugin imports nodes but no guests, check that the token can see guests in Proxmox and that `VM.Audit` is assigned with propagation to the relevant VMs/LXCs or pool.

Do not give write/admin permissions. OpenSecDash does not need Guest Exec, SSH, or Proxmox write access for this plugin.

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables Proxmox node/guest import and optional app declarations from Proxmox notes. |
| Proxmox API URL | Base URL, for example `https://pve.example.local:8006`. |
| API token ID | Token ID, for example `opensecdash@pve!inventory`. |
| API token secret | Secret value for the API token. |
| Verify TLS certificate | Enables certificate verification. Disable only for trusted self-signed homelab certificates. |
| Poll interval seconds | How often Proxmox assets are synchronized. Default: `300`. |

Disable TLS verification only for trusted self-signed homelab certificates.
OpenSecDash rejects API URLs with embedded credentials, query strings, fragments, or
invalid schemes/ports. API-token requests never follow redirects and do not use proxy
environment variables. TLS certificate and hostname verification is enabled by
default; disabling it keeps existing self-signed setups working but produces a
Diagnostics warning.

## Notes metadata

The plugin always imports Proxmox nodes and guests as systems, even without notes metadata.

Apps are optional and can be declared in the Proxmox guest notes using an HTML comment. The comment is editable in Proxmox, but not shown in the rendered notes view.

Example:

```html
Reverse proxy container.

<!-- opensecdash
apps:
  - name: Traefik
    update_check:
      type: github_release
      repo: traefik/traefik
  - name: CrowdSec
    update_check:
      type: github_release
      repo: crowdsecurity/crowdsec
  - name: GeoBlock
  - name: Tor Block
-->
```

Currently supported fields:

- `apps[].name` required
- `apps[].update_check.type` optional, currently `github_release`
- `apps[].update_check.repo` optional, e.g. `traefik/traefik`

Do not put URLs or installed versions into Proxmox notes. Those fields should be edited in OpenSecDash and should not be overwritten by the Proxmox sync.

## Source IDs

The plugin uses internally stable source IDs such as:

```text
proxmox:pve.example.local:8006:node:pve1
proxmox:pve.example.local:8006:guest:pve1:104
proxmox:pve.example.local:8006:guest:pve1:104:app:traefik
```

In the Asset Explorer, Proxmox guests display VMIDs as `node:vmid` (for example `pve1:104`). This avoids collisions with existing JSON Assets systems that may already use plain VMIDs such as `104`.

If an app name in the notes block changes, OpenSecDash treats it as a new app and marks the old Proxmox-imported app inactive.

## Source behavior

JSON Assets and Proxmox Assets can run in parallel. The Proxmox importer generates stable source IDs and only marks its own imported systems and apps inactive.

## Troubleshooting

### zsh and API tokens

Proxmox token IDs contain `!`, for example `opensecdash@pve!inventory`. In zsh, wrap curl headers in single quotes, to check manually if your token settings are correct:

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=opensecdash@pve!inventory=TOKEN_SECRET' \
  'https://pve.example.local:8006/api2/json/cluster/resources'
```
Expected response: List of Proxmox cluster(s) and their respective guest(s).

### Nodes visible, guests missing

If `/cluster/resources` returns only `type=node` entries, the token can authenticate but cannot see VMs/LXCs. Check `VM.Audit` permissions and propagation.
