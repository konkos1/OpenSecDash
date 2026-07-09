# ADR-039: Event Storage Strategy

> **Implementation status (2026-07-09):** Implemented.
> Frequently queried fields are stored as structured columns, plugin-specific data stays in JSON, and indexes support events/search/IP Explorer/rollups.



Frequently needed fields:

```none
id
timestamp

event_type

ip
country
asn

hostname

status_code

path

source

data_json
```


---

Special data:

```json
{
  "scenario": "...",
  "ban_duration": "4h"
}
```

continues to go into JSON.


---

This makes:

* Search 
* Filters 
* Heatmaps 
* Top lists 
* IP Explorer

very fast.


---
