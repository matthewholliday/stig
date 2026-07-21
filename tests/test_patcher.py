import pytest

from stig.annotations import AnnotationTouchError
from stig.patcher import PatchError, apply_diff
from stig.repo import PathEscapeError

# -- the diff channel may not reach annotations by ANY route -----------------
#
# Marker inspection alone is necessarily partial: a whole-file overwrite, a
# deletion, or an unmarked line carries no +/- marker at all. These are the
# routes that bypass a marker-only guard.

ANNOTATED = "# @constraint(c01, status=asserted): never hold the lock\ndef f():\n    return 1\n"


def test_deletion_cannot_remove_annotations(workrepo):
    repo, _ = workrepo
    repo.write("mod.py", ANNOTATED)
    with pytest.raises(AnnotationTouchError):
        apply_diff(repo, "--- a/mod.py\n+++ /dev/null\n")
    assert repo.exists("mod.py")
    assert repo.read("mod.py") == ANNOTATED


def test_dev_null_overwrite_cannot_forge_a_status(workrepo):
    repo, _ = workrepo
    repo.write("mod.py", ANNOTATED)
    forged = (
        "--- /dev/null\n+++ b/mod.py\n@@\n+def f():\n"
        "# @constraint(c01, status=verified, region_hash=deadbeef): never hold the lock\n"
        "+    return 1\n"
    )
    with pytest.raises(PatchError):  # a "new file" that already exists
        apply_diff(repo, forged)
    assert repo.read("mod.py") == ANNOTATED


def test_unmarked_line_cannot_smuggle_an_annotation_into_a_new_file(workrepo):
    """An unmarked line still reaches disk, so the guard must see it."""
    repo, _ = workrepo
    diff = (
        "--- /dev/null\n+++ b/new.py\n@@\n+def g():\n"
        "# @goal(g99, status=satisfied): forged\n+    return 1\n"
    )
    with pytest.raises(AnnotationTouchError):
        apply_diff(repo, diff)
    assert not repo.exists("new.py")


def test_headerless_hunk_cannot_rewrite_a_status(workrepo):
    repo, _ = workrepo
    repo.write("mod.py", ANNOTATED)
    diff = (
        "--- a/mod.py\n+++ b/mod.py\n"
        "-# @constraint(c01, status=asserted): never hold the lock\n"
        "+# @constraint(c01, status=verified): never hold the lock\n"
    )
    with pytest.raises(AnnotationTouchError):
        apply_diff(repo, diff)
    assert repo.read("mod.py") == ANNOTATED


def test_code_next_to_an_annotation_still_applies(workrepo):
    """The guard must not be so broad that ordinary work is blocked."""
    repo, _ = workrepo
    repo.write("mod.py", ANNOTATED)
    apply_diff(repo, "--- a/mod.py\n+++ b/mod.py\n@@\n-    return 1\n+    return 2\n")
    assert "return 2" in repo.read("mod.py")
    assert "status=asserted" in repo.read("mod.py")  # untouched


def test_a_rejected_section_leaves_earlier_sections_unwritten(workrepo):
    """A diff applies whole or not at all."""
    repo, _ = workrepo
    repo.write("a.py", "x = 1\n")
    repo.write("b.py", ANNOTATED)
    diff = (
        "--- a/a.py\n+++ b/a.py\n@@\n-x = 1\n+x = 2\n"
        "--- a/b.py\n+++ /dev/null\n"
    )
    with pytest.raises(AnnotationTouchError):
        apply_diff(repo, diff)
    assert repo.read("a.py") == "x = 1\n"  # the good section did not land
    assert repo.exists("b.py")


def test_path_traversal_is_refused(workrepo):
    """Diff paths are untrusted input; they may not escape the repository."""
    repo, _ = workrepo
    with pytest.raises(PathEscapeError):
        apply_diff(repo, "--- a/../victim.txt\n+++ /dev/null\n")
    with pytest.raises(PathEscapeError):
        apply_diff(repo, "--- /etc/hosts\n+++ /dev/null\n")


def test_applies_bare_at_header(workrepo):
    """A diff with a bare @@ header (no line numbers) still applies."""
    repo, _ = workrepo
    repo.write("m.py", "def _placeholder():\n    return None\n")
    diff = (
        "--- a/m.py\n+++ b/m.py\n@@\n"
        "-def _placeholder():\n-    return None\n"
        "+def add(a, b):\n+    return a + b\n"
    )
    apply_diff(repo, diff)
    assert repo.read("m.py") == "def add(a, b):\n    return a + b\n"


def test_applies_with_wrong_line_numbers(workrepo):
    """Hunk-header line numbers are ignored; context locates the hunk."""
    repo, _ = workrepo
    repo.write("m.py", "x = 1\ny = 2\nz = 3\n")
    diff = "--- a/m.py\n+++ b/m.py\n@@ -99,1 +99,1 @@\n y = 2\n-z = 3\n+z = 4\n"
    apply_diff(repo, diff)
    assert repo.read("m.py") == "x = 1\ny = 2\nz = 4\n"


def test_creates_new_file(workrepo):
    repo, _ = workrepo
    diff = (
        "diff --git a/t.py b/t.py\nnew file mode 100644\n"
        "--- /dev/null\n+++ b/t.py\n@@ -0,0 +1,2 @@\n"
        "+def test_ok():\n+    assert True\n"
    )
    apply_diff(repo, diff)
    assert repo.read("t.py") == "def test_ok():\n    assert True\n"


def test_missing_context_raises(workrepo):
    repo, _ = workrepo
    repo.write("m.py", "a = 1\n")
    diff = "--- a/m.py\n+++ b/m.py\n@@\n-nonexistent line\n+replacement\n"
    with pytest.raises(PatchError):
        apply_diff(repo, diff)


def test_deletes_a_file(workrepo):
    """`+++ /dev/null` removes the file rather than blanking it."""
    repo, _ = workrepo
    repo.write("dead.py", "x = 1\ny = 2\n")
    diff = (
        "diff --git a/dead.py b/dead.py\ndeleted file mode 100644\n"
        "--- a/dead.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n-y = 2\n"
    )
    changed = apply_diff(repo, diff)
    assert changed == ["dead.py"]
    assert not repo.exists("dead.py")


def test_preserves_a_missing_trailing_newline(workrepo):
    repo, _ = workrepo
    repo.write("m.py", "a = 1\nb = 2")  # deliberately no trailing newline
    diff = "--- a/m.py\n+++ b/m.py\n@@\n-b = 2\n+b = 3\n"
    apply_diff(repo, diff)
    assert repo.read("m.py") == "a = 1\nb = 3"


def test_whitespace_tolerant_match(workrepo):
    repo, _ = workrepo
    repo.write("m.py", "def f():\n    return 1   \n")  # trailing spaces in file
    diff = "--- a/m.py\n+++ b/m.py\n@@\n-    return 1\n+    return 2\n"
    apply_diff(repo, diff)
    assert "return 2" in repo.read("m.py")
