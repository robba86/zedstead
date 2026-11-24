"""Map editor tooltips, popups and icons show text, never markup (plans/68 P1).

Leaflet sets a string tooltip or popup with ``innerHTML``. The map editor's
tooltips carry names from files zsm did not write (a mod's merged
cfgeventspawns.xml, TraderPlus and Expansion JSON), so every tooltip is bound
through ``webui/src/pages/mapeditor/leaflet/textTooltip.js``, which escapes
the text, and every popup is given a function that returns a DOM node.
A divIcon's ``html`` goes through innerHTML too, so every piece of markup
the editor builds with a value in it uses the escaping ``html`` tag from
``leaflet/html.js``.

There is no frontend test harness: the source tests read the files, and the
Node tests run the helpers themselves when Node is installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "webui" / "src"
HELPER = SRC / "pages" / "mapeditor" / "leaflet" / "textTooltip.js"
HTML_TAG = SRC / "pages" / "mapeditor" / "leaflet" / "html.js"
MAP_EDITOR = SRC / "pages" / "mapeditor"


def _sources():
    for path in sorted(SRC.rglob("*")):
        if path.suffix in (".js", ".jsx") and path.is_file():
            yield path, path.read_text(encoding="utf-8")


def _offenders(pattern: re.Pattern, skip: Path | None = None) -> list[str]:
    found = []
    for path, text in _sources():
        if path == skip:
            continue
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{path.relative_to(ROOT)}:{line}")
    return found


def test_every_map_tooltip_goes_through_the_text_helper():
    # `.bindTooltip(`, `.setTooltipContent(` and `L.tooltip(` all reach
    # Leaflet's innerHTML with a string. Use `bindTextTooltip(layer, text, opts)`.
    pattern = re.compile(r"\.(?:bindTooltip|setTooltipContent)\(|\bL\.tooltip\(")
    assert _offenders(pattern, skip=HELPER) == []


def test_every_popup_is_given_a_dom_node():
    # The popups render React into a shared host element, handed over as
    # `() => popupHost`. A string here would go through innerHTML.
    pattern = re.compile(
        r"\.(?:bindPopup|setPopupContent)\((?!\s*\(\)\s*=>)|\bL\.popup\("
    )
    assert _offenders(pattern) == []


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_helper_shows_markup_as_text():
    samples = [
        '<img src=x onerror="alert(1)">',
        "Trader & Co's \"best\" <b>deals</b>",
        "Line one\nLine two",
        "",
    ]
    script = (
        f"import {{ bindTextTooltip }} from {json.dumps(HELPER.as_uri())};\n"
        f"const samples = {json.dumps(samples)};\n"
        "const out = samples.map((s) => {\n"
        "  let bound = null;\n"
        "  const layer = { bindTooltip: (content, options) => { bound = [content, options]; return layer } };\n"
        "  bindTextTooltip(layer, s, { direction: 'top' });\n"
        "  return bound;\n"
        "});\n"
        "console.log(JSON.stringify(out));\n"
    )
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60, check=True,
    )
    bound = json.loads(result.stdout)

    for sample, (content, options) in zip(samples, bound):
        assert options == {"direction": "top"}
        # No raw markup character survives, and decoding gives the text back.
        assert not re.search(r"[<>\"']", content), content
        decoded = (
            content.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&#39;", "'").replace("&amp;", "&")
        )
        assert decoded == sample


# A template literal with no tag in front of it that starts with markup. Its
# body runs to the next backtick, which is right for every markup template in
# the editor (none nests another template).
_UNTAGGED_MARKUP = re.compile(r"(?<![\w$])`(\s*<[A-Za-z][^`]*)`")


def test_map_markup_with_a_value_in_it_is_built_with_the_escaping_tag():
    # A divIcon's `html` and the grid labels reach Leaflet's innerHTML. Write
    # them as html`...` (leaflet/html.js), so every value is escaped and only
    # markup wrapped in raw() passes as it is.
    found = []
    for path in sorted(MAP_EDITOR.rglob("*")):
        if path.suffix not in (".js", ".jsx") or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in _UNTAGGED_MARKUP.finditer(text):
            if "${" in match.group(1):
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path.relative_to(ROOT)}:{line}")
    assert found == []


def _run_node(script: str):
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_html_tag_escapes_every_value_and_lets_raw_markup_through():
    script = (
        f"import {{ html, raw }} from {json.dumps(HTML_TAG.as_uri())};\n"
        "const colour = '\"/><img src=x onerror=alert(1)><rect fill=\"';\n"
        "const out = {\n"
        "  attr: html`<circle fill=\"${colour}\"/>`,\n"
        "  text: html`<text>${'<b>&'}</text>`,\n"
        "  number: html`<g transform=\"rotate(${12.5} 12 12)\"></g>`,\n"
        "  missing: html`<rect fill=\"${undefined}\"/>`,\n"
        "  nested: html`<span>${raw(html`<svg fill=\"${colour}\"></svg>`)}</span>`,\n"
        "};\n"
        "console.log(JSON.stringify(out));\n"
    )
    out = _run_node(script)

    assert out["attr"] == (
        '<circle fill="&quot;/&gt;&lt;img src=x onerror=alert(1)&gt;&lt;rect fill=&quot;"/>'
    )
    assert out["text"] == "<text>&lt;b&gt;&amp;</text>"
    assert out["number"] == '<g transform="rotate(12.5 12 12)"></g>'
    assert out["missing"] == '<rect fill=""/>'
    # The nested icon's own markup survives; the value inside it is still escaped.
    assert out["nested"].startswith('<span><svg fill="&quot;/&gt;&lt;img')
    assert out["nested"].endswith('"></svg></span>')
    assert "<img" not in out["nested"]
