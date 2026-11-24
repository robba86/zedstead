"""Starter templates for the manual-link builder (app/xml/xml_templates.py).

Every assertion here is a promise the buttons make to the user: the starter
parses, it is already formatted (so Format is a no-op on it), and it can be
saved as a link without editing a character first. A starter that is a dead end
is worse than no starter.
"""

from __future__ import annotations

import pytest

from app.xml import xml_manual, xml_templates
from app.xml.types_xml import KNOWN_CATEGORIES, KNOWN_USAGES, KNOWN_VALUES
from app.xml.xml_text import check_xml_text, format_xml_text
from tests.conftest import manual_dir

ALL = xml_templates.TEMPLATES


@pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
def test_every_template_parses(template):
    assert check_xml_text(template.content) is None


@pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
def test_every_template_is_already_formatted(template):
    """Pressing Format on a fresh starter must change nothing."""
    assert format_xml_text(template.content).text == template.content


@pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
def test_every_template_declares_itself(template):
    assert template.content.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
    assert bool(template.kind) != bool(template.target)  # exactly one of the two


def test_template_ids_are_unique():
    ids = [t.id for t in ALL]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("template", [t for t in ALL if t.kind], ids=lambda t: t.id)
def test_kind_templates_use_that_kinds_root_tag(template):
    """`_merge_free_form` appends the *children* of the fragment root, so a
    starter that skipped the wrapper would merge its grandchildren."""
    root = xml_manual.root_tag_for(template.kind)
    assert f"<{root}>" in template.content


def test_every_manual_kind_has_a_starter():
    kinds = xml_manual.MANUAL_KINDS - {xml_manual.MERGE_KIND}
    assert {t.kind for t in ALL if t.kind} == kinds


@pytest.mark.parametrize("template", [t for t in ALL if t.target], ids=lambda t: t.id)
def test_free_form_templates_name_a_safe_target(template):
    assert xml_manual.is_safe_target(template.target)


@pytest.mark.parametrize("template", ALL, ids=lambda t: t.id)
def test_every_template_is_accepted_by_create_file(sample_config, template):
    """End to end: a starter goes straight into a link, unedited."""
    link = xml_manual.create_file(
        "starter", template.kind or xml_manual.MERGE_KIND, template.content,
        target=template.target,
        manual_dir=manual_dir(),
    )
    assert link["kind"] == (template.kind or xml_manual.kind_for_target(template.target))


def test_lookup_helpers():
    assert {t.id for t in xml_templates.templates_for_kind("types")} == {
        "types-item", "types-disable", "types-vehicle",
    }
    assert [t.id for t in xml_templates.templates_for_target("DB/Globals.XML")] == ["globals-var"]
    assert xml_templates.templates_for_target("cfgweather.xml") == []
    assert xml_templates.get_template("types-item").kind == "types"
    assert xml_templates.get_template("nope") is None


def test_the_vehicle_starter_names_no_category():
    """Bohemia's CivilianSedan has no <category>, and the `vehiclesparts` it
    used to name is in no stock cfglimitsdefinition.xml."""
    content = xml_templates.get_template("types-vehicle").content
    assert "<category" not in content and "vehiclesparts" not in content


def test_types_starter_lists_the_vocabulary_the_app_knows():
    """The hints are built from types_xml's constants — a starter must not name
    a category or tier the rest of the app doesn't recognise."""
    content = xml_templates.get_template("types-item").content
    for value in (KNOWN_CATEGORIES[0], KNOWN_USAGES[0], KNOWN_VALUES[0]):
        assert value in content
