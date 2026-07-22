# GeoIP Plugin

The GeoIP plugin enriches public IP events with:

- country
- city
- ASN
- ISP

Lookups are cached to reduce provider calls. Local, private, reserved, and otherwise non-public addresses are skipped.

::: warning Remote GeoIP is disabled by default
The bundled provider is the public `ip-api.com` free endpoint. For every uncached
public IP, OpenSecDash sends that IP to ip-api.com over unencrypted HTTP. Successful
lookups are cached for the configured TTL (30 days by default); failures are cached
for one hour. Private, local, reserved, and otherwise non-public addresses are never
sent.

Enable this integration only after accepting that data flow and transport. An active
configuration remains active after an upgrade and is shown as a warning in Settings
and Diagnostics.
:::

## Settings

| Setting | What it does |
| --- | --- |
| Enabled | Adds country, city, ASN, and ISP to new public-IP events when the producer did not already provide them. Disabled by default. |
| Provider | GeoIP provider. The bundled provider is `ip-api.com`. |
| Cache TTL days | How long successful lookups stay cached before being refreshed. |
| Timeout seconds | HTTP timeout for one GeoIP provider request. |

GeoIP is only useful when at least one event-producing plugin is enabled. Diagnostics shows it as disabled when there are no event datasources to enrich.

## Display

Country, city, ASN, and ISP can be enabled as optional columns in Events and Access views.
