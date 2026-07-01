# Proxmox Assets Plugin

The Proxmox Assets plugin imports Proxmox nodes, VMs, and LXCs into OpenSecDash. It can also create application assets from a hidden metadata block in Proxmox guest notes.

Apps Inventory JSON and Proxmox Assets can run in parallel. Each source gets stable source IDs and only manages its own imported assets.

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

## Plugin settings

- Proxmox API URL, e.g. `https://pve.example.local:8006`
- API token ID, e.g. `opensecdash@pve!inventory`
- API token secret
- Verify TLS certificate
- Poll interval, default `300` seconds

Disable TLS verification only for trusted self-signed homelab certificates.

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
-->
```

Supported fields for v1:

- `apps[].name` required
- `apps[].update_check.type` optional, currently `github_release`
- `apps[].update_check.repo` optional, e.g. `traefik/traefik`

Do not put URLs or installed versions into Proxmox notes for v1. Those fields should be edited in OpenSecDash and should not be overwritten by the Proxmox sync.

## Source IDs

The plugin uses stable source IDs such as:

```text
proxmox:pve.example.local:8006:node:pve1
proxmox:pve.example.local:8006:guest:pve1:104
proxmox:pve.example.local:8006:guest:pve1:104:app:traefik
```

In the Asset Explorer, Proxmox guests display VMIDs as `node:vmid` (for example `pve1:104`). This avoids collisions with existing Apps Inventory JSON systems that may already use plain VMIDs such as `104`.

If an app name in the notes block changes, OpenSecDash treats it as a new app and marks the old Proxmox-imported app inactive.

## Troubleshooting

### zsh and API tokens

Proxmox token IDs contain `!`, for example `opensecdash@pve!inventory`. In zsh, wrap curl headers in single quotes:

```bash
curl -k \
  -H 'Authorization: PVEAPIToken=opensecdash@pve!inventory=TOKEN_SECRET' \
  'https://pve.example.local:8006/api2/json/cluster/resources'
```

### Nodes visible, guests missing

If `/cluster/resources` returns only `type=node` entries, the token can authenticate but cannot see VMs/LXCs. Check `VM.Audit` permissions and propagation.
