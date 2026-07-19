from stig.annotations import parse_file
from stig.regions import (
    enforcing_test_exists,
    missing_enforcing_tests,
    region_has_executable_lines,
    region_hash,
    repo_structure_hash,
    resolve_region,
)

DECORATOR_STYLE = """\
# @constraint(c09, status=asserted): never hold db_lock across an await
async def fetch_all(query):
    conn = get()
    return conn.run(query)

def other():
    pass
"""


def test_region_attaches_to_next_definition():
    ann = parse_file(DECORATOR_STYLE, "x.py")[0]
    region = resolve_region(ann, DECORATOR_STYLE)
    assert region.kind == "definition"
    lines = DECORATOR_STYLE.splitlines()
    assert lines[region.start].startswith("async def fetch_all")
    # The region is the function body, not the remainder of the file.
    assert "def other" not in "\n".join(lines[region.start : region.end + 1])


def test_region_file_override():
    text = "# @constraint(c01, status=asserted, region=file): stdlib only\nimport os\n"
    ann = parse_file(text, "x.py")[0]
    region = resolve_region(ann, text)
    assert region.kind == "file"


def test_region_hash_changes_with_region():
    ann = parse_file(DECORATOR_STYLE, "x.py")[0]
    h1 = region_hash(ann, DECORATOR_STYLE)
    changed = DECORATOR_STYLE.replace("return conn.run(query)", "return conn.run(query, True)")
    h2 = region_hash(ann, changed)
    assert h1 != h2


def test_region_hash_ignores_annotation_edits():
    ann = parse_file(DECORATOR_STYLE, "x.py")[0]
    h1 = region_hash(ann, DECORATOR_STYLE)
    # Editing a decision/status line elsewhere in the region should not matter.
    with_status = DECORATOR_STYLE.replace("status=asserted", "status=verified")
    ann2 = parse_file(with_status, "x.py")[0]
    assert region_hash(ann2, with_status) == h1


def test_empty_region_warning_detection():
    text = "def f():\n    return 1\n\n# @goal(g01, status=open): nothing below\n"
    ann = parse_file(text, "x.py")[0]
    assert not region_has_executable_lines(ann, text)


def test_repo_structure_hash_changes_on_new_import():
    files = {"a.py": "import os\n"}
    h1 = repo_structure_hash(files)
    files["a.py"] = "import os\nimport sys\n"
    assert repo_structure_hash(files) != h1
    # A body edit that does not change imports leaves the structure hash stable.
    files["a.py"] = "import os\nimport sys\nx = 1\n"
    h3 = repo_structure_hash(files)
    files["a.py"] = "import os\nimport sys\nx = 2\n"
    assert repo_structure_hash(files) == h3


ENFORCING = {
    "tests/test_a.py": "def test_one():\n    pass\n",
    "tests/test_b.py": "def test_two():\n    pass\n",
}


def test_enforced_by_accepts_several_tests():
    assert enforcing_test_exists("test_one&test_two", ENFORCING)


def test_enforced_by_is_all_or_nothing():
    """A conjunction is only enforced if every named test exists."""
    assert not enforcing_test_exists("test_one&test_missing", ENFORCING)


def test_enforced_by_single_name_still_works():
    assert enforcing_test_exists("test_one", ENFORCING)
    assert enforcing_test_exists("tests/test_a.py::test_one", ENFORCING)
    assert not enforcing_test_exists("", ENFORCING)


def test_missing_enforcing_tests_names_the_culprit():
    """Errors should name the absent test, not echo the whole &-joined list."""
    assert missing_enforcing_tests("test_one&test_two", ENFORCING) == []
    assert missing_enforcing_tests("test_one&test_gone", ENFORCING) == ["test_gone"]
    assert missing_enforcing_tests("a&b", ENFORCING) == ["a", "b"]
