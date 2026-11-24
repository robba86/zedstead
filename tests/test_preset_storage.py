"""Tests for app.presets.preset_storage — save/load round-trip and coercion."""

from __future__ import annotations

import json

from app.presets.preset_storage import (
    _dict_to_preset,
    load_custom_preset,
    save_custom_preset,
)
from app.presets.presets import GlobalsLayer, PresetDefinition


def _minimal_preset(**globals_values) -> PresetDefinition:
    return PresetDefinition(
        name="Test",
        description="d",
        icon="x",
        globals=GlobalsLayer(values=dict(globals_values)),
        is_builtin=False,
    )


class TestGlobalsValueCoercion:
    def test_numeric_json_globals_coerced_to_str(self):
        # A hand-edited preset may store globals values as JSON numbers. The
        # apply path treats them as strings, so the loader must coerce.
        data = {
            "name": "Hand Edited",
            "globals": {"values": {"ZombieMaxCount": 200, "Ratio": 1.5}},
        }
        preset = _dict_to_preset(data)
        assert preset.globals.values == {"ZombieMaxCount": "200", "Ratio": "1.5"}
        # No value is left as a non-str that would crash `"." in value`.
        assert all(isinstance(v, str) for v in preset.globals.values.values())

    def test_malformed_values_not_a_dict_yields_empty(self):
        data = {"name": "Bad", "globals": {"values": ["not", "a", "dict"]}}
        preset = _dict_to_preset(data)
        assert preset.globals.values == {}

    def test_missing_globals_yields_empty(self):
        preset = _dict_to_preset({"name": "NoGlobals"})
        assert preset.globals.values == {}


class TestRoundTrip:
    def test_save_then_load_preserves_globals(self, tmp_path):
        preset = _minimal_preset(ZombieMaxCount="200", AnimalMaxCount="400")
        path = save_custom_preset(tmp_path, preset, "mypreset.json")

        loaded = load_custom_preset(path)
        assert loaded.globals.values == {"ZombieMaxCount": "200", "AnimalMaxCount": "400"}
        assert loaded.name == "Test"

    def test_saved_file_is_valid_json(self, tmp_path):
        path = save_custom_preset(tmp_path, _minimal_preset(X="1"), "p.json")
        # Parses cleanly and carries the globals block.
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["globals"]["values"] == {"X": "1"}


class TestNameClash:
    """Test pass 2, N26: two names that clean up to one file name."""

    def _named(self, name: str) -> PresetDefinition:
        preset = _minimal_preset(ZombieMaxCount="100")
        preset.name = name
        return preset

    def test_a_different_name_for_the_same_file_is_refused(self, tmp_path):
        import pytest

        from app.presets.preset_storage import PresetNameClash

        first = save_custom_preset(tmp_path, self._named("Zz Test"))
        before = first.read_text()
        with pytest.raises(PresetNameClash) as exc_info:
            save_custom_preset(tmp_path, self._named("zz test!"))
        assert exc_info.value.existing == "Zz Test"
        assert first.read_text() == before

    def test_punctuation_only_names_clash_too(self, tmp_path):
        import pytest

        from app.presets.preset_storage import PresetNameClash

        save_custom_preset(tmp_path, self._named("!!!"))
        with pytest.raises(PresetNameClash):
            save_custom_preset(tmp_path, self._named("???"))

    def test_the_same_name_updates_the_preset(self, tmp_path):
        path = save_custom_preset(tmp_path, self._named("Zz Test"))
        updated = self._named("Zz Test")
        updated.description = "second save"
        assert save_custom_preset(tmp_path, updated) == path
        assert json.loads(path.read_text())["description"] == "second save"


def test_one_unreadable_preset_does_not_stop_the_list(tmp_path):
    from app.presets.preset_storage import list_custom_presets

    save_custom_preset(tmp_path, _minimal_preset())
    (tmp_path / "a_list.json").write_text("[]", encoding="utf-8")
    (tmp_path / "ansi.json").write_bytes(b'{"name": "caf\xe9"}')  # saved by an ANSI editor
    (tmp_path / "half.json").write_text('{"name": ', encoding="utf-8")

    listed = dict(list_custom_presets(tmp_path))
    assert listed["Test"].name == "test.json"
    # Listed under the file's own name, as a half-written one always was.
    assert {"a_list", "ansi", "half"} <= set(listed)


def test_a_save_that_cannot_be_encoded_keeps_the_preset_it_would_replace(tmp_path):
    import pytest

    path = save_custom_preset(tmp_path, _minimal_preset())
    before = path.read_text(encoding="utf-8")

    broken = _minimal_preset()
    broken.description = "half an emoji \ud83d"  # JSON's "\ud83d", a lone surrogate
    with pytest.raises(UnicodeEncodeError):
        save_custom_preset(tmp_path, broken)

    assert path.read_text(encoding="utf-8") == before
    assert [f.name for f in tmp_path.iterdir()] == ["test.json"]


def test_a_snapshot_keeps_an_absent_gameplay_switch_as_off(sample_config):
    """serverDZ.cfg without enableCfgGameplayFile reads as DayZ's default, 0.
    Saved as None, a later preset's 1 stayed when this one was applied again."""
    from app.presets.preset_storage import snapshot_current_config

    cfg = sample_config.server_cfg_path
    text = cfg.read_text(encoding="utf-8")
    cfg.write_text("\n".join(l for l in text.splitlines() if "enableCfgGameplayFile" not in l) + "\n", encoding="utf-8")

    assert snapshot_current_config(sample_config, "base").server_cfg.enableCfgGameplayFile == 0
