# Mobile & PWA

OpenSecDash is built to be used on phones and tablets, not just on a desktop
browser. Every function is available on a small screen, and the app can be
installed like a native app.

## Responsive UI

The interface adapts to the screen you are on:

- On phones and tablets the navigation collapses into a burger menu (☰), and the
  dashboard widgets stack into a single column.
- Wide tables (events, rollups, assets) turn into labelled cards on narrow
  screens, so you never have to scroll a table sideways to read a row.
- On the **Events** page, the live/snapshot switch stays visible. In live mode a
  🟢 LIVE badge is shown; in snapshot mode you see ⚪ SNAPSHOT with the time the
  data is from.

## Install as an app (PWA)

OpenSecDash ships a web app manifest and a service worker, so most modern
browsers offer to install it. Once installed it opens in its own window without
the browser address bar.

- **Android / Chrome:** open the browser menu and choose **Install app** (or
  **Add to home screen**).
- **iPhone / iPad (Safari):** tap the share button and choose **Add to Home
  Screen**.
- **Desktop Chrome / Edge:** click the install icon on the right side of the
  address bar.

## Requirements

Installability and the service worker only work over **HTTPS**, or on
`localhost`. This is a browser rule, not an OpenSecDash setting.

For a homelab this means:

- Behind a reverse proxy with TLS — as described in the
  [reverse proxy guide](../installation/reverse-proxy.md) — installation works
  automatically.
- Opening the app over plain `http://<lan-ip>` will work for viewing, but the
  browser will not offer to install it.

## No offline mode

OpenSecDash is a live security dashboard, so it does not work offline. When there
is no connection, opening the app shows a short offline notice page instead of a
browser error. Live data is only available while connected.

This is a deliberate decision: the app never shows cached — and therefore
possibly outdated — security data. Stale bans or events could be mistaken for the
current state, so nothing is cached beyond that single offline notice page.
