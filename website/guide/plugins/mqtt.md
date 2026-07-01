# MQTT to Home Assistant

The MQTT plugin publishes OpenSecDash asset update states to MQTT so Home Assistant can discover them as update entities.

It is useful when you want app update information from OpenSecDash to appear in Home Assistant dashboards or automations.

## What is published

For each publishable app asset, OpenSecDash publishes:

- Home Assistant MQTT discovery config
- current installed version
- latest known version
- release URL
- update title/name

An asset is publishable when it has:

- MQTT publishing enabled for that asset
- installed version
- latest version
- release URL

Asset data can come from any supported asset source, for example JSON Assets or Proxmox Assets.

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Enables MQTT export. |
| MQTT host | MQTT broker hostname or IP. |
| MQTT port | Broker port, usually `1883` for plain MQTT. |
| MQTT username | Optional broker username. Use a dedicated MQTT user if possible. |
| MQTT password | Optional broker password. |
| Home Assistant discovery prefix | Discovery prefix, usually `homeassistant`. |
| MQTT state topic prefix | Prefix for OpenSecDash state topics, default `opensecdash`. |
| Publish interval | `auto`, `0`, or a number of seconds. |

## Publish interval modes

| Value | Behavior |
| --- | --- |
| `auto` | Publish when an asset source or manual sync/check triggers MQTT export. |
| `0` | Manual publish only. |
| Number of seconds | Periodically publish all publishable assets at that interval. |

## Manual publishing

The Asset Explorer shows MQTT controls when MQTT is enabled and assets have enough metadata. Use **Publish MQTT now** to publish all currently publishable app update states immediately.

## Home Assistant

Home Assistant must have MQTT integration enabled and connected to the same broker. The discovery prefix in OpenSecDash must match Home Assistant's MQTT discovery prefix.

## Diagnostics

Diagnostics verifies whether the configured broker host/port is reachable. Publish failures are reported on the MQTT plugin diagnostic row.
