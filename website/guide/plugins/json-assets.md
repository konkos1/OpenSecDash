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

## URL and inventory limits

URL sources accept HTTP and HTTPS, including private RFC1918 and IPv6 ULA addresses
used by homelab services. URL credentials, localhost/loopback, link-local, metadata,
unspecified, multicast, and reserved targets are rejected. DNS answers and each of at
most three redirects are validated before use. OpenSecDash does not use proxy
environment variables for this fetch. Because DNS is resolved again by the HTTP
library when connecting, DNS rebinding remains a documented residual risk.

URL responses are streamed and limited to 10 MiB in both compressed and unpacked
form. A declared oversized `Content-Length` is rejected before download. The same
semantic limits apply to URL, file, and `/api/assets/import` input: JSON depth 20,
10,000 systems, 1,000 apps per system, and 2,048 characters per field. A rejected
inventory is not partially imported.

## Source behavior

JSON Assets and Proxmox Assets can run in parallel. The JSON importer generates stable source IDs and only marks its own imported systems and apps inactive.

## Update checks

For GitHub release checks, use a release URL like:

```text
https://github.com/owner/repo/releases/latest
```
