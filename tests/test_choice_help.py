"""A closed list says what each value means in the picker itself.

The web's `Select` shows an option's description under it in the open list
(`choice_help` from a schema, `optionHelp` on the component). A value's meaning
written only into the field's help text sits in a tooltip nobody opens while
choosing, so every closed list whose values need explaining carries one line
per value. The tooltip keeps its own text as well (webui/CLAUDE.md).
"""

from __future__ import annotations

import pytest

from app.commands.config_cmd import _CFG_CHOICE_HELP, _CFG_CHOICES, _CFG_OPEN_CHOICES
from app.parsers.cfggameplay import CHOICE_HELP as GAMEPLAY_CHOICE_HELP
from app.parsers.cfggameplay import CHOICES as GAMEPLAY_CHOICES
from app.parsers.cfggameplay import OPEN_CHOICES as GAMEPLAY_OPEN_CHOICES
from app.shared.settings_registry import _REGISTRY
from app.web.editors import _mod_json, dynamic_ai, expansion_ai, radzone, toxiczone
from app.xml.economycore_xml import FILE_TYPE_HELP, FILE_TYPES
from app.xml.events_xml import LIMIT_HELP, LIMIT_VALUES, POSITION_HELP, POSITION_VALUES

# A list of words (Expansion's WALK, JOG, …) explains itself; a list of number
# codes needs a line per value. Number lists whose meaning no source gives,
# each with the reason:
_SELF_EXPLAINING = {
    "Spatial_SpawnMode",  # "0 or 1": no source says what either does
}


@pytest.mark.parametrize("setting", [s for s in _REGISTRY if s.choices], ids=lambda s: s.key)
def test_every_closed_setting_describes_each_choice(setting):
    assert setting.choice_help is not None, f"{setting.key}: add choice_help"
    assert set(setting.choice_help) == set(setting.choices)


@pytest.mark.parametrize(
    ("lists", "meanings"),
    [
        ({**_CFG_CHOICES, **_CFG_OPEN_CHOICES}, _CFG_CHOICE_HELP),
        ({**GAMEPLAY_CHOICES, **GAMEPLAY_OPEN_CHOICES}, GAMEPLAY_CHOICE_HELP),
    ],
    ids=["serverDZ.cfg", "cfggameplay.json"],
)
def test_every_config_choice_list_describes_each_value(lists, meanings):
    for key, values in lists.items():
        assert set(meanings.get(key, {})) == set(values), f"{key}: one line per value"


def test_event_and_economycore_pickers_describe_each_value():
    assert set(POSITION_HELP) == set(POSITION_VALUES)
    assert set(LIMIT_HELP) == set(LIMIT_VALUES)
    assert set(FILE_TYPE_HELP) == set(FILE_TYPES)


def _mod_fields():
    """Every Field in the mod JSON editors' forms and tables."""
    seen = []

    def walk(obj):
        if isinstance(obj, _mod_json.Field):
            seen.append(obj)
        elif isinstance(obj, (tuple, list)):
            for x in obj:
                walk(x)
        elif isinstance(obj, (_mod_json.Form, _mod_json.Table)):
            for value in vars(obj).values():
                walk(value)

    for module in (dynamic_ai, expansion_ai, radzone, toxiczone):
        for value in vars(module).values():
            walk(value)
    return seen


@pytest.mark.parametrize("f", _mod_fields(), ids=lambda f: f.key)
def test_a_mod_field_with_fixed_values_describes_them(f):
    if not isinstance(f.choices, tuple):
        return
    if f.key in _SELF_EXPLAINING or not all(v.lstrip("-").isdigit() for v in f.choices):
        return
    assert f.choice_help, f"{f.key}: pass choices as a {{value: meaning}} dict"
