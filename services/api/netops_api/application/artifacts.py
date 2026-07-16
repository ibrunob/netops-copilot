"""Typed, deliberately narrow boundary for private artifact-object storage.

This module does not persist artifact metadata or expose an HTTP route.  It is the
M4 foundation those later layers will use to request short-lived uploads without
leaking raw object keys, artifact bytes, or storage credentials into application
logs or public response models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from netops_api.core.config import ArtifactStoreSettings


@dataclass(frozen=True, slots=True)
class ArtifactUploadRequest:
    """Server-authorized upload intent; it deliberately carries no artifact bytes."""

    organization_id: UUID
    artifact_id: UUID
    content_type: str
    content_length: int
    sha256_hex: str
    case_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.content_type.strip() or len(self.content_type) > 255:
            raise ValueError("content_type must contain 1 to 255 non-blank characters.")
        if not 1 <= self.content_length <= 100 * 1024 * 1024:
            raise ValueError("content_length must be between 1 byte and 100 MiB.")
        if len(self.sha256_hex) != 64 or any(
            char not in "0123456789abcdef" for char in self.sha256_hex
        ):
            raise ValueError("sha256_hex must be a lowercase SHA-256 digest.")


@dataclass(frozen=True, slots=True, repr=False)
class PresignedArtifactUpload:
    """Short-lived upload capability with no raw object-key field.

    ``upload_url`` is a bearer capability and is intentionally hidden from
    ``repr``.  It may be returned only by a future authenticated API route to the
    principal that initiated this request; callers must not log it.
    """

    upload_url: str = field(repr=False)
    required_headers: tuple[tuple[str, str], ...]
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.upload_url.startswith(("http://", "https://")):
            raise ValueError("upload_url must be an absolute HTTP(S) URL.")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware.")
        for name, value in self.required_headers:
            if not name.strip() or not value.strip():
                raise ValueError("required headers must have non-blank names and values.")


@dataclass(frozen=True, slots=True)
class ArtifactObjectMetadata:
    """Metadata read from an uploaded object without retrieving artifact bytes.

    ``encryption_key_reference`` is deliberately an opaque store-side reference.
    The API never exposes it and refuses to finalize an object that the store
    cannot attest is encrypted.
    """

    content_type: str
    content_length: int
    sha256_hex: str
    encryption_key_reference: str

    def __post_init__(self) -> None:
        if not self.content_type.strip() or len(self.content_type) > 255:
            raise ValueError("content_type must contain 1 to 255 non-blank characters.")
        if not 1 <= self.content_length <= 100 * 1024 * 1024:
            raise ValueError("content_length must be between 1 byte and 100 MiB.")
        if len(self.sha256_hex) != 64 or any(
            char not in "0123456789abcdef" for char in self.sha256_hex
        ):
            raise ValueError("sha256_hex must be a lowercase SHA-256 digest.")
        if not self.encryption_key_reference.strip() or len(self.encryption_key_reference) > 500:
            raise ValueError("encryption_key_reference must contain 1 to 500 non-blank characters.")


class ArtifactObjectMissingError(LookupError):
    """The store cannot find an expected object without reading any bytes."""


class ArtifactStore(Protocol):
    """Port for granting an upload capability to a single immutable artifact."""

    def presign_upload(
        self, request: ArtifactUploadRequest, *, now: datetime
    ) -> PresignedArtifactUpload:
        """Return a short-lived upload capability without exposing the object key."""

    def head_uploaded_object(self, request: ArtifactUploadRequest) -> ArtifactObjectMetadata:
        """Attest uploaded metadata without downloading or returning artifact bytes.

        Implementations raise :class:`ArtifactObjectMissingError` when the
        capability has not produced an object yet. Other store failures are
        intentionally not collapsed into "missing" so callers can fail closed.
        """


class S3PresigningClient(Protocol):
    """Minimal subset shared by boto3 and compatible MinIO clients."""

    def generate_presigned_url(
        self,
        ClientMethod: str,
        Params: dict[str, object],
        ExpiresIn: int,
        HttpMethod: str,
    ) -> str: ...


class S3HeadClient(Protocol):
    """Minimal private S3 operation used only for completion verification."""

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]: ...


class MinioArtifactStore:
    """S3-compatible adapter seam; credentials stay inside the injected client."""

    def __init__(
        self,
        *,
        client: S3PresigningClient,
        settings: ArtifactStoreSettings,
        head_client: S3HeadClient | None = None,
    ) -> None:
        self._client = client
        self._head_client = head_client or client
        self._settings = settings

    def presign_upload(
        self, request: ArtifactUploadRequest, *, now: datetime
    ) -> PresignedArtifactUpload:
        _require_utc(now)
        if not self._settings.enabled:
            raise RuntimeError("Artifact storage is disabled.")
        # The key is private adapter state. It is never part of the request/result
        # types, returned separately, or logged. UUID components are deliberately
        # opaque and avoid filenames or other potentially sensitive user input.
        object_key = _object_key(request)
        upload_url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._settings.bucket,
                "Key": object_key,
                "ContentType": request.content_type.strip(),
                "ContentLength": request.content_length,
                "Metadata": {"sha256": request.sha256_hex},
            },
            ExpiresIn=self._settings.presign_ttl_seconds,
            HttpMethod="PUT",
        )
        return PresignedArtifactUpload(
            upload_url=upload_url,
            required_headers=(
                ("content-type", request.content_type.strip()),
                ("x-amz-meta-sha256", request.sha256_hex),
            ),
            expires_at=now.astimezone(UTC) + timedelta(seconds=self._settings.presign_ttl_seconds),
        )

    def head_uploaded_object(self, request: ArtifactUploadRequest) -> ArtifactObjectMetadata:
        if not self._settings.enabled:
            raise RuntimeError("Artifact storage is disabled.")
        client = self._head_client
        head_object = getattr(client, "head_object", None)
        if not callable(head_object):
            raise RuntimeError("Artifact storage does not support metadata verification.")
        try:
            response = head_object(Bucket=self._settings.bucket, Key=_object_key(request))
        except Exception as exc:  # SDK-specific not-found errors are adapter details.
            if _is_not_found(exc):
                raise ArtifactObjectMissingError(request.artifact_id) from exc
            raise RuntimeError("Artifact storage metadata verification failed.") from exc
        metadata = response.get("Metadata")
        sha256_hex = metadata.get("sha256") if isinstance(metadata, dict) else None
        content_type = response.get("ContentType")
        content_length = response.get("ContentLength")
        encryption_key_reference = _encryption_key_reference(response)
        if not (
            isinstance(content_type, str)
            and isinstance(content_length, int)
            and isinstance(sha256_hex, str)
            and isinstance(encryption_key_reference, str)
        ):
            raise RuntimeError("Artifact storage returned incomplete object metadata.")
        return ArtifactObjectMetadata(
            content_type=content_type,
            content_length=content_length,
            sha256_hex=sha256_hex,
            encryption_key_reference=encryption_key_reference,
        )


class FakeArtifactStore:
    """Deterministic test double that grants no real storage access or byte handling."""

    def __init__(self, *, settings: ArtifactStoreSettings | None = None) -> None:
        self._settings = settings or ArtifactStoreSettings(enabled=True)
        self.requests: list[ArtifactUploadRequest] = []
        self.objects: dict[UUID, ArtifactObjectMetadata] = {}

    def presign_upload(
        self, request: ArtifactUploadRequest, *, now: datetime
    ) -> PresignedArtifactUpload:
        _require_utc(now)
        if not self._settings.enabled:
            raise RuntimeError("Artifact storage is disabled.")
        self.requests.append(request)
        return PresignedArtifactUpload(
            upload_url=f"https://artifact-upload.invalid/{request.artifact_id}?capability=test-only",
            required_headers=(
                ("content-type", request.content_type.strip()),
                ("x-amz-meta-sha256", request.sha256_hex),
            ),
            expires_at=now.astimezone(UTC) + timedelta(seconds=self._settings.presign_ttl_seconds),
        )

    def head_uploaded_object(self, request: ArtifactUploadRequest) -> ArtifactObjectMetadata:
        if not self._settings.enabled:
            raise RuntimeError("Artifact storage is disabled.")
        try:
            return self.objects[request.artifact_id]
        except KeyError as exc:
            raise ArtifactObjectMissingError(request.artifact_id) from exc


def _object_key(request: ArtifactUploadRequest) -> str:
    """Build the adapter-private, opaque object location from immutable IDs only."""
    case_component = str(request.case_id) if request.case_id is not None else "unassigned"
    return (
        f"organizations/{request.organization_id}/cases/{case_component}/"
        f"artifacts/{request.artifact_id}"
    )


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")


def _is_not_found(error: Exception) -> bool:
    """Recognize the narrow cross-S3 404 shape without importing an SDK."""
    response = getattr(error, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return str(code) in {"404", "NoSuchKey", "NotFound"}


def _encryption_key_reference(response: dict[str, object]) -> str | None:
    """Return a non-secret encryption attestation or fail completion closed."""
    key_id = response.get("SSEKMSKeyId")
    if isinstance(key_id, str) and key_id.strip():
        return key_id
    algorithm = response.get("ServerSideEncryption")
    if isinstance(algorithm, str) and algorithm.strip():
        return f"s3:{algorithm}"
    return None
