# ADR-009: OpenSecDash is "Live First"

> **Implementation status (2026-07-09):** Implemented.
> Datasource and periodic plugins are processed continuously by the plugin manager; the UI controls live vs. snapshot display.



Principle:

`All data sources are processed continuously.`

The application always works internally in real time.

The interface only decides:

```none
Show live
  or
show static snapshot
```


---
