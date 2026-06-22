from __future__ import annotations

import importlib
import json
import re
import socket
from typing import Any, Protocol, cast

from app.plugins.base import ExportPlugin, PluginMetadata, PluginSetting


class _PublishModule(Protocol):
    def single(self, *args: Any, **kwargs: Any) -> Any:
        ...


class Plugin(ExportPlugin):
    metadata = PluginMetadata(
        id="mqtt-hass", 
        name="MQTT to Home Assistant", 
        version="1.0.0", 
        capabilities=["export"], 
        description="Publishes OpenSecDash assets as update.* -Entity to Home Assistant via MQTT."
    )
    settings = [
        PluginSetting("enabled", "mqtt.settings.enabled", "mqtt.settings.enabled.help", "boolean", "false", [("false", "common.no"), ("true", "common.yes")]),
        PluginSetting("host", "mqtt.settings.host", "mqtt.settings.host.help", "text", "", visible_if=("enabled", "true")),
        PluginSetting("port", "mqtt.settings.port", "mqtt.settings.port.help", "number", "1883", visible_if=("enabled", "true")),
        PluginSetting("username", "mqtt.settings.username", "mqtt.settings.username.help", "text", "", visible_if=("enabled", "true")),
        PluginSetting("password", "mqtt.settings.password", "mqtt.settings.password.help", "password", "", visible_if=("enabled", "true")),
        PluginSetting("discovery_prefix", "mqtt.settings.discovery_prefix", "mqtt.settings.discovery_prefix.help", "text", "homeassistant", visible_if=("enabled", "true")),
        PluginSetting("topic_prefix", "mqtt.settings.topic_prefix", "mqtt.settings.topic_prefix.help", "text", "opensecdash", visible_if=("enabled", "true")),
    ]
    locales = {
        "en": {
            "mqtt.settings.enabled": "MQTT enabled",
            "mqtt.settings.enabled.help": "Publishes assets and updates to MQTT when enabled.",
            "mqtt.settings.host": "MQTT host",
            "mqtt.settings.host.help": "Hostname or IP of the MQTT broker.",
            "mqtt.settings.port": "MQTT port",
            "mqtt.settings.port.help": "Broker port, usually 1883 for plain MQTT.",
            "mqtt.settings.username": "MQTT username",
            "mqtt.settings.username.help": "Username for broker authentication. Required for a working secured MQTT setup.",
            "mqtt.settings.password": "MQTT password",
            "mqtt.settings.password.help": "Password for broker authentication. Use a dedicated MQTT user if possible.",
            "mqtt.settings.discovery_prefix": "Home Assistant discovery prefix",
            "mqtt.settings.discovery_prefix.help": "Discovery prefix for Home Assistant MQTT discovery, usually homeassistant.",
            "mqtt.settings.topic_prefix": "MQTT state topic prefix",
            "mqtt.settings.topic_prefix.help": "Base topic for state payloads. Asset update states are published below <prefix>/apps/<app>/state.",
            "common.yes": "Yes", "common.no": "No",
        },
        "de": {
            "mqtt.settings.enabled": "MQTT aktiviert",
            "mqtt.settings.enabled.help": "Publiziert Assets und Updates per MQTT, wenn aktiviert.",
            "mqtt.settings.host": "MQTT Host",
            "mqtt.settings.host.help": "Hostname oder IP des MQTT-Brokers.",
            "mqtt.settings.port": "MQTT Port",
            "mqtt.settings.port.help": "Broker-Port, meist 1883 für unverschlüsseltes MQTT.",
            "mqtt.settings.username": "MQTT Benutzername",
            "mqtt.settings.username.help": "Benutzername für die Broker-Anmeldung. Für ein abgesichertes MQTT-Setup erforderlich.",
            "mqtt.settings.password": "MQTT Passwort",
            "mqtt.settings.password.help": "Passwort für die Broker-Anmeldung. Wenn möglich einen dedizierten MQTT-Benutzer verwenden.",
            "mqtt.settings.discovery_prefix": "Home Assistant Discovery-Präfix",
            "mqtt.settings.discovery_prefix.help": "Discovery-Präfix für Home Assistant MQTT Discovery, üblicherweise homeassistant.",
            "mqtt.settings.topic_prefix": "MQTT State-Topic-Präfix",
            "mqtt.settings.topic_prefix.help": "Basistopic für State-Payloads. Asset-Update-States werden unter <prefix>/apps/<app>/state publiziert.",
            "common.yes": "Ja", "common.no": "Nein",
        },
    }

    async def health(self, context) -> dict[str, str]:
        host = context.get("host")
        if not host:
            return {"status": "error", "message": "MQTT host is not configured"}
        try:
            port = int(context.get("port", "1883"))
            with socket.create_connection((host, port), timeout=5):
                return {"status": "healthy", "message": f"MQTT broker reachable: {host}:{port}"}
        except Exception as exc:
            return {"status": "error", "message": f"MQTT broker not reachable: {exc}"}

    async def export_asset(self, context, asset: Any) -> None:
        if not getattr(asset, "mqtt_publish_enabled", False):
            return
        if not asset.version or not asset.latest_version:
            return

        slug = self.make_slug(str(asset.name))
        state_topic = f"{context.get('topic_prefix', 'opensecdash').strip('/')}/apps/{slug}/state"
        discovery_topic = f"{context.get('discovery_prefix', 'homeassistant').strip('/')}/update/{slug}/config"

        discovery_payload = {
            "name": asset.name,
            "unique_id": f"opensecdash_{slug}",
            "state_topic": state_topic,
            "device_class": "firmware",
            "device": {
                "identifiers": ["opensecdash_mqtt_plugin"],
                "name": "OpenSecDash Assets",
                "model": "OpenSecDash Apps Inventory",
                "manufacturer": "konkos1",
            },
        }
        state_payload = {
            "installed_version": asset.version,
            "latest_version": asset.latest_version,
            "title": asset.name,
            "release_url": asset.release_url,
        }

        self.publish(context, discovery_topic, discovery_payload, retain=True)
        self.publish(context, state_topic, state_payload, retain=True)

    @staticmethod
    def make_slug(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower().strip())
        slug = re.sub(r"_+", "_", slug).strip("_")
        return slug[:60]

    def publish(self, context, topic: str, payload: dict[str, Any] | str, retain: bool = True) -> None:
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
        publish.single(
            topic,
            json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload,
            hostname=host,
            port=int(context.get("port", "1883")),
            auth=auth,
            retain=retain,
        )
