"""Text-level XML parse / check / format (app/xml/xml_text.py).

The two behaviours worth guarding here are the ones that silently destroy user
data when they regress: dropping a `<!-- START MOD: @X -->` marker (which makes
a mod's merged content unremovable) and rewriting the author's own XML
declaration in a file they were only asking to indent.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.xml import xml_text as xt

DOC = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<!-- header -->\n"
    "<types>\n"
    '\t<type name="X">\n'
    "\t\t<nominal>1</nominal>\n"
    "\t</type>\n"
    "</types>\n"
    "<!-- trailer -->\n"
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_xml_text_matches_load_xml(tmp_path):
    from app.xml.xml_utils import load_xml

    path = tmp_path / "types.xml"
    path.write_text(DOC, encoding="utf-8")

    from_text = xt.parse_xml_text(DOC).getroot()
    from_file = load_xml(path).getroot()
    assert from_text.tag == from_file.tag
    assert [c.tag for c in from_text] == [c.tag for c in from_file]


def test_parse_xml_text_preserves_comments_only_when_asked():
    plain = xt.parse_xml_text("<a><!-- c --><b/></a>").getroot()
    kept = xt.parse_xml_text("<a><!-- c --><b/></a>", preserve_comments=True).getroot()

    assert [c.tag for c in plain] == ["b"]
    assert len(kept) == 2


def test_parse_xml_text_strips_leading_junk_only_when_asked():
    junk = '<!-- note -->\n<?xml version="1.0"?>\n<a/>'
    assert xt.parse_xml_text(junk).getroot().tag == "a"
    with pytest.raises(ET.ParseError):
        xt.parse_xml_text(junk, strip_leading_junk=False)


def test_entity_bomb_is_reported_as_a_parse_error():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaa">'
        '<!ENTITY b "&a;&a;&a;">]><x>&b;</x>'
    )
    with pytest.raises(ET.ParseError):
        xt.parse_xml_text(bomb)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pattern,hint", xt.XML_ERROR_HINTS)
def test_friendly_xml_error_maps_every_hint(pattern, hint):
    assert xt.friendly_xml_error(f"{pattern}: line 3, column 1") == hint


def test_friendly_xml_error_falls_back_to_the_message_without_the_position():
    assert xt.friendly_xml_error("something odd: line 3, column 1") == "something odd"


def test_describe_reports_1_based_line_and_column():
    try:
        xt.parse_xml_text("<a>\n<b>\n</c>\n</a>")
    except ET.ParseError as exc:
        problem = xt.describe_xml_error(exc)
    # expat's column is 0-based and points at the offending tag name in `</c>`.
    assert (problem.line, problem.column) == (3, 3)
    assert problem.summary == "Line 3, column 3 — closing tag doesn't match opening tag"


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


def test_check_accepts_a_fragment_only_in_fragment_mode():
    fragment = '<type name="X"/><type name="Y"/>'
    assert xt.check_xml_text(fragment) is not None
    assert xt.check_xml_text(fragment, fragment=True) is None


def test_fragment_error_lines_are_not_shifted_by_the_wrapper():
    problem = xt.check_xml_text('<type name="X"/>\n<type>\n</typo>', fragment=True)
    assert problem.line == 3


def test_check_rejects_content_before_the_declaration():
    """Check has to agree with save_raw, which does not strip leading junk."""
    assert xt.check_xml_text('<!-- note -->\n<?xml version="1.0"?>\n<a/>') is not None


def test_inspect_reports_the_root_and_the_element_count():
    info = xt.inspect_xml_text(DOC)
    assert info.ok and info.root_tag == "types"
    assert info.elements == 3  # types + type + nominal, comments excluded
    assert xt.inspect_xml_text('<a/><b/>', fragment=True).root_tag == ""


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def test_format_keeps_comments_above_inside_and_after_the_root():
    text = xt.format_xml_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!-- header -->\n'
        '<types><!-- inner --><type name="X"/></types>\n<!-- trailer -->\n'
    ).text
    assert "<!-- header -->" in text
    assert "<!-- inner -->" in text
    assert "<!-- trailer -->" in text


def test_format_keeps_a_mod_marker_block():
    text = xt.format_xml_text(
        "<eventposdef><!-- START MOD: @Bikes --><event name='E'/>"
        "<!-- END MOD: @Bikes --></eventposdef>"
    ).text
    assert "<!-- START MOD: @Bikes -->" in text and "<!-- END MOD: @Bikes -->" in text


def test_format_does_not_dedent_inside_a_multiline_comment():
    """The wrapper's indent level lives in ET's whitespace, not in content.

    Stripping a leading indent off every output *line* ate a tab from the
    second line of every multi-line comment — which is most of the starter
    templates.
    """
    source = "<types>\n\t<!-- first line\n\t     second line -->\n</types>\n"
    assert xt.format_xml_text(source).text == source


def test_format_preserves_the_authors_declaration():
    text = xt.format_xml_text("<?xml version='1.0' encoding='utf-8'?>\n<a><b/></a>").text
    assert text.startswith("<?xml version='1.0' encoding='utf-8'?>\n")


def test_format_never_invents_a_declaration():
    assert not xt.format_xml_text("<a><b/></a>").text.startswith("<?xml")


def test_format_a_rootless_fragment_stays_rootless():
    result = xt.format_xml_text('<type name="X"/><type name="Y"><nominal>1</nominal></type>')
    assert result.ok
    assert result.root_tag == ""  # more than one top-level element
    assert result.text == '<type name="X" />\n<type name="Y">\n\t<nominal>1</nominal>\n</type>\n'


def test_format_is_idempotent_and_uses_tabs_and_one_trailing_newline():
    once = xt.format_xml_text("<a><b><c/></b></a>").text
    assert once == "<a>\n\t<b>\n\t\t<c />\n\t</b>\n</a>\n"
    assert xt.format_xml_text(once).text == once
    assert xt.format_xml_text(once).changed is False


def test_format_preserves_attributes_and_text():
    text = xt.format_xml_text('<a><b x="1" y="two">val</b></a>').text
    assert '<b x="1" y="two">val</b>' in text


def test_format_returns_the_input_untouched_for_broken_xml():
    broken = "<types>\n<type>\n</typo>\n</types>"
    result = xt.format_xml_text(broken)
    assert result.text == broken and result.changed is False
    assert result.problem.line == 3


def test_format_error_line_accounts_for_the_declaration_it_sliced_off():
    result = xt.format_xml_text('<?xml version="1.0"?>\n<a>\n<b>\n</c>\n</a>')
    assert result.problem.line == 4


def test_format_refuses_text_outside_an_element():
    result = xt.format_xml_text("stray words\n<a/>")
    assert result.problem is not None and result.text == "stray words\n<a/>"


def test_format_leaves_empty_input_alone():
    assert xt.format_xml_text("   ").text == "   "
    assert xt.format_xml_text("").problem is None


def test_xml_syntax_error_stringifies_to_the_summary():
    problem = xt.check_xml_text("<a>")
    assert str(xt.XmlSyntaxError(problem)) == problem.summary


def test_a_lone_surrogate_is_a_problem_not_a_crash():
    """JSON's `"\\ud83d"` reaches /api/xml/check and /format as a lone
    surrogate, which the parser cannot encode: both raised UnicodeEncodeError,
    a 500 from two routes that promise `ok: false`."""
    text = "<a>\ud83d</a>"
    assert xt.inspect_xml_text(text).ok is False
    assert xt.check_xml_text(text) is not None
    assert xt.check_xml_text(text, fragment=True) is not None
    result = xt.format_xml_text(text)
    assert result.problem is not None and result.text == text


def test_format_accepts_a_bom_without_a_declaration():
    text = "\ufeff<types>\n<type name='a'/>\n</types>\n"
    assert xt.inspect_xml_text(text).ok is True
    result = xt.format_xml_text(text)
    assert result.problem is None
    assert result.text == '<types>\n\t<type name="a" />\n</types>\n'


def test_a_fragment_with_a_declaration_reports_the_users_line():
    """An error on line 3 of a manual file that starts
    with <?xml ...?> was reported as line 1."""
    from app.xml import xml_text as xt

    text = '<?xml version="1.0" encoding="UTF-8"?>\n<types>\n<broken\n</types>\n'
    # The parser notices the bad tag on the line after it; fragment and strict agree.
    assert xt.check_xml_text(text, fragment=True).line == xt.check_xml_text(text).line == 4
    without = '<types>\n<broken\n</types>\n'
    assert xt.check_xml_text(without, fragment=True).line == xt.check_xml_text(without).line == 3
