"""Container health probe that verifies the worker's Temporal-backed readiness."""

from __future__ import annotations

import sys
from urllib.error import URLError
from urllib.request import urlopen


def main() -> int:
    """Return non-zero until the local worker readiness endpoint is healthy."""
    try:
        with urlopen("http://127.0.0.1:8081/readyz", timeout=2) as response:
            return 0 if response.status == 200 else 1
    except URLError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
