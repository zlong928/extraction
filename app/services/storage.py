from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator
from uuid import uuid4

from app.config import (
    S3_BUCKET,
    S3_ENDPOINT_URL,
    S3_PREFIX,
    S3_REGION,
    STORAGE_BACKEND,
    STORAGE_LOCAL_ROOT,
)


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class StoredObjectInfo:
    key: str
    uri: str
    sha256: str
    size_bytes: int
    media_type: str
    etag: str | None = None


def normalize_object_key(key: str) -> str:
    normalized = PurePosixPath(str(key).replace("\\", "/").lstrip("/"))
    if not normalized.parts or any(part in {"", ".", ".."} for part in normalized.parts):
        raise ValueError(f"Invalid storage object key: {key!r}")
    return normalized.as_posix()


class StorageAdapter(ABC):
    """Byte-oriented storage boundary used by application services."""

    @abstractmethod
    def put_bytes(self, key: str, data: bytes, *, media_type: str) -> StoredObjectInfo:
        raise NotImplementedError

    def put_file(self, key: str, source: str | Path, *, media_type: str | None = None) -> StoredObjectInfo:
        path = Path(source)
        return self.put_bytes(
            key,
            path.read_bytes(),
            media_type=media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )

    @abstractmethod
    def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def exists(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def uri_for(self, key: str) -> str:
        raise NotImplementedError

    @contextmanager
    def materialize(self, key: str, *, suffix: str = "") -> Iterator[Path]:
        with tempfile.TemporaryDirectory(prefix="extraction-object-") as temp_dir:
            destination = Path(temp_dir) / f"object{suffix}"
            destination.write_bytes(self.get_bytes(key))
            yield destination


class LocalStorageAdapter(StorageAdapter):
    def __init__(self, root: str | Path = STORAGE_LOCAL_ROOT) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / normalize_object_key(key)).resolve()
        path.relative_to(self.root)
        return path

    def put_bytes(self, key: str, data: bytes, *, media_type: str) -> StoredObjectInfo:
        object_key = normalize_object_key(key)
        destination = self._path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(data).hexdigest()
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        temporary.write_bytes(data)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            existing_digest, existing_size = file_digest(destination)
            if existing_digest != digest or existing_size != len(data):
                raise ValueError(f"Object key {object_key!r} already exists with different content")
        finally:
            temporary.unlink(missing_ok=True)
        return StoredObjectInfo(
            key=object_key,
            uri=self.uri_for(object_key),
            sha256=digest,
            size_bytes=len(data),
            media_type=media_type,
            etag=digest,
        )

    def put_file(self, key: str, source: str | Path, *, media_type: str | None = None) -> StoredObjectInfo:
        object_key = normalize_object_key(key)
        source_path = Path(source)
        destination = self._path(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest, size = file_digest(source_path)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        shutil.copyfile(source_path, temporary)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            existing_digest, existing_size = file_digest(destination)
            if existing_digest != digest or existing_size != size:
                raise ValueError(f"Object key {object_key!r} already exists with different content")
        finally:
            temporary.unlink(missing_ok=True)
        return StoredObjectInfo(
            key=object_key,
            uri=self.uri_for(object_key),
            sha256=digest,
            size_bytes=size,
            media_type=media_type or mimetypes.guess_type(source_path.name)[0] or "application/octet-stream",
            etag=digest,
        )

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def uri_for(self, key: str) -> str:
        return self._path(key).as_uri()

    @contextmanager
    def materialize(self, key: str, *, suffix: str = "") -> Iterator[Path]:
        yield self._path(key)


class S3StorageAdapter(StorageAdapter):
    def __init__(
        self,
        *,
        bucket: str = S3_BUCKET,
        prefix: str = S3_PREFIX,
        endpoint_url: str | None = S3_ENDPOINT_URL,
        region: str = S3_REGION,
        client=None,
    ) -> None:
        if not bucket:
            raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def _remote_key(self, key: str) -> str:
        object_key = normalize_object_key(key)
        return f"{self.prefix}/{object_key}" if self.prefix else object_key

    def put_bytes(self, key: str, data: bytes, *, media_type: str) -> StoredObjectInfo:
        object_key = normalize_object_key(key)
        digest = hashlib.sha256(data).hexdigest()
        try:
            response = self.client.put_object(
                Bucket=self.bucket,
                Key=self._remote_key(object_key),
                Body=data,
                ContentType=media_type,
                Metadata={"sha256": digest},
                IfNoneMatch="*",
            )
        except Exception as exc:
            if not _is_precondition_failure(exc):
                raise
            return self._existing_info(object_key, digest=digest, size=len(data), media_type=media_type)
        return StoredObjectInfo(
            key=object_key,
            uri=self.uri_for(object_key),
            sha256=digest,
            size_bytes=len(data),
            media_type=media_type,
            etag=str(response.get("ETag") or "").strip('"') or None,
        )

    def get_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._remote_key(key))
        body: BinaryIO = response["Body"]
        return body.read()

    def put_file(self, key: str, source: str | Path, *, media_type: str | None = None) -> StoredObjectInfo:
        object_key = normalize_object_key(key)
        source_path = Path(source)
        digest, size = file_digest(source_path)
        content_type = media_type or mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
        try:
            with source_path.open("rb") as source_handle:
                response = self.client.put_object(
                    Bucket=self.bucket,
                    Key=self._remote_key(object_key),
                    Body=source_handle,
                    ContentType=content_type,
                    Metadata={"sha256": digest},
                    IfNoneMatch="*",
                )
        except Exception as exc:
            if not _is_precondition_failure(exc):
                raise
            return self._existing_info(object_key, digest=digest, size=size, media_type=content_type)
        return StoredObjectInfo(
            key=object_key,
            uri=self.uri_for(object_key),
            sha256=digest,
            size_bytes=size,
            media_type=content_type,
            etag=str(response.get("ETag") or "").strip('"') or None,
        )

    def _existing_info(self, object_key: str, *, digest: str, size: int, media_type: str) -> StoredObjectInfo:
        head = self.client.head_object(Bucket=self.bucket, Key=self._remote_key(object_key))
        existing_digest = str((head.get("Metadata") or {}).get("sha256") or "")
        existing_size = int(head.get("ContentLength") or 0)
        if existing_digest != digest or existing_size != size:
            raise ValueError(f"Object key {object_key!r} already exists with different content")
        return StoredObjectInfo(
            key=object_key,
            uri=self.uri_for(object_key),
            sha256=digest,
            size_bytes=size,
            media_type=str(head.get("ContentType") or media_type),
            etag=str(head.get("ETag") or "").strip('"') or None,
        )

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._remote_key(key))
            return True
        except Exception as exc:
            response = getattr(exc, "response", {})
            code = str(response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._remote_key(key))

    def uri_for(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._remote_key(key)}"


