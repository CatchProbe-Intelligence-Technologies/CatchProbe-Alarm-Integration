"""Enable ``python -m alarm_integration``."""

import sys

from alarm_integration.cli import main

if __name__ == "__main__":
    sys.exit(main())
