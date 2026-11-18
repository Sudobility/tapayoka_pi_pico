"""Device boot entry point for the Tapayoka Pico W.

MicroPython runs `/main.py` on boot. The firmware itself lives under `/src` as a
package (its modules import each other with relative imports, e.g.
`from .config import ...`), and the vendored `tapayoka_pi_core` is imported
absolutely (`import tapayoka_pi_core`). This launcher makes both resolve:

  * `/src` is added to `sys.path`, so the absolute `import tapayoka_pi_core`
    (which lives at `/src/tapayoka_pi_core`) resolves.
  * `src` is importable as a package from the filesystem root, so
    `import src.main` runs the firmware's entry module with its relative imports
    intact.

Deploy: copy this file to the device root and the `src/` directory to `/src`
(see README / CLAUDE.md). `src/main.py` is NOT run directly — its relative
imports only resolve when imported as part of the `src` package, which is what
this launcher does.
"""

import sys

if "/src" not in sys.path:
    sys.path.insert(0, "/src")

import src.main

src.main.main()