def get_storage_adapter() -> StorageAdapter:
    if STORAGE_BACKEND == "local":
        return LocalStorageAdapter()
    if STORAGE_BACKEND == "s3":
        return S3StorageAdapter()
    raise ValueError(f"Unsupported STORAGE_BACKEND={STORAGE_BACKEND!r}; expected 'local' or 's3'.")


def file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _is_precondition_failure(exc: Exception) -> bool:
    response = getattr(exc, "response", {})
    error = response.get("Error", {})
    return str(error.get("Code", "")) in {"412", "PreconditionFailed"} or response.get(
        "ResponseMetadata", {}
    ).get("HTTPStatusCode") == 412


class StorageService:
    """Compatibility facade while business flows migrate from paths to object keys."""

    def __init__(self, root: Path | None = None, adapter: StorageAdapter | None = None) -> None:
        self.adapter = adapter or (LocalStorageAdapter(root) if root is not None else get_storage_adapter())
        self.root = self.adapter.root if isinstance(self.adapter, LocalStorageAdapter) else None

    def safe_filename(self, filename: str) -> str:
        name = Path(filename).name.strip() or "upload.pdf"
        return SAFE_NAME_RE.sub("_", name)[:180]

    def put_bytes(self, key: str, data: bytes, *, media_type: str) -> StoredObjectInfo:
        return self.adapter.put_bytes(key, data, media_type=media_type)

    def put_file(self, key: str, source: str | Path, *, media_type: str | None = None) -> StoredObjectInfo:
        return self.adapter.put_file(key, source, media_type=media_type)

    def get_bytes(self, key: str) -> bytes:
        legacy = self._legacy_absolute_path(key)
        if legacy is not None:
            return legacy.read_bytes()
        return self.adapter.get_bytes(key)

    def exists(self, key: str) -> bool:
        if self._legacy_absolute_path(key) is not None:
            return True
        return self.adapter.exists(key)

    @contextmanager
    def materialize(self, key: str, *, suffix: str = ""):
        legacy = self._legacy_absolute_path(key)
        if legacy is not None:
            yield legacy
            return
        with self.adapter.materialize(key, suffix=suffix) as path:
            yield path

    def _legacy_absolute_path(self, key: str) -> Path | None:
        if not isinstance(self.adapter, LocalStorageAdapter):
            return None
        path = Path(key)
        return path.resolve() if path.is_absolute() and path.is_file() else None

    def relative_path(self, path: Path) -> str:
        if self.root is None:
            raise RuntimeError("relative_path is only available for local compatibility storage")
        return path.resolve().relative_to(self.root).as_posix()

    def absolute_path(self, relative_path: str) -> Path:
        if self.root is None:
            raise RuntimeError("absolute_path is only available for local compatibility storage; use materialize()")
        candidate = (self.root / normalize_object_key(relative_path)).resolve()
        candidate.relative_to(self.root)
        return candidate

    def paper_dir(self, paper_id: int) -> Path:
        if self.root is None:
            raise RuntimeError("paper_dir is not available for object storage; use put_bytes/put_file")
        path = self.root / "papers" / str(paper_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def asset_dir(self, paper_id: int) -> Path:
        path = self.paper_dir(paper_id) / "assets"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def result_dir(self, paper_id: int, asset_id: int) -> Path:
        path = self.paper_dir(paper_id) / "results" / str(asset_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def remove_paper_tree(self, paper_id: int) -> None:
        if self.root is not None:
            shutil.rmtree(self.root / "papers" / str(paper_id), ignore_errors=True)

    def remove_path(self, path: str) -> None:
        self.adapter.delete(path)
