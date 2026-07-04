from typing import cast

from app.core.template_context import build_template_context
from app.models.core import Datasource


def _add_datasource(db_session, plugin_id, enabled, backlog_pending, progress=None):
    db_session.add(
        Datasource(
            name=plugin_id,
            plugin_id=plugin_id,
            enabled=enabled,
            backlog_pending=backlog_pending,
            backlog_progress_percent=progress,
        )
    )


def test_build_template_context_lists_only_enabled_pending_datasources(db_session):
    _add_datasource(db_session, "catching_up", enabled=True, backlog_pending=True, progress=30)
    _add_datasource(db_session, "idle", enabled=True, backlog_pending=False)
    _add_datasource(db_session, "disabled_but_pending", enabled=False, backlog_pending=True, progress=10)
    db_session.commit()

    context = build_template_context(db_session)
    backlog_datasources = cast(list[Datasource], context["backlog_datasources"])

    names = [datasource.plugin_id for datasource in backlog_datasources]
    assert names == ["catching_up"]
    assert backlog_datasources[0].backlog_progress_percent == 30


def test_build_template_context_backlog_list_is_empty_when_nothing_pending(db_session):
    _add_datasource(db_session, "idle", enabled=True, backlog_pending=False)
    db_session.commit()

    context = build_template_context(db_session)

    assert context["backlog_datasources"] == []
