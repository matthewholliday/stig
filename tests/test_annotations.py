from stig.annotations import GrammarError, is_annotation_line, parse_file, parse_inside


def test_parse_basic_header():
    text = "# @goal(g04, status=open, after=g02): expose a streaming variant\n"
    anns = parse_file(text, "x.py")
    assert len(anns) == 1
    a = anns[0]
    assert a.kind == "goal"
    assert a.id == "g04"
    assert a.status == "open"
    assert a.attrs["after"] == "g02"
    assert a.full_body == "expose a streaming variant"


def test_continuation_lines():
    text = (
        "# @decision(d02, status=recorded): chose sqlite over postgres — single-user tool,\n"
        "#   .. zero-config install matters more than concurrent writes\n"
        "x = 1\n"
    )
    anns = parse_file(text, "x.py")
    assert len(anns) == 1
    a = anns[0]
    assert a.start_line == 0
    assert a.end_line == 1
    assert "zero-config install" in a.full_body


def test_human_added_without_id():
    text = "# @goal(, status=open): do the thing\n"
    a = parse_file(text, "x.py")[0]
    assert a.id is None
    assert a.status == "open"


def test_header_render_roundtrip():
    text = "    # @constraint(c09, status=asserted): never hold db_lock across an await"
    a = parse_file(text, "x.py")[0]
    assert a.header_text() == text


def test_parse_inside_grammar_error():
    try:
        parse_inside("g01, malformed")
    except GrammarError:
        return
    raise AssertionError("expected GrammarError")


def test_is_annotation_line():
    assert is_annotation_line("# @goal(g01, status=open): x")
    assert is_annotation_line("#   .. continuation")
    assert not is_annotation_line("x = 1  # a comment")


def test_strikes_view():
    a = parse_file("# @goal(g01, status=open, strikes=2): x\n", "x.py")[0]
    assert a.strikes == 2
    a.strikes = 3
    assert a.attrs["strikes"] == "3"
