# Phase 7: Traefik-Access-Seite ins Plugin

**Status: UMGESETZT (2026-07-07).** Abweichungen/Präzisierungen gegenüber dem Plan:
- `access.host` und `access.method` bleiben bewusst in den Core-Locales, weil die geteilte Events-/Access-Spaltenauswahl sie auch in Core-Templates (`events.html`, `asset.html`) verwenden kann.
- `grep -rn "traefik" backend/app --include="*.py"` zeigt weiterhin Dashboard-Metriken/-Links in `pages.py`; das ist keine Access-Seiten-Ownership mehr und entspricht Schritt 7.3 (Dashboard-Verweise prüfen, keine Änderung nötig).

**Ziel:** Die `/access`-Seite (Traefik-Zugriffslog-Ansicht) zieht nach dem in Phase 6
etablierten Muster ins `traefik_log`-Plugin. Diese Phase ist bewusst klein — sie
wiederholt das CrowdSec-Muster und dient als Nachweis, dass die Registrierungspunkte
generisch tragen.

## Umfang

Aus `app/api/pages.py` ziehen um:

- `GET /access` (`access_page`, Zeilen ~918-969)
- `POST /access/columns` (`save_access_columns`, Zeilen ~971-984)
- Template `app/templates/access.html` → `plugins/traefik_log/templates/access.html`
  (Referenz: `"traefik_log/access.html"`)
- Nav-Eintrag `nav.access` aus `base.html` (Desktop ~50, Mobile ~64) → `PluginNavItem`
- Locales: `access.*`-Keys + `nav.access` aus `app/locales/{en,de}.py` →
  `plugins/traefik_log/locales.py` (Settings-Locales aus `plugin.py` dort konsolidieren).
  Vorsicht: Keys, die auch `events.html`/Dashboard nutzen (z.B. Spalten-Labels, die beide
  Tabellen teilen), per Grep identifizieren und im Core belassen.

## Schritt 7.1: Geteilte Tabellen-Helfer nach `app/web/tables.py`

`access_page` teilt sich Helfer mit der (Core bleibenden) Events-Seite. Diese Helfer aus
`pages.py` nach `app/web/tables.py` verschieben, damit das Plugin sie importieren kann,
ohne `app.api.pages` zu importieren (Import-Kette `plugin routes → pages` wäre bei
App-Start zwar technisch möglich, aber ein Wartungs-Antipattern):

- `table_columns`, `save_table_columns`, `column_redirect_url`, `DEFAULT_ACCESS_COLUMNS`
  (+ `DEFAULT_EVENTS_COLUMNS`, per Grep den echten Konstantennamen prüfen)
- `asset_links_for_events`
- `clean_filter_value`, `clean_url_value`
- `utc_search_terms_for_ui_time`
- `parse_snapshot_before`, `today_start`, `today_hour_range` (falls nur `today_start`
  gebraucht wird, trotzdem die zusammengehörige Gruppe verschieben)
- `save_setting` (wird von `save_table_columns` genutzt — prüfen und ggf. mitverschieben
  oder nach `app/web/`-Ebene ziehen)

`pages.py` importiert diese Namen fortan aus `app.web.tables` (Aufrufstellen unverändert).

## Schritt 7.2: Plugin-Routen

`plugins/traefik_log/routes.py` mit beiden Routen 1:1; Anpassungen wie in Phase 6:
`require_plugin_enabled`-Aufrufe entfernen (Router-Guard), `render` aus `app.web.render`,
Helfer aus `app.web.tables`, `apply_event_filters`/`tokenize_search_expression` aus
`app.services.events` (bleibt Core), `get_setting_value` aus `app.core.template_context`.

Der Setting-Key `ui.access.visible_columns` und der Plugin-Setting-Key
`plugin.traefik_log.hide_local_ips_default` bleiben unverändert.

`plugin.py` bekommt den `web()`-Hook:

```python
def web(self):
    from pathlib import Path

    from app.plugins.web import PluginNavItem, PluginWebRegistration

    from .routes import router

    return PluginWebRegistration(
        router=router,
        templates_dir=Path(__file__).parent / "templates",
        nav_items=(PluginNavItem(label_key="nav.access", href="/access", active_prefix="/access"),),
    )
```

Kein `ungated_router`, kein `ip_page_panels`.

## Schritt 7.3: Dashboard-Verweise prüfen

Das Dashboard verlinkt auf `/events?...` (nicht `/access`) für Access-Widgets
(pages.py ~648) — keine Änderung nötig. Trotzdem `grep -rn '"/access' backend/app`
laufen lassen: Alle verbleibenden Links müssen aus Core-Templates kommen, die bei
deaktiviertem Plugin ohnehin nicht rendern (Nav ist ab jetzt plugin-registriert).

## Akzeptanzkriterien

- [ ] `grep -rn "traefik" backend/app --include="*.py"` → keine seitenbezogenen Treffer
      mehr (erlaubt bleiben: keine; die Legacy-Lookups betreffen nur mqtt).
- [ ] `grep -rn "access" backend/app/locales/` → nur noch Keys, die Core-Seiten nutzen.
- [ ] Gesamtsuite + Pyright grün.
- [ ] Live-Probe: `/access` mit aktiviertem traefik_log rendert mit Filtern,
      Spalten-Auswahl (POST /access/columns) und Suche; deaktiviert → 404 + kein
      Nav-Eintrag; `OSD_PLUGIN_TRAEFIK_LOG_DISABLED=true` → ebenso; beide Sprachen geprüft.
