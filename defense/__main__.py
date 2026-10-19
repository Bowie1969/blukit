"""``python -m defense`` entrypoint."""

import sys

from .server import serve


if __name__ == "__main__":
    sys.exit(serve())
