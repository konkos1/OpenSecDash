# ADR-034: Asset Inventory & Update Detection

> **Implementation status (2026-07-09):** Implemented.
> JSON Assets, Proxmox Assets, GitHub release checks, release path derivation, caching/rate-limit-conscious checks, and source-independent update detection exist.



## Intervals

JSON Assets has a source-specific interval setting:

- `plugin.json_assets.inventory_interval`: automatic reload of `assets.json` in seconds. `0` disables automatic import.

Asset update checks are core settings and apply to all asset sources, e.g. JSON Assets and Proxmox Assets:

- `asset_updates.github_interval`: automatic GitHub release check in seconds. `0` disables automatic checking.
- `asset_updates.github_token`: optional GitHub API token for higher rate limits.

The asset update check only runs if at least one supported asset source is active.


## Data source

V1 directly supports the format for `assets.json`:

```json
{
  "last_update": "21.06.2026 12:40:02",
  "systems": [
    {
      "vmid": "...",
      "type": "...",
      "hostname": "...",
      "apps": [
        {
          "name": "...",
          "version": "...",
          "url": ""
        },
        {
          "name": "...",
          "version": "...",
          "url": "https://github.com/.../releases/latest"
        },
        ...
      ]
    },
    ...
```


---

## Asset hierarchy

Not:

`Apps`

but:

```none
System
 ├─ App
 ├─ App
 └─ App
```

Example:

```none
traefik (LXC 100)

 ├─ Debian 12
 ├─ Traefik
 ├─ CrowdSec
 ├─ CrowdSec Bouncer
 ├─ GeoBlock
 └─ TorBlockRedirect
```


---

## Asset Explorer

Make it two-level.

### System view

for example:

```none
traefik

Type:
LXC

VMID:
100

Apps:
7

Updates:
2
```


---

### Detail view

for example:

```none
Traefik

Installed:
v3.7.5

Current:
v3.8.0

Update available
```


---

# Update Detection

OpenSecDash checks itself.

Because assets.json already provides, for example:

```json
{
  "name": "traefik",
  "version": "v3.7.5",
  "url": "https://github.com/traefik/traefik/releases/latest"
}
```

That is completely sufficient.


---

## Workflow

```none
assets.json
↓
Asset Import
↓
release_url present?
↓
Check GitHub Release
↓
Compare version
↓
Store update status
```


---

OpenSecDash thus becomes the central source.


---

# Release Path

`release_path` should be derived automatically.

Example:

From:

`https://github.com/traefik/traefik/releases/latest`

internally becomes:

`traefik/traefik`

stored.


---

This can then use, for example:

`GitHub API`

instead of evaluating redirects.


---

# Caching

Important for GitHub.

Not:

`check every 5 minutes`


---

But:

```none
assets.json
e.g. every 5 minutes

GitHub Release Check
once per hour
or (configurable)
every 24 hours
```

Otherwise, sooner or later you run into rate limits.


---

# Asset model

At least:

```none
system_id
hostname
vmid
type
```


---

This can automatically become:

```none
Assets

LXC: 12
VMs: 5
Docker Hosts: 3
```


---
