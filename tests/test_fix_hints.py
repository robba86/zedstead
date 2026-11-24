"""Tests for app/ops/fix_hints.py — per-finding remediation hints."""

from __future__ import annotations

import pytest

from app.modmgr.mod_conflict import ConflictItem
from app.ops.fix_hints import conflict_fix

_CATEGORIES = ["rpt_error", "missing_dep", "missing_bikey", "required_addon", "no_logs"]


@pytest.mark.parametrize("category", _CATEGORIES)
def test_every_scanned_category_has_a_hint(category):
    # The five categories ConflictItem documents are exactly the ones the two
    # Conflicts tables can render — none may fall through to a bare row.
    fix = conflict_fix(ConflictItem(severity="warning", category=category, message="x"))
    assert fix is not None
    assert fix["why"] and fix["how"]
    assert isinstance(fix["links"], list)
    assert isinstance(fix["actions"], list)


@pytest.mark.parametrize("category", _CATEGORIES)
def test_hints_are_plain_text(category):
    # FixCell.jsx renders these raw — Rich markup would show up as literal
    # brackets in the browser (same rule as the job log lines).
    fix = conflict_fix(ConflictItem(severity="warning", category=category, message="x"))
    assert "[/" not in fix["why"] + fix["how"]


def test_unknown_category_has_no_hint():
    assert conflict_fix(ConflictItem(severity="info", category="something_new", message="x")) is None


def test_missing_bikey_offers_the_targeted_copy_first():
    fix = conflict_fix(
        ConflictItem(
            severity="warning",
            category="missing_bikey",
            message="Missing bikey(s): tp.bikey",
            mod_name="@TraderPlus",
        )
    )
    kinds = [a["kind"] for a in fix["actions"]]
    # Copying keys is the one-step repair; reinstalling is the fallback for a
    # mod whose own Keys/ folder is empty, so it comes second.
    assert kinds == ["copy_bikeys", "install_mods"]
    assert fix["actions"][1]["mods"] == [{"name": "@TraderPlus", "id": ""}]
    # Both rewrite files — the page must ask before running either.
    assert all(a["confirm"] for a in fix["actions"])


def test_missing_bikey_without_a_mod_name_still_copies():
    fix = conflict_fix(ConflictItem(severity="warning", category="missing_bikey", message="x"))
    assert [a["kind"] for a in fix["actions"]] == ["copy_bikeys"]


def test_missing_dep_installs_by_workshop_id_when_known():
    fix = conflict_fix(
        ConflictItem(
            severity="error",
            category="missing_dep",
            message="@CF required by @TraderPlus",
            mod_name="@TraderPlus",
            dep_id="1559212036",
            dep_name="@CF",
        )
    )
    assert fix["actions"] == [
        {
            "kind": "install_mods",
            "label": "Install @CF",
            "mods": [{"name": "@CF", "id": "1559212036"}],
        }
    ]
    # Installing a missing dependency is additive — nothing to confirm.
    assert "confirm" not in fix["actions"][0]
    assert "@CF" in fix["why"] and "@TraderPlus" in fix["why"]


def test_missing_dep_without_a_dependency_offers_no_button():
    fix = conflict_fix(ConflictItem(severity="error", category="missing_dep", message="x"))
    assert fix["actions"] == []
    assert fix["links"]  # ...but still routes somewhere useful


def test_required_addon_reinstalls_the_mod_that_declares_it():
    fix = conflict_fix(
        ConflictItem(
            severity="warning",
            category="required_addon",
            message="Requires addon 'XYZ' — not found in installed mods",
            mod_name="@Mod",
        )
    )
    assert [a["label"] for a in fix["actions"]] == ["Reinstall @Mod"]


def test_no_logs_points_at_the_log_cleanup_setting():
    fix = conflict_fix(ConflictItem(severity="info", category="no_logs", message="x"))
    assert fix["actions"] == []
    # ?q= is the app-wide settings prefill key — SettingsPage lands on the field.
    assert [l["to"] for l in fix["links"]] == ["/settings?q=log_cleanup_keep"]


def test_hint_reads_a_serialised_item_too():
    # The Mods router attaches hints to dataclasses, but nothing stops a caller
    # passing the JSON copy — duck-typed on the same field names.
    class _Row:
        category = "missing_bikey"
        mod_name = "@CF"

    assert conflict_fix(_Row()) is not None
