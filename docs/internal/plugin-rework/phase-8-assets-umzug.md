# Phase 8: json_assets- und proxmox_assets-Domäne in die Plugins

**Status: UMGESETZT (2026-07-07).** Abweichungen/Präzisierungen gegenüber dem Plan:
- `grep -rnE "proxmox_assets|json_assets_import|json_assets_source|json_assets_updates" backend/app --include="*.py"` zeigt keine alten Service-Importe mehr; verbleibende `proxmox_assets`-Treffer sind Core-Asset-Explorer-Feature-Flags, Source-Labels und Locale-Keys, weil die `/assets*`-Seiten laut Designentscheidung Core bleiben.
- Die gewünschte Route-Smoke-Verifikation für deaktivierte Plugin-Routen wurde ohne `TestClient` über Router-Registrierung plus `plugin_enabled_guard` getestet; zusätzlich wurde ein Dev-Server-Smoke per `uvicorn`/`curl` ausgeführt.

**Ziel:** Die Import-/Sync-Logik der beiden Asset-Quellen-Plugins zieht in deren
Plugin-Verzeichnisse; plugin-spezifische Routen registrieren die Plugins selbst. Der
Asset Explorer (Seiten `/assets*`), das Asset-Model, das generische Lock-Framework und
der GitHub-Update-Check bleiben Core (Begründung: README, Designentscheidungen 6+7).

## Schritt 8.1: Umbenennung `json_assets_updates.py` → `asset_updates.py` (bleibt Core)

`app/services/json_assets_updates.py` → `app/services/asset_updates.py`
(reine Umbenennung; der Update-Check läuft über ALLE Assets mit Release-URL, unabhängig
von der Quelle — der alte Name war irreführend). Importstellen anpassen:

- `app/plugins/manager.py:24` (`refresh_asset_updates`)
- `app/api/pages.py:33` (`refresh_asset_update` — genutzt in `update_asset_metadata`, ~1428)
- `app/services/asset_actions.py`
- `tests/test_json_assets.py` (importiert `refresh_asset_update`/`refresh_asset_updates`;
  ggf. die Update-Check-Tests in eine eigene `tests/test_asset_updates.py` ausgliedern,
  damit die Datei-Zuordnung stimmt)

## Schritt 8.2: json_assets-Services ins Plugin

Ziel-Layout:

```none
plugins/json_assets/
  __init__.py
  plugin.py
  routes.py
  locales.py           (optional, Muster Phase 6; Settings-Locales konsolidieren)
  services/
    __init__.py
    importer.py        (ehem. app/services/json_assets_import.py)
    source.py          (ehem. app/services/json_assets_source.py)
    actions.py         (json-spezifische Action-Wrapper, siehe unten)
```

1. `json_assets_import.py` → `services/importer.py`, `json_assets_source.py` →
   `services/source.py` (Inhalte unverändert; sie importieren nur Core-Module).
2. `plugins/json_assets/plugin.py`: Importe auf relativ umstellen
   (`from .services.importer import import_json_assets`, `from .services.source import load_asset_source`).
3. **`app/services/asset_actions.py` aufteilen:**
   - BLEIBT Core: `run_asset_action`, `run_asset_metadata_action`, `asset_action_running`,
     `current_asset_action`, `AssetActionAlreadyRunning`, `export_publishable_asset_updates`,
     `refresh_asset_updates_action`, `publish_asset_updates_action` (alles generisch bzw.
     quellenunabhängig; `refresh_asset_updates` kommt ab 8.1 aus `asset_updates`).
   - ZIEHT UM nach `plugins/json_assets/services/actions.py`:
     `import_assets_inventory_action`, `import_assets_source_action` (importieren
     `run_asset_action`/`export_publishable_asset_updates` aus `app.services.asset_actions`
     und `import_json_assets`/`load_asset_source` relativ).
4. Routen umziehen nach `plugins/json_assets/routes.py`:
   - aus `app/api/pages.py`: `POST /assets/import-source` (~1467) — inklusive der
     Legacy-Setting-Fallback-Kette (`plugin.json_assets.source` → `plugin.assets.source`
     → `asset_source`), die gehört zur json_assets-Domäne.
   - aus `app/api/assets.py`: `POST /api/assets/import` und `POST /api/assets/import-source`
     (eigener `APIRouter(prefix="/api/assets")` im Plugin ist in Ordnung — Prefixe dürfen
     sich mit dem Core-Router überlappen, solange die Pfade disjunkt sind).
   - `GET /api/assets` (list_assets) und `POST /api/assets/refresh-updates` bleiben Core.
5. `web()`-Hook: nur `router=` (kein Nav-Item — der Assets-Nav-Eintrag bleibt Core über
   `asset_plugins_enabled`; kein Template).
   **Gating-Hinweis:** Der Router-Guard (Phase 4) übernimmt das bisherige
   `require_plugin_enabled(db, "json_assets")`; für die `/api/assets/*`-Routen ist das
   eine leichte Verhaltensänderung (bisher kein Plugin-Gate auf `/api/assets/import`).
   Das ist gewollt und zu dokumentieren: Import-Endpoints ohne aktives json_assets-Plugin
   → 404.

