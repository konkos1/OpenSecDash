# MQTT to Home Assistant

The MQTT plugin publishes OpenSecDash asset update states to MQTT so Home Assistant can discover them as update entities.

::: danger Protect MQTT credentials and publishing
Do not configure MQTT credentials or publish MQTT data unless OpenSecDash requires
either [internal sign-in](../configuration/authentication.md) or an external
authentication provider and is accessed exclusively through an HTTPS
[reverse proxy](../installation/reverse-proxy.md) with a browser-trusted certificate.
An unauthenticated dashboard allows every reachable client to trigger publishing or
change broker settings with OpenSecDash's MQTT access.
:::

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
| MQTT transport security | `TLS` verifies the broker certificate and hostname; `None` keeps an existing plain MQTT setup working but shows a warning. |
| Custom CA certificate file | Optional PEM CA file for a private broker CA. The system trust store is used when empty. |
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

Topics are namespaced per app: discovery configs are published to `<discovery prefix>/update/opensecdash_<app-slug>/config` and states to `<state topic prefix>/apps/<app-slug>/state`. The `opensecdash_` namespace in the discovery topic matters: for Home Assistant the discovery topic is the entity's identity, and a bare app slug like `nextcloud` would collide with any other update publisher on the same broker - a colliding retained config with a different `unique_id` can never create an entity, so the apps silently never appear.

::: tip Upgrading from versions before this namespace
Older versions (< v0.2.0) published discovery configs to `<discovery prefix>/update/<app-slug>/config` (without the `opensecdash_` namespace). Those retained configs stay on the broker until cleared. If leftover duplicate entities bother you, clear each old topic once with an empty retained payload, e.g. `mosquitto_pub -h <broker> -u <user> -P <pass> -t 'homeassistant/update/nextcloud/config' -r -n` - Home Assistant then removes the old entity.
:::

Each published app becomes a standard Home Assistant `update.*` entity (`device_class: firmware`), grouped under a single **OpenSecDash Assets** device. Home Assistant's MQTT Update integration reads the JSON state payload directly, so these attributes are available on every entity without any extra Home Assistant-side configuration:

| Attribute | Source |
| --- | --- |
| `installed_version` | The version OpenSecDash currently has on record for the app. |
| `latest_version` | The latest version OpenSecDash found (for example via a GitHub release check). |
| `release_url` | Link to the release notes/page. |
| `title` | The app name. |

## Automation: notify when an update is available

A common use case is a typical homelab setup: apps run on Proxmox, [Proxmox Assets](proxmox-assets.md) or [JSON Assets](json-assets.md) keeps their installed/latest versions current in OpenSecDash, the MQTT plugin publishes them as `update.*` entities, and a Home Assistant automation notifies you when one of them has an update pending.

The automation below reacts to two situations:

- **Home Assistant restarts**: any `update.*` entity that is already showing a pending update (from a retained MQTT state) gets one notification per app, instead of staying silent until its state happens to change again.
- **An update newly becomes available while Home Assistant is running**: a single `state_changed` event for that entity triggers one notification.

A `timer` helper is used as a cooldown so a Home Assistant restart doesn't immediately fire a burst of near-duplicate notifications while every retained MQTT state arrives. Create it once under **Settings → Devices & services → Helpers → Create Helper → Timer**, for example named `opensecdash_update_notify_cooldown` with a duration of `00:05:00`.

Since the trigger/condition logic only looks for entities whose ID starts with `update.`, this automation isn't limited to OpenSecDash - it also picks up update entities from any other integration (HACS, add-ons, ESPHome, ...). If you want notifications only for apps OpenSecDash publishes, add `and device_attr(repeat.item, 'name') == 'OpenSecDash Assets'` (or the equivalent for `trigger.event.data.entity_id` in the second branch) to the template conditions.

The `notify.notify` action below is a placeholder for whichever notification target you actually use (a mobile app notify service, a Telegram bot, your own notify script, ...) - replace it with yours.

<details>
<summary>Home Assistant automation YAML</summary>

```yaml
alias: OpenSecDash update notifications
description: Notify once per app when an update.* entity has a pending update.
triggers:
  - trigger: homeassistant
    event: start
    id: RESTART
  - trigger: event
    event_type: state_changed
    id: STATE_CHANGE
conditions:
  - condition: or
    conditions:
      - condition: trigger
        id:
          - RESTART
      - alias: A tracked update entity just switched from no update to update available
        condition: template
        value_template: >-
          {% set eid = trigger.event.data.entity_id %}
          {% set old_state = trigger.event.data.old_state %}
          {% set new_state = trigger.event.data.new_state %}
          {{ eid is defined
             and eid.startswith('update.')
             and old_state is not none
             and new_state is not none
             and old_state.state | lower == 'off'
             and new_state.state | lower == 'on' }}
  - condition: state
    entity_id: timer.opensecdash_update_notify_cooldown
    state: idle
    alias: Skip while the post-restart cooldown timer is still running
actions:
  - choose:
      - conditions:
          - condition: trigger
            id:
              - RESTART
        sequence:
          - action: timer.start
            target:
              entity_id: timer.opensecdash_update_notify_cooldown
            data: {}
          - alias: Notify once for every update entity that is already pending
            repeat:
              for_each: >-
                {{ states.update
                   | selectattr('state', 'eq', 'on')
                   | rejectattr('attributes.installed_version', 'in', ['', 'unknown', 'unavailable', none])
                   | rejectattr('attributes.latest_version', 'in', ['', 'unknown', 'unavailable', none])
                   | map(attribute='entity_id')
                   | list }}
              sequence:
                - action: notify.notify
                  data:
                    title: "🚀 Update available"
                    message: >-
                      {{ state_attr(repeat.item, 'friendly_name') or repeat.item }}:
                      {{ state_attr(repeat.item, 'installed_version') | default('unknown') }}
                      → {{ state_attr(repeat.item, 'latest_version') | default('unknown') }}
                      {{ state_attr(repeat.item, 'release_url') | default('') }}
        alias: Triggered by a Home Assistant restart
      - conditions:
          - condition: trigger
            id:
              - STATE_CHANGE
        sequence:
          - action: notify.notify
            data:
              title: "🆕 New update available"
              message: >-
                {% set eid = trigger.event.data.entity_id %}
                {{ state_attr(eid, 'friendly_name') or eid }}:
                {{ state_attr(eid, 'installed_version') | default('unknown') }}
                → {{ state_attr(eid, 'latest_version') | default('unknown') }}
                {{ state_attr(eid, 'release_url') | default('') }}
        alias: Triggered by an entity state change
mode: queued
max: 10
```

</details>

## Diagnostics

Diagnostics verifies whether the configured broker host/port is reachable. In TLS
mode it completes a certificate- and hostname-verified TLS handshake. Plain MQTT is
reported as a warning. Publish failures are reported on the MQTT plugin diagnostic
row.
