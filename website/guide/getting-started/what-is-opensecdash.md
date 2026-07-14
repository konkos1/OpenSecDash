# What is OpenSecDash?

OpenSecDash is an open-source security dashboard for homelabs.

It collects security events, access logs, asset information, and update signals from common homelab tools. Its Insights engine turns noisy events into useful context such as web-probe patterns, bans, geoblocks, and short-window correlations. Notifications surface relevant changes, while centrally validated Actions provide a controlled way to respond. The result is one practical UI for questions such as:

- Who is knocking on my services?
- Which requests were blocked or failed?
- What happened around a specific IP address?
- Which events are meaningful security insights rather than background noise?
- Which apps are installed, and which need updates?
- Are my plugins and datasources healthy?
- Which findings should notify me instead of waiting for a dashboard visit?
- Can I safely test or execute a CrowdSec ban from the investigation view?

## What it is not

OpenSecDash is not a Grafana replacement. Grafana is excellent for metrics, time series, custom dashboards, and general observability. OpenSecDash focuses on security-oriented context and investigation workflows.

## Core concepts

- **Events**: normalized security, access, and action records.
- **Insights engine**: declarative and local correlation rules that turn event patterns into actionable hints.
- **Dashboard**: a customizable widget overview to which enabled plugins can contribute validated counters and lists.
- **Filters and saved views**: shareable and reusable investigations across Events and Access.
- **IP Explorer**: investigation view for one IP address.
- **Assets**: systems and apps imported from inventory sources.
- **Notifications**: cooldown-aware SMTP alerts and delivery history for events, insights, offline assets, and plugin errors.
- **Actions**: confirmed, permission-aware, audited responses such as CrowdSec ban and unban, with dry-run simulation enabled by default.
- **Plugins**: integrations such as CrowdSec, Proxmox, Traefik, GeoIP, and MQTT.
- **Diagnostics**: runtime state for plugins, datasources, actions, and migrations.

## Complete planned v1 workflow

The current feature set covers the workflow planned for the first stable v1 release: collection, enrichment, investigation, correlation, notifications, and controlled response. Intentionally later work—such as additional notification channels, a background action queue, advanced Level 2 correlation chains, and full offline operation—is not required for that v1 scope.

OpenSecDash remains deliberately focused on understandable homelab security rather than trying to become a general SIEM or automation platform.
