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


def test_header_text_round_trips_delimiter_bearing_attrs():
    """Rendering must never produce a header this module cannot parse.

    Regression: a handler set ``enforced_by`` to a comma-separated list of test
    names. The comma parsed as the start of a second attribute, so every later
    parse_repo() raised GrammarError and no stig command could run until a human
    edited the file by hand.
    """
    for value in ["a, b", "a)b", "a(b", "multi\nline   value", "x, y, z"]:
        a = parse_file("# @goal(g01, status=open): body\n", "x.py")[0]
        a.attrs["enforced_by"] = value
        rendered = a.header_text()
        reparsed = parse_file(rendered, "x.py")
        assert len(reparsed) == 1, f"{value!r} rendered an unparseable header: {rendered}"
        # And rendering the reparsed annotation is a fixpoint.
        assert parse_file(reparsed[0].header_text(), "x.py")[0].attrs == reparsed[0].attrs


def test_comma_in_attr_folds_onto_the_and_separator():
    a = parse_file("# @goal(g01, status=open): body\n", "x.py")[0]
    a.attrs["enforced_by"] = "test_one, test_two"
    assert parse_file(a.header_text(), "x.py")[0].attrs["enforced_by"] == "test_one&test_two"


def test_malformed_attr_key_cannot_escape_the_grammar():
    a = parse_file("# @goal(g01, status=open): body\n", "x.py")[0]
    a.attrs["bad key,x"] = "v"
    assert len(parse_file(a.header_text(), "x.py")) == 1
