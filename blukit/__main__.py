"""Allow ``python -m blukit``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
