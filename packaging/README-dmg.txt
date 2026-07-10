Stig — macOS install
=====================

This disk image contains a self-contained `stig` command-line tool. No Python
installation is required.

To install:

  1. Double-click "install.command".
     (It copies `stig` onto your PATH — into /usr/local/bin, or ~/.local/bin
      if that is not writable.)

  2. Open a new Terminal window and run:

       stig --help

If macOS blocks the installer the first time ("cannot be opened because it is
from an unidentified developer"), right-click it, choose Open, then confirm.

Manual install:

  Copy the `stig` binary anywhere on your PATH, e.g.:

     cp stig /usr/local/bin/stig && chmod +x /usr/local/bin/stig

Uninstall:

     rm "$(command -v stig)"
