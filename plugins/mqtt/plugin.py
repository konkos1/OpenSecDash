from __future__ import annotations

import importlib
import json
import logging
import re
import socket
import ssl
from typing import Any, Protocol, cast

from app.models.assets import Asset
from app.plugins.base import ExportPlugin, PeriodicPlugin, PluginContext, PluginMetadata, PluginSetting


logger = logging.getLogger(__name__)


class _PublishModule(Protocol):
    def single(self, *args: Any, **kwargs: Any) -> Any:
        ...

    def multiple(self, *args: Any, **kwargs: Any) -> Any:
        ...


class Plugin(ExportPlugin, PeriodicPlugin):
    def __init__(self) -> None:
        self._last_publish_error: str | None = None

    @staticmethod
    def tls_enabled(context: PluginContext) -> bool:
        mode = context.get("tls_mode", "none")
        if mode not in {"none", "tls"}:
            raise ValueError("Unsupported MQTT transport security mode")
        return mode == "tls"

    metadata = PluginMetadata(
        id="mqtt-hass", 
        name="MQTT to Home Assistant", 
        version="1.0.0", 
        api_version="2",
        capabilities=["export"], 
        description="Publishes OpenSecDash assets as update.* -Entity to Home Assistant via MQTT."
    )
    settings = [
        PluginSetting("enabled", "mqtt.settings.enabled", "mqtt.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("host", "mqtt.settings.host", "mqtt.settings.host.help", "text", "", visible_if=("enabled", "true")),
        PluginSetting("port", "mqtt.settings.port", "mqtt.settings.port.help", "number", "1883", visible_if=("enabled", "true")),
        PluginSetting("username", "mqtt.settings.username", "mqtt.settings.username.help", "text", "", visible_if=("enabled", "true")),
        PluginSetting("password", "mqtt.settings.password", "mqtt.settings.password.help", "password", "", visible_if=("enabled", "true")),
        PluginSetting("tls_mode", "mqtt.settings.tls_mode", "mqtt.settings.tls_mode.help", "select", "none", [("none", "mqtt.option.tls_none"), ("tls", "mqtt.option.tls")], visible_if=("enabled", "true")),
        PluginSetting("ca_file", "mqtt.settings.ca_file", "mqtt.settings.ca_file.help", "file", "", visible_if=("enabled", "true")),
        PluginSetting("discovery_prefix", "mqtt.settings.discovery_prefix", "mqtt.settings.discovery_prefix.help", "text", "homeassistant", visible_if=("enabled", "true")),
        PluginSetting("topic_prefix", "mqtt.settings.topic_prefix", "mqtt.settings.topic_prefix.help", "text", "opensecdash", visible_if=("enabled", "true")),
        PluginSetting("publish_interval", "mqtt.settings.publish_interval", "mqtt.settings.publish_interval.help", "text", "auto", visible_if=("enabled", "true")),
    ]
    locales = {
        "en": {
            "mqtt.settings.enabled": "MQTT enabled",
            "mqtt.settings.enabled.help": "Publishes assets and updates to Home Assistant's MQTT when enabled.",
            "mqtt.settings.host": "MQTT host",
            "mqtt.settings.host.help": "Hostname or IP of the MQTT broker.",
            "mqtt.settings.port": "MQTT port",
            "mqtt.settings.port.help": "Broker port, usually 1883 for plain MQTT.",
            "mqtt.settings.username": "MQTT username",
            "mqtt.settings.username.help": "Username for broker authentication. Required for a working secured MQTT setup.",
            "mqtt.settings.password": "MQTT password",
            "mqtt.settings.password.help": "Password for broker authentication. Use a dedicated MQTT user if possible.",
            "mqtt.settings.tls_mode": "MQTT transport security (plain MQTT is unencrypted)",
            "mqtt.settings.tls_mode.help": "TLS verifies the broker certificate and hostname. Plain MQTT is unencrypted and is shown as a diagnostic warning.",
            "mqtt.settings.ca_file": "Custom CA certificate file",
            "mqtt.settings.ca_file.help": "Optional PEM CA file available inside the OpenSecDash container. Leave empty to use the system trust store.",
            "mqtt.option.tls_none": "None (unencrypted)",
            "mqtt.option.tls": "TLS (certificate verified)",
            "mqtt.settings.discovery_prefix": "Home Assistant discovery prefix",
            "mqtt.settings.discovery_prefix.help": "Discovery prefix for Home Assistant MQTT discovery, usually homeassistant.",
            "mqtt.settings.topic_prefix": "MQTT state topic prefix",
            "mqtt.settings.topic_prefix.help": "Base topic for state payloads. Asset update states are published below <prefix>/apps/<app>/state.",
            "mqtt.settings.publish_interval": "Publish interval",
            "mqtt.settings.publish_interval.help": "Use 'auto' to publish when asset sources trigger MQTT, 0 for manual only, or a number of seconds for periodic publishing.",
            "common.yes": "Yes", "common.no": "No",
        },
        "de": {
            "mqtt.settings.enabled": "MQTT aktiviert",
            "mqtt.settings.enabled.help": "Publiziert Assets und Updates per MQTT an Home Assistant, wenn aktiviert.",
            "mqtt.settings.host": "MQTT Host",
            "mqtt.settings.host.help": "Hostname oder IP des MQTT-Brokers.",
            "mqtt.settings.port": "MQTT Port",
            "mqtt.settings.port.help": "Broker-Port, meist 1883 für unverschlüsseltes MQTT.",
            "mqtt.settings.username": "MQTT Benutzername",
            "mqtt.settings.username.help": "Benutzername für die Broker-Anmeldung. Für ein abgesichertes MQTT-Setup erforderlich.",
            "mqtt.settings.password": "MQTT Passwort",
            "mqtt.settings.password.help": "Passwort für die Broker-Anmeldung. Wenn möglich einen dedizierten MQTT-Benutzer verwenden.",
            "mqtt.settings.tls_mode": "MQTT-Transportsicherheit (einfaches MQTT ist unverschlüsselt)",
            "mqtt.settings.tls_mode.help": "TLS prüft Zertifikat und Hostnamen des Brokers. Unverschlüsseltes MQTT wird als Diagnosewarnung angezeigt.",
            "mqtt.settings.ca_file": "Eigene CA-Zertifikatsdatei",
            "mqtt.settings.ca_file.help": "Optionale PEM-CA-Datei im OpenSecDash-Container. Leer lassen, um den System-Zertifikatsspeicher zu verwenden.",
            "mqtt.option.tls_none": "Keine (unverschlüsselt)",
            "mqtt.option.tls": "TLS (Zertifikat geprüft)",
            "mqtt.settings.discovery_prefix": "Home Assistant Discovery-Präfix",
            "mqtt.settings.discovery_prefix.help": "Discovery-Präfix für Home Assistant MQTT Discovery, üblicherweise homeassistant.",
            "mqtt.settings.topic_prefix": "MQTT State-Topic-Präfix",
            "mqtt.settings.topic_prefix.help": "Basistopic für State-Payloads. Asset-Update-States werden unter <prefix>/apps/<app>/state publiziert.",
            "mqtt.settings.publish_interval": "Veröffentlichungsintervall",
            "mqtt.settings.publish_interval.help": "'auto' veröffentlicht, wenn Asset-Quellen MQTT triggern, 0 bedeutet nur manuell, oder eine Sekundenanzahl für periodisches Veröffentlichen.",
            "common.yes": "Ja", "common.no": "Nein",
        },
    }

    def web(self):
        from app.plugins.web import PluginWebRegistration

        from .routes import ungated_router

        return PluginWebRegistration(ungated_router=ungated_router)

    async def health(self, context) -> dict[str, str]:
        host = context.get("host")
        if not host:
            return {"status": "error", "message": "MQTT host is not configured"}
        try:
            port = int(context.get("port", "1883"))
            tls_enabled = self.tls_enabled(context)
            with socket.create_connection((host, port), timeout=5) as connection:
                if tls_enabled:
                    ca_file = context.get("ca_file").strip() or None
                    tls_context = ssl.create_default_context(cafile=ca_file)
                    with tls_context.wrap_socket(connection, server_hostname=host):
                        pass
                logger.debug("MQTT broker reachable: %s:%s", host, port)
                message = f"MQTT broker reachable: {host}:{port}"
                if self._last_publish_error:
                    return {"status": "error", "message": f"{message}; MQTT publish failed: {self._last_publish_error}"}
                if not tls_enabled:
                    return {"status": "warning", "message": f"{message}; MQTT transport is unencrypted"}
                return {"status": "healthy", "message": message}
        except Exception as exc:
            return {"status": "error", "message": f"MQTT broker not reachable: {exc}"}

    async def tick(self, context: PluginContext) -> None:
        # ``publish_interval`` has three modes:
        #   auto: publish only when JSON Assets calls this export plugin
        #   0:    manual button only
        #   N:    periodic publish every N seconds via the plugin manager loop
        interval = self.publish_interval(context)
        if interval <= 0:
            return
        context.settings["_periodic_export"] = "true"
        for asset in self.publishable_assets(context):
            await self.export_asset(context, asset)

    @staticmethod
    def publish_interval(context: PluginContext) -> int:
        value = context.get("publish_interval", "auto").strip().lower()
        if value == "auto":
            return -1
        try:
            return max(int(value), 0)
        except ValueError:
            return -1

    @staticmethod
    def publishable_assets(context: PluginContext) -> list[Asset]:
        return (
            context.db.query(Asset)
            .filter(
                Asset.mqtt_publish_enabled == True,
                Asset.version.isnot(None),
                Asset.latest_version.isnot(None),
                Asset.release_url.isnot(None),
            )
            .all()
        )

    async def export_asset(self, context, asset: Any) -> None:
        # This hook is called by asset sources, the manual button, and the
        # periodic loop. Gate here so all entry points follow the same policy.
        interval = self.publish_interval(context)
        if interval != -1 and not context.manual_export and context.get("_periodic_export") != "true":
            logger.debug("Skipping MQTT auto export because publish_interval=%s", context.get("publish_interval", "auto"))
            return
        if not getattr(asset, "mqtt_publish_enabled", False):
            logger.debug("Skipping MQTT export for asset=%s because publish toggle is disabled", getattr(asset, "name", "unknown"))
            return
        if not asset.version or not asset.latest_version or not asset.release_url:
            logger.debug(
                "Skipping MQTT export for asset=%s because version/latest_version/release_url is missing",
                getattr(asset, "name", "unknown"),
            )
            return

        slug = self.make_slug(str(asset.name))
        state_topic = f"{context.get('topic_prefix', 'opensecdash').strip('/')}/apps/{slug}/state"
        # The discovery object_id is namespaced to match the unique_id: the
        # discovery topic is the entity's identity for Home Assistant, and a
        # bare app slug ("nextcloud") is far too likely to collide with other
        # update publishers on the same broker. A colliding retained config
        # with a different unique_id can never create an entity - HA refuses
        # to change the unique_id of an existing discovery topic.
        discovery_topic = f"{context.get('discovery_prefix', 'homeassistant').strip('/')}/update/opensecdash_{slug}/config"

        discovery_payload = {
            "name": asset.name,
            "unique_id": f"opensecdash_{slug}",
            "state_topic": state_topic,
            "device_class": "firmware",
            "device": {
                "identifiers": ["opensecdash_mqtt_plugin"],
                "name": "OpenSecDash Assets",
                "model": "OpenSecDash Assets",
                "manufacturer": "konkos1",
            },
        }
        state_payload = {
            "installed_version": asset.version,
            "latest_version": asset.latest_version,
            "title": asset.name,
            "release_url": asset.release_url,
        }

        try:
            # One broker connection for both messages (discovery + state)
            # instead of one connection per message - with many publishable
            # assets the per-message connect/disconnect dominated the export.
            self.publish_many(
                context,
                [
                    (discovery_topic, discovery_payload, True),
                    (state_topic, state_payload, True),
                ],
            )
        except Exception as exc:
            self._last_publish_error = str(exc)
            raise
        self._last_publish_error = None
        logger.debug("Published MQTT discovery/state for asset=%s", asset.name)

    @staticmethod
    def make_slug(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower().strip())
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug[:60]

    def publish(self, context, topic: str, payload: dict[str, Any] | str, retain: bool = True) -> None:
        self.publish_many(context, [(topic, payload, retain)])

    def publish_many(self, context, messages: list[tuple[str, dict[str, Any] | str, bool]]) -> None:
        host = context.get("host")
        if not host:
            raise ValueError("MQTT host is required")
        try:
            publish = cast(_PublishModule, importlib.import_module("paho.mqtt.publish"))
        except Exception as exc:
            raise RuntimeError("paho-mqtt is required for MQTT export") from exc
        auth = None
        if context.get("username") or context.get("password"):
            auth = {"username": context.get("username"), "password": context.get("password")}
        payloads = [
            {
                "topic": topic,
                "payload": json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload,
                "retain": retain,
            }
            for topic, payload, retain in messages
        ]
        logger.debug("Publishing %d MQTT message(s): %s", len(payloads), ", ".join(item["topic"] for item in payloads))
        tls = None
        if self.tls_enabled(context):
            tls = {
                "ca_certs": context.get("ca_file").strip() or None,
                "cert_reqs": ssl.CERT_REQUIRED,
                "tls_version": ssl.PROTOCOL_TLS_CLIENT,
            }
        publish.multiple(
            payloads,
            hostname=host,
            port=int(context.get("port", "1883")),
            auth=auth,
            tls=tls,
        )
