# ADR-031: Export Framework

> **Implementation status (2026-07-09):** Partially implemented.
> MQTT export is implemented as a plugin and can publish events/assets/update-related data. Additional export types remain planned.



V1:

* MQTT

Later, for example in V2:

* Webhook
* JSON Export
* REST Push


---

Plugin type:

`Export`


---

Input:

```none
Events
Insights
Actions
Assets
```


---

Output, for example:

`MQTT Topics`


---

Example:

```none
event.security.ban
↓
opensecdash/events/security/ban

asset.update_available
↓
opensecdash/assets/update

insight.wordpress_scan
↓
opensecdash/insights/wordpress_scan
```


---

## Settings:

for example:

```none
Broker
Port
Username
Password
Topic Prefix
```


---

Example:

`opensecdash`


---

Topics:

```none
opensecdash/events/#
opensecdash/assets/#
opensecdash/actions/#
opensecdash/insights/#
```


---

### Home Assistant Integration Plugin

Optional core plugin.


---

Function:

```none
Asset Update
↓
MQTT Discovery Entity
```


---

---

## Implementation notes (2026-07-09)

MQTT export is implemented as the current export plugin. Additional export targets such as webhook, JSON export, and REST push remain planned.

