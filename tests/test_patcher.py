import pytest

from stig.patcher import PatchError, apply_diff


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


def test_whitespace_tolerant_match(workrepo):
    repo, _ = workrepo
    repo.write("m.py", "def f():\n    return 1   \n")  # trailing spaces in file
    diff = "--- a/m.py\n+++ b/m.py\n@@\n-    return 1\n+    return 2\n"
    apply_diff(repo, diff)
    assert "return 2" in repo.read("m.py")
