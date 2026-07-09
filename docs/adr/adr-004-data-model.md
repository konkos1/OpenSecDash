# ADR-004: Data Model

> **Implementation status (2026-07-09):** Implemented.
> The minimal event model exists and has been expanded with structured fields, event_time/created_at, plugin/source identifiers, severity, GeoIP fields, raw_data, and retention_class.



## Event

Minimal model:

```python
Event
```

Fields:

```python
id
timestamp
source
type
ip
country
data
```


---

Example:

```json
{
  "type": "access",
  "source": "traefik",
  "ip": "1.2.3.4",
  "data": {
    "status": 404,
    "path": "/wp-login.php"
  }
}
```


---
