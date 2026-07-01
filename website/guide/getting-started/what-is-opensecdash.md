# What is OpenSecDash?

OpenSecDash is an open-source security dashboard for homelabs.

It collects security events, access logs, asset information, and update signals from common homelab tools and turns them into a simple web UI for practical questions:

- Who is knocking on my services?
- Which requests were blocked or failed?
- What happened around a specific IP address?
- Which apps are installed, and which need updates?
- Are my plugins and datasources healthy?

## What it is not

OpenSecDash is not a Grafana replacement. Grafana is excellent for metrics, time series, custom dashboards, and general observability. OpenSecDash focuses on security-oriented context and investigation workflows.

## Core concepts

- **Events**: normalized security, access, and action records.
- **IP Explorer**: investigation view for one IP address.
- **Assets**: systems and apps imported from inventory sources.
- **Plugins**: integrations such as CrowdSec, Proxmox, Traefik, GeoIP, and MQTT.
- **Diagnostics**: runtime state for plugins, datasources, actions, and migrations.
