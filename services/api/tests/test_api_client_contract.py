"""Keep the checked TypeScript client synchronized with FastAPI's OpenAPI schema."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_checked_typescript_client_matches_openapi_contract() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "scripts/generate-api-client.py", "--check"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_generated_typescript_client_typechecks() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    compiler = repository_root / "apps" / "web" / "node_modules" / ".bin" / "tsc"
    assert compiler.is_file(), "Run npm --prefix apps/web ci before running the API contract tests."
    result = subprocess.run(
        [str(compiler), "--noEmit", "-p", "packages/api-client/tsconfig.json"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_client_exposes_the_checked_sse_recovery_boundary() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    generated_client_path = repository_root / "packages" / "api-client" / "src" / "generated.ts"
    generated_client = generated_client_path.read_text()

    assert "CaseEventStreamEvent" in generated_client
    assert "streamCaseEvents" in generated_client
    assert "Last-Event-ID" in generated_client
