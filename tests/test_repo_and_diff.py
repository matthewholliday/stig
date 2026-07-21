import pytest

from stig.diffutil import AnnotationTouchError, assert_no_annotation_lines, diff_hash
from stig.graph import ImportGraph
from stig.repo import DuplicateIDError


def test_assign_missing_ids(workrepo):
    repo, _ = workrepo
    repo.write("m.py", "# @goal(, status=open): first\n# @goal(, status=open): second\n")
    assigned = repo.assign_missing_ids()
    assert assigned == ["g01", "g02"]
    text = repo.read("m.py")
    assert "@goal(g01, status=open)" in text
    assert "@goal(g02, status=open)" in text


def test_assign_respects_existing_counters(workrepo):
    repo, _ = workrepo
    repo.write("m.py", "# @goal(g05, status=open): a\n# @goal(, status=open): b\n")
    assigned = repo.assign_missing_ids()
    assert assigned == ["g06"]


def test_duplicate_ids_error(workrepo):
    repo, _ = workrepo
    repo.write("a.py", "# @goal(g01, status=open): a\n")
    repo.write("b.py", "# @goal(g01, status=open): b\n")
    with pytest.raises(DuplicateIDError):
        repo.check_duplicates()


def test_diff_guard_rejects_annotation_lines():
    diff = (
        "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n"
        " x = 1\n+# @goal(g09, status=open): sneaky\n"
    )
    with pytest.raises(AnnotationTouchError):
        assert_no_annotation_lines(diff)


def test_diff_guard_allows_code():
    diff = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
    assert_no_annotation_lines(diff)  # no raise


def test_diff_guard_catches_hunks_with_no_at_header():
    """The applier tolerates a missing `@@` header, so the guard must too — a
    guard with a stricter parser than the applier is a bypass."""
    diff = (
        "--- a/x.py\n+++ b/x.py\n"
        "-# @constraint(c01, status=asserted): must hold\n"
        "+# @constraint(c01, status=verified): must hold\n"
    )
    with pytest.raises(AnnotationTouchError):
        assert_no_annotation_lines(diff)


def test_diff_guard_and_applier_see_the_same_lines(workrepo):
    """Whatever the guard passes is exactly what gets written: a headerless diff
    that forges a status must not reach disk."""
    repo, _ = workrepo
    original = "# @constraint(c01, status=asserted): must hold\nx = 1\n"
    repo.write("x.py", original)
    forged = (
        "--- a/x.py\n+++ b/x.py\n"
        "-# @constraint(c01, status=asserted): must hold\n"
        "+# @constraint(c01, status=verified): must hold\n"
    )
    with pytest.raises(AnnotationTouchError):
        assert_no_annotation_lines(forged)
    assert repo.read("x.py") == original


def test_diff_hash_stable():
    assert diff_hash("abc") == diff_hash("abc")
    assert len(diff_hash("abc")) == 12


def test_import_graph_hops():
    files = {
        "pkg/a.py": "from pkg import b\n",
        "pkg/b.py": "import os\n",
        "pkg/c.py": "from pkg import a\n",
    }
    g = ImportGraph(files)
    assert "pkg/b.py" in g.direct_imports("pkg/a.py")
    assert "pkg/c.py" in g.direct_importers("pkg/a.py")
