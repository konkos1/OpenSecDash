---
layout: home

hero:
  name: OpenSecDash
  text: A security dashboard for homelabs
  tagline: Turn reverse proxy logs, CrowdSec decisions, Proxmox inventory, app update signals, and heuristic insights into a practical live-first security UI.
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
    title: Security-first homelab visibility
    details: See access logs, security events, blocked requests, IP context, and CrowdSec actions in one focused dashboard.
  - icon: 🔎
    title: Insights engine
    details: "Surface useful security context from noisy events: web probes, bans, geoblocks, correlations, and remotely updated declarative rules."
  - icon: 🧭
    title: IP Explorer
    details: Investigate what happened around a specific IP address and correlate access, block, ban, and insight activity.
  - icon: 🧩
    title: Plugin-based
    details: Start with CrowdSec, Traefik, GeoIP, Proxmox Assets, JSON Assets, MQTT, and extend from there.
  - icon: 📦
    title: Assets and updates
    details: Track apps running in your homelab, check GitHub releases, and keep imported sources separate and safe.
  - icon: 🏠
    title: Built for homelabs
    details: Docker-first deployment, responsive UI, sensible defaults, and no need to build a full observability stack first.
  - icon: 🧰
    title: Debuggable
    details: Diagnostics and debug ZIP exports help understand plugin health, datasource state, and recent actions.
---

## Live-first, not Grafana-shaped

OpenSecDash is not a Grafana replacement. It focuses on security-oriented context: IP-centric investigations, event taxonomy, access-log correlation, the Insights engine, asset inventory, update signals, plugin health, and controlled actions such as CrowdSec ban/unban.

![OpenSecDash dashboard](/assets/screenshots/dashboard.png)

## A closer look

Filter security events, investigate a single IP with one click, and keep an eye on your apps and their updates:

![Security events with filters](/assets/screenshots/events.png)

![IP Explorer with actions and insights](/assets/screenshots/ip-explorer.png)

![Asset inventory and update checks](/assets/screenshots/assets.png)

## Docker is recommended

The recommended installation method is Docker Compose. It keeps Python dependencies, the database location, log handling, and file permissions reproducible.

```bash
cp docker/docker-compose.example.yml docker-compose.yml
docker compose up -d
```

OpenSecDash does not include built-in authentication yet. Keep it internal, behind a VPN, or behind a trusted auth reverse proxy.
