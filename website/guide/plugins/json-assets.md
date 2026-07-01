# JSON Assets

The JSON Assets plugin imports systems and installed apps from a JSON source.

It is useful for hosts that are not discovered by another plugin, or for environments where a simple generated JSON file is easier than an API integration.

## Typical input

```json
{
  "last_update": "01.07.2026 12:00:01",
  "systems": [
    {
      "vmid": "100",
      "hostname": "edge-01",
      "type": "proxmox-lxc",
      "apps": [
        {
          "name": "traefik",
          "version": "v3.0.0",
          "url": "https://github.com/traefik/traefik/releases/latest"
        }
      ]
    }
  ]
}
```

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables the JSON Assets plugin and asset navigation/widgets. |
| Source type | Loads `assets.json` from a local file or URL. |
| Source | Path or URL to `assets.json`. Missing apps are marked inactive and kept for history. |
| Master for app values | Controls whether version/release URL are maintained in OpenSecDash or overwritten from `assets.json` for existing apps. |
| Inventory interval seconds | How often `assets.json` is reloaded automatically. Use `0` to disable automatic reloads. |

## Source behavior

JSON Assets and Proxmox Assets can run in parallel. The JSON importer generates stable source IDs and only marks its own imported systems and apps inactive.

## Update checks

For GitHub release checks, use a release URL like:

```text
https://github.com/owner/repo/releases/latest
```
