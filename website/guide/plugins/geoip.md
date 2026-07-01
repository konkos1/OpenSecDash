# GeoIP Plugin

The GeoIP plugin enriches public IP events with:

- country
- city
- ASN
- ISP

Lookups are cached to reduce provider calls. Local, private, reserved, and otherwise non-public addresses are skipped.

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Adds country, city, ASN, and ISP to new public-IP events when the producer did not already provide them. |
| Provider | GeoIP provider. The bundled provider is `ip-api.com`. |
| Cache TTL days | How long successful lookups stay cached before being refreshed. |
| Timeout seconds | HTTP timeout for one GeoIP provider request. |

GeoIP is only useful when at least one event-producing plugin is enabled. Diagnostics shows it as disabled when there are no event datasources to enrich.

## Display

Country, city, ASN, and ISP can be enabled as optional columns in Events and Access views.
