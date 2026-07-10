#!/bin/bash
# Double-clickable installer shipped inside the stig .dmg.
#
# Copies the `stig` binary that sits next to this script into a directory on
# your PATH. Prefers /usr/local/bin (system-wide, may prompt for your password)
# and falls back to ~/.local/bin (no password needed) when that is not writable.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/stig"

if [[ ! -x "$BIN" ]]; then
    echo "error: could not find the stig binary next to this installer." >&2
    echo "Make sure you opened this from the mounted disk image." >&2
    exit 1
fi

echo "Installing stig from: $BIN"

install_to() {
    local dest_dir="$1"
    local sudo_cmd="$2"
    $sudo_cmd mkdir -p "$dest_dir"
    $sudo_cmd cp "$BIN" "$dest_dir/stig"
    $sudo_cmd chmod +x "$dest_dir/stig"
    # Strip the quarantine flag so Gatekeeper does not block the copy.
    $sudo_cmd xattr -d com.apple.quarantine "$dest_dir/stig" 2>/dev/null || true
    echo "Installed to: $dest_dir/stig"
}

SYSTEM_DIR="/usr/local/bin"
USER_DIR="$HOME/.local/bin"

if [[ -w "$SYSTEM_DIR" ]] || mkdir -p "$SYSTEM_DIR" 2>/dev/null; then
    install_to "$SYSTEM_DIR" ""
    TARGET="$SYSTEM_DIR"
elif command -v sudo >/dev/null 2>&1 && sudo -v 2>/dev/null; then
    install_to "$SYSTEM_DIR" "sudo"
    TARGET="$SYSTEM_DIR"
else
    install_to "$USER_DIR" ""
    TARGET="$USER_DIR"
fi

echo
if command -v stig >/dev/null 2>&1 && [[ "$(command -v stig)" == "$TARGET/stig" ]]; then
    echo "✅ Done. Run 'stig --help' to get started."
else
    echo "✅ Installed to $TARGET/stig"
    case ":$PATH:" in
        *":$TARGET:"*) : ;;
        *)
            echo
            echo "⚠️  $TARGET is not on your PATH yet. Add it with:"
            echo "    echo 'export PATH=\"$TARGET:\$PATH\"' >> ~/.zshrc && source ~/.zshrc"
            ;;
    esac
fi

echo
echo "Press Return to close this window."
read -r _
