"""Frozen-binary entry point for the `stig` CLI.

PyInstaller freezes this module as the program's start; it just delegates to
the package's real entry point so the bundled binary behaves exactly like
`python -m stig` / the `stig` console script.
"""

from stig.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
