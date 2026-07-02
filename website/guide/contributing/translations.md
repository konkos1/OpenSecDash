# Contributing translations

OpenSecDash welcomes UI translations. The app currently uses simple Python dictionaries for translations, so adding or improving a language does not require a separate translation platform.

## Where translations live

Core app translations live in:

```text
backend/app/locales/en.py
backend/app/locales/de.py
backend/app/locales/__init__.py
```

Each locale file exports one dictionary:

```python
EN = {
    "nav.dashboard": "Dashboard",
    "settings.title": "Settings",
}
```

```python
DE = {
    "nav.dashboard": "Dashboard",
    "settings.title": "Einstellungen",
}
```

Plugin translations live inside the plugin file itself, in the plugin's `locales` dictionary. Example:

```python
locales = {
    "en": {
        "my_plugin.settings.enabled": "My plugin enabled",
        "my_plugin.settings.enabled.help": "Enables My plugin.",
    },
    "de": {
        "my_plugin.settings.enabled": "My Plugin aktiviert",
        "my_plugin.settings.enabled.help": "Aktiviert My Plugin.",
    },
}
```

## Adding a new app language

To add a new language, for example French (`fr`):

1. Copy the English locale:

```bash
cp backend/app/locales/en.py backend/app/locales/fr.py
```

2. Rename the exported dictionary:

```python
FR = {
    "app.name": "OpenSecDash",
    ...
}
```

3. Add it to `backend/app/locales/__init__.py`:

```python
from app.locales.de import DE
from app.locales.en import EN
from app.locales.fr import FR

LOCALES = {
    "de": DE,
    "en": EN,
    "fr": FR,
}
```

4. Add the language to the Settings page selector in `backend/app/templates/settings.html`:

```html
<option value="fr" {% if language_setting == 'fr' %}selected{% endif %}>Français</option>
```

5. Allow the language code in `backend/app/api/pages.py` when settings are saved.

The current save handler validates language codes before storing them. Add the new code to that allow-list.

## Translation keys

Templates call translations with:

```jinja
{{ t("settings.title") }}
```

Python code can use:

```python
from app.core.i18n import translate

translate("settings.title", "en")
```

If a key is missing, OpenSecDash returns the key itself. This makes missing translations visible in the UI, for example:

```text
settings.title
```

## Naming conventions

Use namespaced keys:

```text
nav.*
common.*
settings.*
dashboard.*
events.*
access.*
assets.*
asset.*
diagnostics.*
insight.*
plugin_id.settings.*
```

Guidelines:

- keep keys stable once used in templates/code
- prefer clear, descriptive keys
- avoid duplicate keys
- keep technical event identifiers untranslated, such as `access.denied`
- translate user-facing labels, help text, button labels, and descriptions

## What should not be translated

Usually keep these unchanged:

- technical event types: `access.error`, `security.ban`
- plugin IDs: `traefik_log`, `json_assets`
- setting keys: `plugin.traefik_log.enabled`
- file paths and commands
- API names such as `cscli`, `MQTT`, `GeoIP`, `Proxmox`

## Tone and wording

OpenSecDash is aimed at homelab users and OSS contributors. Prefer wording that is:

- clear
- practical
- not overly formal
- consistent with existing UI labels

For German translations, use informal but concise product UI language. Avoid overly long labels where they appear on buttons, table headers, or badges.

## Plugin translations

When adding a plugin, include translations for all plugin settings:

```python
PluginSetting(
    "poll_interval",
    "my_plugin.settings.poll_interval",
    "my_plugin.settings.poll_interval.help",
    "number",
    "5",
)
```

Needs matching locale entries:

```python
"my_plugin.settings.poll_interval": "Poll interval seconds",
"my_plugin.settings.poll_interval.help": "How often the source is checked for new data.",
```

If you add a new app language, plugin locale dictionaries can also include that language code. If a plugin does not provide a translation for a language, OpenSecDash falls back to English for plugin settings.

## Checking completeness

There is currently no dedicated translation completeness command. Recommended checks:

1. Compare the new locale file with `backend/app/locales/en.py`.
2. Search for the raw language key in the UI after running the app.
3. Run tests and type checks:

```bash
cd backend
uv run pytest -q
uv run pyright ../backend/app ../backend/tests ../plugins
```

4. Build the website if docs were changed:

```bash
cd website
npm run docs:build
```

## Good pull requests

A good translation PR should include:

- the new or updated locale file
- Settings page language selector update, if adding a new language
- any plugin locale additions, if relevant
- screenshots are welcome for new languages, especially Settings and Dashboard
- a note if some strings intentionally stay in English because they are technical terms
