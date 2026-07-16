from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from netops_api.application.artifacts import (
    ArtifactObjectMetadata,
    ArtifactObjectMissingError,
    ArtifactUploadRequest,
    FakeArtifactStore,
    MinioArtifactStore,
)
from netops_api.core.config import ArtifactStoreSettings

NOW = datetime(2026, 7, 16, tzinfo=UTC)
ORGANIZATION_ID = UUID("00000000-0000-0000-0000-000000000001")
CASE_ID = UUID("00000000-0000-0000-0000-000000000002")
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000003")
SHA256 = "a" * 64


def upload_request() -> ArtifactUploadRequest:
    return ArtifactUploadRequest(
        organization_id=ORGANIZATION_ID,
        case_id=CASE_ID,
        artifact_id=ARTIFACT_ID,
        content_type="text/plain",
        content_length=42,
        sha256_hex=SHA256,
    )


class RecordingPresigner:
    def __init__(self) -> None:
        self.call: tuple[str, dict[str, object], int, str] | None = None

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, object],
        ExpiresIn: int,
        HttpMethod: str,
    ) -> str:
        self.call = (ClientMethod, Params, ExpiresIn, HttpMethod)
        return "https://minio.example.test/a-capability?signature=private"


def test_fake_store_returns_short_lived_capability_without_an_object_key_field() -> None:
    store = FakeArtifactStore(settings=ArtifactStoreSettings(enabled=True, presign_ttl_seconds=60))

    result = store.presign_upload(upload_request(), now=NOW)

    assert store.requests == [upload_request()]
    assert result.expires_at == NOW + timedelta(seconds=60)
    assert result.required_headers == (
        ("content-type", "text/plain"),
        ("x-amz-meta-sha256", SHA256),
    )
    assert not hasattr(result, "object_key")
    assert "capability=test-only" not in repr(result)


def test_minio_adapter_keeps_private_key_out_of_result_but_scopes_presign() -> None:
    client = RecordingPresigner()
    store = MinioArtifactStore(
        client=client,
        settings=ArtifactStoreSettings(
            enabled=True,
            bucket="netops-artifacts",
            presign_ttl_seconds=120,
        ),
    )

    result = store.presign_upload(upload_request(), now=NOW)

    assert client.call is not None
    method, params, expires_in, http_method = client.call
    assert (method, expires_in, http_method) == ("put_object", 120, "PUT")
    assert params["Bucket"] == "netops-artifacts"
    assert params["Key"] == (
        "organizations/00000000-0000-0000-0000-000000000001/"
        "cases/00000000-0000-0000-0000-000000000002/"
        "artifacts/00000000-0000-0000-0000-000000000003"
    )
    assert not hasattr(result, "object_key")
    assert "organizations/" not in repr(result)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"content_length": 0}, "content_length"),
        ({"sha256_hex": "A" * 64}, "sha256_hex"),
        ({"content_type": " "}, "content_type"),
    ],
)
def test_upload_intent_rejects_unbounded_or_ambiguous_metadata(
    kwargs: dict[str, object], message: str
) -> None:
    values = {
        "organization_id": ORGANIZATION_ID,
        "artifact_id": ARTIFACT_ID,
        "content_type": "application/octet-stream",
        "content_length": 1,
        "sha256_hex": SHA256,
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=message):
        ArtifactUploadRequest(**values)  # type: ignore[arg-type]


def test_disabled_store_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        FakeArtifactStore(settings=ArtifactStoreSettings(enabled=False)).presign_upload(
            upload_request(), now=NOW
        )


def test_fake_store_head_returns_only_attested_metadata() -> None:
    store = FakeArtifactStore()
    metadata = ArtifactObjectMetadata(
        content_type="text/plain",
        content_length=42,
        sha256_hex=SHA256,
        encryption_key_reference="kms:local-test",
    )
    store.objects[ARTIFACT_ID] = metadata

    assert store.head_uploaded_object(upload_request()) == metadata


def test_fake_store_head_fails_when_no_uploaded_object_exists() -> None:
    with pytest.raises(ArtifactObjectMissingError):
        FakeArtifactStore().head_uploaded_object(upload_request())
