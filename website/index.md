---
layout: home

hero:
  name: OpenSecDash
  text: A security dashboard for homelabs
  tagline: Turn homelab events, access logs, CrowdSec decisions, asset inventory, and update signals into correlated insights, notifications, and controlled actions.
  image:
    src: /favicon.svg
    alt: OpenSecDash logo
  actions:
    - theme: brand
      text: Get started
      link: /guide/getting-started/quickstart
    - theme: alt
      text: View on GitHub
      link: https://github.com/konkos1/OpenSecDash
    - theme: alt
      text: View on Docker-Hub
      link: https://hub.docker.com/r/konkos1/OpenSecDash
    - theme: alt
      text: Buy me a coffee
      link: https://www.buymeacoffee.com/konkos1
      

features:
  - icon: 🛡️
    title: Live security dashboard
    details: Arrange plugin-driven widgets for bans, geoblocks, access activity, assets, updates, insights, and historical trends.
  - icon: 🔎
    title: Investigate and correlate
    details: Filter Events and Access, save investigation views, and connect IPs, assets, paths, bans, geoblocks, and web probes.
  - icon: 🧭
    title: Insights engine
    details: Turn noisy traffic into deterministic findings with validated declarative rules—never remotely executed code.
  - icon: ⚡
    title: Controlled actions
    details: Ban or unban public IPs through CrowdSec LAPI with confirmations, permissions, dry-run simulation, and audit history.
  - icon: 🔔
    title: Useful notifications
    details: Send SMTP alerts for matching events, insights, offline assets, and plugin errors with cooldowns and digest aggregation.
  - icon: 🧩
    title: Plugin-first architecture
    details: Start with CrowdSec, Traefik, GeoIP, Proxmox Assets, JSON Assets, GeoBlock, MQTT, and extend from there.
  - icon: 📦
    title: Assets and updates
    details: Track systems and apps, link hosts to security events, check GitHub releases, and publish update states to Home Assistant.
  - icon: 👥
    title: Optional sign-in and roles
    details: Enable internal Viewer, Operator, and Admin accounts, personal preferences, saved views, and per-user dashboard layouts.
  - icon: 📱
    title: Responsive and installable
    details: Use every workflow on phones, tablets, and desktops, or install OpenSecDash as a PWA behind HTTPS.
---

## From signals to response

OpenSecDash brings the complete planned v1 workflow into one understandable, self-hosted UI:

```text
Datasources → Enrichment → Events → Insights → Dashboard / Explorer → Notifications / Actions
```

It is not a Grafana replacement. Instead of asking you to build a general-purpose observability stack, OpenSecDash focuses on security-oriented homelab workflows: IP-centric investigations, structured event filters, access-log correlation, deterministic insights, asset inventory, update signals, plugin health, notifications, and controlled CrowdSec actions.

![OpenSecDash dashboard](/assets/screenshots/dashboard.png)

## A closer look

Filter and save security investigations, inspect one IP with a click, and keep an eye on your apps and their updates:

![Security events with filters](/assets/screenshots/events.png)

![IP Explorer with actions and insights](/assets/screenshots/ip-explorer.png)

![Asset inventory and update checks](/assets/screenshots/assets.png)

## Docker is recommended

The recommended installation method is Docker Compose. It keeps Python dependencies, the database location, log handling, and file permissions reproducible.

```bash
cp docker/docker-compose.example.yml docker-compose.yml
docker compose up -d
```

OpenSecDash includes optional internal sign-in with Viewer, Operator, and Admin roles. It is disabled by default so existing deployments keep their current access model. Even with internal sign-in enabled, keep the dashboard internal, behind a VPN, or behind a trusted HTTPS reverse proxy.
