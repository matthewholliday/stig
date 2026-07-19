"""The structured channel's single parse point (SPEC §07).

Every handler response funnels through ``extract_json``, so a parsing gap here
is a gap in every activation.
"""

import pytest

from stig.handlers import HandlerParseError, _parse
from stig.models import ScriptedModel, extract_json


def test_bare_object():
    assert extract_json('{"diff": "", "updates": []}')["updates"] == []


def test_fenced_block():
    raw = 'Here is my answer:\n```json\n{"diff": "", "updates": [{"id": "g01"}]}\n```\nDone.'
    assert extract_json(raw)["updates"] == [{"id": "g01"}]


def test_nested_objects_survive_the_fence():
    raw = '```json\n{"a": {"b": {"c": 1}}}\n```'
    assert extract_json(raw)["a"]["b"]["c"] == 1


def test_braces_inside_a_string_do_not_end_the_object():
    """A diff value routinely contains braces and quotes — a scanner blind to
    JSON string context terminates the object early and loses the response."""
    raw = '{"diff": "+    d = {\\"k\\": [1]}\\n", "updates": []}'
    assert extract_json(raw)["diff"] == '+    d = {"k": [1]}\n'


def test_prose_around_the_object_is_ignored():
    raw = 'I considered {this} informally.\n\n{"diff": "", "updates": [], "x": 1}'
    assert extract_json(raw)["x"] == 1


def test_no_json_raises():
    with pytest.raises(ValueError):
        extract_json("no structured channel here")


def test_top_level_array_is_refused_not_silently_unwrapped():
    """Scanning on would find the first `{` inside the array and return one
    element as if it were the whole response — wrong data, no error."""
    with pytest.raises(ValueError):
        extract_json('```json\n[{"id": "g01", "status": "satisfied"}]\n```')


def test_second_fenced_block_does_not_break_extraction():
    raw = '```python\nx = 1\n```\nand then:\n```json\n{"diff": "", "updates": []}\n```'
    assert extract_json(raw) == {"diff": "", "updates": []}


# -- the handler contract on top of it --------------------------------------

def test_parse_raises_handler_error_on_garbage():
    with pytest.raises(HandlerParseError):
        _parse("I refuse to emit JSON.")


def test_parse_skips_malformed_entries_but_keeps_the_diff():
    """One bad update record must not cost the whole response."""
    raw = '{"diff": "d", "updates": [{"no_id": 1}, {"id": "g01", "status": "satisfied"}]}'
    result = _parse(raw)
    assert result.diff == "d"
    assert [u.id for u in result.updates] == ["g01"]


def test_parse_tolerates_missing_keys():
    result = _parse('{"diff": "x"}')
    assert result.diff == "x"
    assert result.updates == []
    assert result.new_annotations == []


@pytest.mark.parametrize(
    "raw",
    [
        '{"updates": 5}',
        '{"new_annotations": 7}',
        '{"updates": [{"id": "g01", "attrs": "not-an-object"}]}',
        '{"new_annotations": [{"kind": "goal", "attrs": [1, 2]}]}',
        '{"updates": "nope", "diff": "d"}',
    ],
)
def test_parse_survives_wrong_container_types(raw):
    """A contract violation must surface as a handler failure the scheduler can
    strike — never as a TypeError/AttributeError that aborts the loop."""
    try:
        _parse(raw)
    except HandlerParseError:
        pass  # acceptable: the scheduler turns this into a strike


def test_parse_collapses_a_multiline_body():
    """A body is written into a one-line comment; a newline would break the
    grammar and silently truncate the annotation."""
    result = _parse('{"updates": [{"id": "u01", "body": "line one\\nline two"}]}')
    assert result.updates[0].body == "line one line two"


def test_scripted_model_is_stateless_per_call():
    model = ScriptedModel(["a", "b"])
    assert model.complete("s", "u1") == "a"
    assert model.complete("s", "u2") == "b"
    with pytest.raises(RuntimeError):
        model.complete("s", "u3")
