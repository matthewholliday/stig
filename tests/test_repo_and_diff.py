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
