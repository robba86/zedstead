"""Configs editors for Zedstead's own values about how it runs the server.

Settings holds only Zedstead's own behaviour. A `config.json` value about the
server (which branch it updates to, how systemd restarts it, the restart
warnings it sends, where Zedstead dials RCon) names its Configs editor in
`settings_registry._Setting.editor`, and is shown and saved here instead.

The fields and the checks come from the registry, so the CLI's
`zsm settings set`, the Settings page and these editors refuse the same
values. `settings_fields` also serves the BattlEye editor's RCon host.
"""

from __future__ import annotations

from typing import Any

from app.shared.container import env_lock_note, env_lock_problem, env_locked_fields
from app.shared import settings_registry
from app.shared.config import DayzConfig
from app.web.editors import EditorSpec, register


def editor_settings(editor_id: str) -> list[settings_registry._Setting]:
    """The registry settings the Configs editor *editor_id* shows, in order."""
    return [s for s in settings_registry._REGISTRY if s.editor == editor_id]


def settings_fields(config: DayzConfig, editor_id: str) -> list[dict]:
    """Form fields for *editor_id*'s settings. The label is the key, which the
    form turns into words with the key in its tooltip (webui fieldLabel.js).
    A value the container's environment sets is shown read-only."""
    env_set = env_locked_fields(config)
    fields = []
    for s in editor_settings(editor_id):
        value = settings_registry._read(s, config)
        field: dict[str, Any] = {
            "key": s.key,
            "label": s.key,
            # A list of minutes is typed as "30,15,5,1", as on the CLI.
            "type": "str" if s.kind == "int_list" else s.kind,
            "value": settings_registry._format(s, value) if s.kind == "int_list" else value,
            "help": s.description,
        }
        if s.unit:
            field["unit"] = s.unit
        if s.choices is not None:
            field["choices"] = list(s.choices)
            if s.choice_help:
                field["choice_help"] = dict(s.choice_help)
        if s.key in env_set:
            field["locked"] = True
            field["locked_note"] = env_lock_note(env_set[s.key])
        fields.append(field)
    return fields


def apply_settings(config: DayzConfig, editor_id: str, values: dict) -> None:
    """Check every value of *editor_id* in *values*, then write them into
    *config*. A value that is refused raises before any is written; keys that
    belong to another editor are left for the caller."""
    by_key = {s.key: s for s in editor_settings(editor_id)}
    parsed: dict[str, Any] = {}
    for key, raw in values.items():
        setting = by_key.get(key)
        if setting is None:
            continue
        text = raw if isinstance(raw, str) else settings_registry._format(setting, raw)
        # click.BadParameter names the key; the save route answers 400 with it.
        parsed[key] = settings_registry._parse(setting, text)
    changed = [k for k, v in parsed.items() if settings_registry._read(by_key[k], config) != v]
    problem = env_lock_problem(env_locked_fields(config), changed)
    if problem:
        raise ValueError(problem)
    for key in changed:
        settings_registry._write(by_key[key], config, parsed[key])


def _register(editor_id: str, title: str, section: str) -> None:
    """A Server-group editor over one group of registry settings, saved into
    config.json through `WebState.mutate_config` like the Run Command editor."""

    def load(config: DayzConfig) -> dict:
        return {"kind": "form", "sections": [{"title": section, "fields": settings_fields(config, editor_id)}]}

    def save(config: DayzConfig, values: dict) -> None:
        unknown = sorted(set(values) - {s.key for s in editor_settings(editor_id)})
        if unknown:
            raise ValueError(f"Unknown field(s): {', '.join(unknown)}")
        apply_settings(config, editor_id, values)

    register(
        EditorSpec(
            id=editor_id,
            title=title,
            group="Server",
            kind="form",
            exists=lambda config: True,
            load=load,
            save=save,
            mutates_app_config=True,
            # No Raw tab: these are config.json values, not a server file.
        )
    )


# (editor id, title, section title). The ids are what `_Setting.editor` names.
ZSM_EDITORS: list[tuple[str, str, str]] = [
    ("updates", "Updates", "Server updates"),
    ("crash-recovery", "Crash recovery", "Crash recovery"),
    ("restart-warnings", "Restart warnings", "Restart warnings"),
]

for _id, _title, _section in ZSM_EDITORS:
    _register(_id, _title, _section)
