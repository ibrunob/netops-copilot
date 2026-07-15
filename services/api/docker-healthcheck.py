"""Minimal container-local health probe with no shell or third-party dependency."""

from urllib.request import urlopen

with urlopen("http://127.0.0.1:8000/healthz", timeout=2) as response:
    if response.status != 200:
        raise SystemExit(f"Unexpected health status: {response.status}")
