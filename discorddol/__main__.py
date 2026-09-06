"""CLI surface for discorddol: ``python -m discorddol <verb> ...``.

One line of adapter over the SSOT verb list in :mod:`discorddol.tools`. Adding a verb
there adds it here, with no change to this file.
"""

import cw

from .tools import DISPATCH_FUNCS


def main():
    """Entry point for the ``discorddol`` console script."""
    return cw.dispatch(DISPATCH_FUNCS, prog="discorddol")


if __name__ == "__main__":
    raise SystemExit(main())
