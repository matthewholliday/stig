# PyInstaller spec — builds a single-file, self-contained `stig` CLI binary.
# Used by .github/workflows/build-dmg.yml. Build with:
#   pyinstaller packaging/stig.spec --distpath dist --workpath build/pyinstaller
#
# The optional `anthropic` extra is only needed for live model calls; it is
# collected when present so a released binary can drive real activations, but
# the build does not fail when it is absent.

from PyInstaller.utils.hooks import collect_submodules

hidden = []
datas = []
try:
    hidden += collect_submodules("anthropic")
except Exception:
    pass

a = Analysis(
    ["entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="stig",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
