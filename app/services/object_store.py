from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import StorageObject
from app.services.storage import (
    StorageAdapter,
    StoredObjectInfo,
    file_digest,
    get_storage_adapter,
    normalize_object_key,
)


class ObjectStore:
    """Persists object bytes through an adapter and their facts in PostgreSQL."""

    def __init__(self, db: Session, adapter: StorageAdapter | None = None) -> None:
        self.db = db
        self.adapter = adapter or get_storage_adapter()

    def put_bytes(
        self,
        *,
        key: str,
        data: bytes,
        media_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> StorageObject:
        key = normalize_object_key(key)
        existing = self.db.query(StorageObject).filter(StorageObject.object_key == key).one_or_none()
        digest = hashlib.sha256(data).hexdigest()
        if existing is not None:
            if existing.sha256 != digest or existing.size_bytes != len(data):
                raise ValueError(f"Object key {key!r} already exists with different content")
            return existing
        info = self.adapter.put_bytes(key, data, media_type=media_type)
        return self.register(info, metadata=metadata)

    def put_file(
        self,
        *,
        key: str,
        source: str | Path,
        media_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StorageObject:
        path = Path(source)
        key = normalize_object_key(key)
        digest, size = file_digest(path)
        existing = self.db.query(StorageObject).filter(StorageObject.object_key == key).one_or_none()
        if existing is not None:
            if existing.sha256 != digest or existing.size_bytes != size:
                raise ValueError(f"Object key {key!r} already exists with different content")
            return existing
        info = self.adapter.put_file(key, path, media_type=media_type)
        return self.register(info, metadata=metadata)

    def put_json(self, *, key: str, payload: Any, metadata: dict[str, Any] | None = None) -> StorageObject:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return self.put_bytes(key=key, data=data, media_type="application/json", metadata=metadata)

    def stage_bytes(
        self,
        *,
        run_id: str,
        claim_generation: int,
        role: str,
        filename: str,
        data: bytes,
        media_type: str,
    ) -> StoredObjectInfo:
        """Write immutable worker output without reading or mutating database state."""
        digest = hashlib.sha256(data).hexdigest()
        key = self._staging_key(
            run_id=run_id,
            claim_generation=claim_generation,
            role=role,
            digest=digest,
            filename=filename,
        )
        return self.adapter.put_bytes(key, data, media_type=media_type)

    def stage_file(
        self,
        *,
        run_id: str,
        claim_generation: int,
        role: str,
        filename: str,
        source: str | Path,
        media_type: str | None = None,
    ) -> StoredObjectInfo:
        """Write an immutable file under a lease-generation-specific staging key."""
        path = Path(source)
        digest, _ = file_digest(path)
        key = self._staging_key(
            run_id=run_id,
            claim_generation=claim_generation,
            role=role,
            digest=digest,
            filename=filename,
        )
        return self.adapter.put_file(key, path, media_type=media_type)

    def stage_json(
        self,
        *,
        run_id: str,
        claim_generation: int,
        role: str,
        filename: str,
        payload: Any,
    ) -> StoredObjectInfo:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return self.stage_bytes(
            run_id=run_id,
            claim_generation=claim_generation,
            role=role,
            filename=filename,
            data=data,
            media_type="application/json",
        )

    def register(self, info: StoredObjectInfo, *, metadata: dict[str, Any] | None = None) -> StorageObject:
        """Register already-written bytes in the current database transaction."""
        existing = self.db.query(StorageObject).filter(StorageObject.object_key == info.key).one_or_none()
        if existing is not None:
            if existing.sha256 != info.sha256 or existing.size_bytes != info.size_bytes:
                raise ValueError(f"Object key {info.key!r} already exists with different content")
            return existing
        record = StorageObject(
            object_key=info.key,
            uri=info.uri,
            sha256=info.sha256,
            size_bytes=info.size_bytes,
            media_type=info.media_type,
            etag=info.etag,
            metadata_json=metadata or {},
        )
        try:
            with self.db.begin_nested():
                self.db.add(record)
                self.db.flush()
        except IntegrityError:
            existing = self.db.query(StorageObject).filter(StorageObject.object_key == info.key).one_or_none()
            if existing is None:
                raise
            if existing.sha256 != info.sha256 or existing.size_bytes != info.size_bytes:
                raise ValueError(f"Object key {info.key!r} already exists with different content")
            return existing
        return record

    @staticmethod
    def _staging_key(
        *,
        run_id: str,
        claim_generation: int,
        role: str,
        digest: str,
        filename: str,
    ) -> str:
        safe_role = normalize_object_key(role).replace("/", "-")
        safe_filename = normalize_object_key(filename)
        return normalize_object_key(
            f"runs/{run_id}/staging/{claim_generation}/{safe_role}/{digest}/{safe_filename}"
        )
