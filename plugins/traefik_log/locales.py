# All Traefik Access Log translations (settings labels, access page, nav).
# Registered globally at discovery as a fallback below core LOCALES, so
# t()/translate() resolve them from any template. Core strings win on collision.
LOCALES = {
    "en": {
        "nav.access": "Access",
        # Settings
        "traefik_log.settings.enabled": "Traefik log enabled",
        "traefik_log.settings.enabled.help": "Continuously watches the Traefik JSON access log and imports new lines as access events.",
        "traefik_log.settings.log_path": "Traefik access log path",
        "traefik_log.settings.log_path.help": "Path to the Traefik JSON access.log. Fields are parsed like the proven traefik-logs.sh script.",
        "traefik_log.settings.poll_interval": "Traefik poll interval seconds",
        "traefik_log.settings.poll_interval.help": "How often the file is checked for appended lines and rotation.",
        "traefik_log.settings.hide_local_ips_default": "Hide local IPs by default",
        "traefik_log.settings.hide_local_ips_default.help": "Starts the Access page with local/private IPs hidden unless the filter is changed manually.",
        "common.yes": "Yes",
        "common.no": "No",
        # Page
        "access.title": "Access",
        "access.search_placeholder": "Search access",
        "access.search_help": "Searches access fields such as time, IP, host, method, HTTP status, path, and JSON/raw data. Supports &&, || and parentheses, e.g. 2026-06-20 && /.env or (api && 404).",
        "access.no_events": "No access events",
        "table.limit.access": "Shows the newest 200 matching access events.",
    },
    "de": {
        "nav.access": "Access",
        # Settings
        "traefik_log.settings.enabled": "Traefik Log aktiviert",
        "traefik_log.settings.enabled.help": "Überwacht fortlaufend das Traefik JSON access.log und importiert neue Zeilen als Access-Events.",
        "traefik_log.settings.log_path": "Traefik Access-Log-Pfad",
        "traefik_log.settings.log_path.help": "Pfad zum Traefik JSON access.log. Die Felder werden wie im erprobten traefik-logs.sh geparst.",
        "traefik_log.settings.poll_interval": "Traefik Prüfintervall in Sekunden",
        "traefik_log.settings.poll_interval.help": "Wie oft die Datei auf neue Zeilen und Rotation geprüft wird.",
        "traefik_log.settings.hide_local_ips_default": "Lokale IPs standardmäßig ausblenden",
        "traefik_log.settings.hide_local_ips_default.help": "Öffnet die Access-Seite standardmäßig mit ausgeblendeten lokalen/privaten IPs, solange der Filter nicht manuell geändert wird.",
        "common.yes": "Ja",
        "common.no": "Nein",
        # Page
        "access.title": "Access",
        "access.search_placeholder": "In Access suchen",
        "access.search_help": "Durchsucht Access-Felder wie Zeit, IP, Host, Methode, HTTP-Status, Pfad sowie JSON/Raw-Daten. Unterstützt &&, || und Klammern, z.B. 2026-06-20 && /.env oder (api && 404).",
        "access.no_events": "Keine Access-Events",
        "table.limit.access": "Zeigt die neuesten 200 passenden Access-Events.",
    },
}