## Schritt 8.3: proxmox_assets-Service ins Plugin

```none
plugins/proxmox_assets/
  __init__.py
  plugin.py
  routes.py
  services/
    __init__.py
    sync.py            (ehem. app/services/proxmox_assets.py)
```

1. `app/services/proxmox_assets.py` → `services/sync.py` (unverändert; enthält
   `ProxmoxClient`, `sync_proxmox_assets`, `inspect_proxmox_guest_visibility`,
   `proxmox_visibility_message`, `parse_opensecdash_notes`).
2. `plugin.py`: Import relativ (`from .services.sync import ...`).
3. Route `POST /assets/proxmox-sync` (`assets_proxmox_sync_page`, pages.py ~1265) →
   `plugins/proxmox_assets/routes.py`. Sie nutzt `run_asset_action`,
   `export_publishable_asset_updates` (Core), `Diagnostic`-Model, Settings — alles per
   Core-Import verfügbar. Redirect-Ziele (`/assets?...proxmox_error=...`) bleiben gleich;
   die Assets-Seite (Core) liest `proxmox_error` weiterhin als Query-Parameter — der
   Parameter ist generisch genug (Fehleranzeige), KEINE Umbenennung nötig, aber im
   Zieldokument (ADR-Update, Phase 9) als bekannte Kopplung notieren.
4. `web()`-Hook: nur `router=`.

## Schritt 8.4: mqtt-Publish-Route ins mqtt-Plugin

`POST /assets/mqtt-publish` (`assets_mqtt_publish_page`, pages.py ~1300) →
`plugins/mqtt/routes.py`. Der Handler ruft nur Core-Funktionen
(`publish_asset_updates_action`) — der Umzug entfernt die mqtt-Enabled-Sonderprüfung aus
pages.py (übernimmt der Router-Guard). **Achtung:** Der Guard prüft
`plugin.mqtt-hass.enabled`; der Legacy-Fallback auf `plugin.mqtt.enabled` muss erhalten
bleiben → der Guard aus Phase 4 bekommt dafür KEINEN Sonderfall; stattdessen prüft die
Route selbst zusätzlich den Legacy-Key wie bisher, und das Plugin registriert die Route im
`ungated_router` (Mechanismus aus Phase 6) mit der bisherigen kompletten Prüfkette
(`require_assets_feature_enabled` + mqtt-hass/mqtt-Fallback). Die weiteren
Legacy-Lookups in pages.py (~1234 in `assets_page`, ~1391 in `asset_page`) bleiben, da
diese Seiten Core bleiben (Anzeige der MQTT-Buttons).

## Schritt 8.5: Core-Referenzen bereinigen und Tests

- `app/api/pages.py`: Importe `sync_proxmox_assets`, `import_assets_source_action`
  entfernen; `app/api/assets.py`: `import_assets_inventory_action`/`import_assets_source_action`
  entfernen; danach `grep -rn "proxmox_assets\|json_assets_import\|json_assets_source\|json_assets_updates" backend/app`
  → keine Treffer (außer ggf. Kommentare/`plugin.json_assets.`-Setting-Keys, die bleiben).
- `tests/test_proxmox_assets.py`: Import über conftest-Helfer
  (`sync = import_plugin_module("proxmox_assets", "services.sync")`), Monkeypatch-Ziele
  auf das Modulobjekt umstellen.
- `tests/test_json_assets.py`: Import-Teil auf
  `import_plugin_module("json_assets", "services.importer")` umstellen; Update-Check-Teile
  auf `app.services.asset_updates` (siehe 8.1).
- Route-Smoke-Tests: `POST /assets/proxmox-sync` → 404 bei deaktiviertem Plugin;
  `POST /api/assets/import-source` → 404 bei deaktiviertem json_assets.

## Akzeptanzkriterien

- [ ] `ls backend/app/services/` enthält keine `crowdsec_*`-, `proxmox_*`-, `json_assets_*`-Dateien
      mehr; `asset_updates.py` existiert.
- [ ] Gesamtsuite + Pyright grün.
- [ ] Live-Probe: Assets-Seite mit beiden Quellen-Plugins; Proxmox-Sync-Button (Fehlerfall
      zeigt `proxmox_error`-Banner), Import-from-Source, Refresh-Updates, MQTT-Publish;
      `OSD_PLUGIN_PROXMOX_ASSETS_DISABLED=true` blendet Proxmox überall aus, Assets-Feature
      bleibt über json_assets aktiv.
- [ ] Manager-Update-Loop (`_run_asset_update_tick`) läuft weiter (Diagnostics-Zeile
      `asset_updates` wird aktualisiert).
