# Apps Inventory JSON

The Apps Inventory plugin imports systems and installed apps from a JSON source.

It is useful for hosts that are not discovered by another plugin, or for environments where a simple generated JSON file is easier than an API integration.

## Typical input

```json
{
  "systems": [
    {
      "vmid": "100",
      "hostname": "edge-01",
      "type": "proxmox-lxc",
      "apps": [
        {
          "name": "traefik",
          "version": "v3.0.0",
          "release_url": "https://github.com/traefik/traefik/releases/latest"
        }
      ]
    }
  ]
}
```

## Source behavior

Apps Inventory and Proxmox Assets can run in parallel. The JSON importer generates stable source IDs and only marks its own imported apps inactive.

## Update checks

For GitHub release checks, use a release URL like:

```text
https://github.com/owner/repo/releases/latest
```
