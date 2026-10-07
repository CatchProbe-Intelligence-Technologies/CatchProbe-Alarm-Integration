"""Backwards-compatible entry point.

The implementation now lives in the :mod:`alarm_integration` package. This shim
lets ``python main.py ...`` keep working; prefer ``python -m alarm_integration``
or the installed ``alarm-integration`` console script.
"""

import sys

from alarm_integration.cli import main

if __name__ == "__main__":
    sys.exit(main())
