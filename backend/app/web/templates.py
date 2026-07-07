from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

from app.web.filters import register_filters

# The single Jinja2Templates instance for the whole app - core pages, error
# pages and plugin pages all render through this one, so filters/globals are
# registered once and plugin template dirs can be mounted onto it.
templates = Jinja2Templates(directory="app/templates")
register_filters(templates.env)


def register_plugin_template_dirs(dirs_by_plugin_id: dict[str, str]) -> None:
    """Mount plugin template dirs under their plugin id, e.g. "crowdsec/crowdsec.html".

    The PrefixLoader namespace prevents name collisions with core templates;
    plugin templates can still extend "base.html" because the core loader
    stays first in the chain.
    """
    if not dirs_by_plugin_id:
        return
    templates.env.loader = ChoiceLoader([
        FileSystemLoader("app/templates"),
        PrefixLoader({pid: FileSystemLoader(path) for pid, path in dirs_by_plugin_id.items()}),
    ])
