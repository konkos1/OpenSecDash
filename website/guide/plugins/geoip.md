# GeoIP Plugin

The GeoIP plugin enriches public IP events with:

- country
- city
- ASN
- ISP

Lookups are cached to reduce provider calls. Local, private, reserved, and otherwise non-public addresses are skipped.

## Display

Country, city, ASN, and ISP can be enabled as optional columns in Events and Access views.
