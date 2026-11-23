"""Temp-disk chunked upload sessions for extension assets.

The page uploads 4 MB chunks through the bridge; the host appends them to a
temp file and streams the completed file to the extension. This avoids a
single 50 MB ``postMessage`` and keeps the Octop → extension leg streaming.

V1 is single-instance: sessions live under ``tempfile.gettempdir()``. A
multi-instance deployment needs shared storage or sticky sessions.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_short_id

UPLOAD_CHUNK_BYTES = 4 * 1024 * 1024
UPLOAD_SESSION_TTL_SECONDS = 3600

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _root() -> Path:
    root = Path(tempfile.gettempdir()) / "octop-service-uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _session_dir(upload_id: str) -> Path:
    if not upload_id.startswith("upl_") or "/" in upload_id or "\\" in upload_id:
        raise OctopError(ErrorCode.APP_SOURCE_INVALID, "invalid upload session id")
    return _root() / upload_id


def _lock(upload_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(upload_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[upload_id] = lock
        return lock


@dataclass(frozen=True)
class UploadSession:
    upload_id: str
    app_id: str
    service_id: str
    name: str
    filename: str
    content_type: str
    purpose: str
    size: int
    actor_user_id: int
    next_index: int
    received: int
    created_at: int
    path: Path


def _meta_path(upload_id: str) -> Path:
    return _session_dir(upload_id) / "meta.json"


def _part_path(upload_id: str) -> Path:
    return _session_dir(upload_id) / "part.bin"


def _load(upload_id: str) -> UploadSession:
    meta_path = _meta_path(upload_id)
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise OctopError(ErrorCode.APP_NOT_FOUND, "upload session not found") from None
    if not isinstance(payload, dict):
        raise OctopError(ErrorCode.APP_NOT_FOUND, "upload session not found")
    return UploadSession(
        upload_id=upload_id,
        app_id=str(payload.get("app_id") or ""),
        service_id=str(payload.get("service_id") or ""),
        name=str(payload.get("name") or ""),
        filename=str(payload.get("filename") or "upload"),
        content_type=str(payload.get("content_type") or "application/octet-stream"),
        purpose=str(payload.get("purpose") or ""),
        size=int(payload.get("size") or 0),
        actor_user_id=int(payload.get("actor_user_id") or 0),
        next_index=int(payload.get("next_index") or 0),
        received=int(payload.get("received") or 0),
        created_at=int(payload.get("created_at") or 0),
        path=_part_path(upload_id),
    )


def _save(session: UploadSession) -> None:
    payload = {
        "app_id": session.app_id,
        "service_id": session.service_id,
        "name": session.name,
        "filename": session.filename,
        "content_type": session.content_type,
        "purpose": session.purpose,
        "size": session.size,
        "actor_user_id": session.actor_user_id,
        "next_index": session.next_index,
        "received": session.received,
        "created_at": session.created_at,
    }
    _meta_path(session.upload_id).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def start_session(
    *,
    app_id: str,
    service_id: str,
    name: str,
    filename: str,
    content_type: str,
    purpose: str,
    size: int,
    actor_user_id: int,
    max_bytes: int,
) -> UploadSession:
    if size <= 0 or size > max_bytes:
        raise OctopError(ErrorCode.APP_SOURCE_INVALID, "invalid upload size")
    cleanup_stale()
    upload_id = f"upl_{new_short_id(20)}"
    directory = _session_dir(upload_id)
    directory.mkdir(parents=True, exist_ok=False)
    session = UploadSession(
        upload_id=upload_id,
        app_id=app_id,
        service_id=service_id,
        name=name,
        filename=filename,
        content_type=content_type,
        purpose=purpose,
        size=size,
        actor_user_id=actor_user_id,
        next_index=0,
        received=0,
        created_at=int(time.time()),
        path=_part_path(upload_id),
    )
    session.path.touch()
    _save(session)
    return session


def get_session(upload_id: str) -> UploadSession:
    with _lock(upload_id):
        return _load(upload_id)


def append_chunk(
    upload_id: str,
    *,
    index: int,
    data: bytes,
    max_bytes: int,
) -> UploadSession:
    if len(data) > UPLOAD_CHUNK_BYTES:
        raise OctopError(ErrorCode.APP_SOURCE_INVALID, "upload chunk is too large")
    with _lock(upload_id):
        session = _load(upload_id)
        if index != session.next_index:
            raise OctopError(
                ErrorCode.APP_CONFLICT,
                f"expected chunk {session.next_index}, got {index}",
            )
        if session.received + len(data) > min(session.size, max_bytes):
            raise OctopError(ErrorCode.APP_SOURCE_INVALID, "upload exceeds the declared size")
        with session.path.open("ab") as handle:
            handle.write(data)
        session = UploadSession(
            upload_id=session.upload_id,
            app_id=session.app_id,
            service_id=session.service_id,
            name=session.name,
            filename=session.filename,
            content_type=session.content_type,
            purpose=session.purpose,
            size=session.size,
            actor_user_id=session.actor_user_id,
            next_index=session.next_index + 1,
            received=session.received + len(data),
            created_at=session.created_at,
            path=session.path,
        )
        _save(session)
        return session


def finish_session(upload_id: str) -> UploadSession:
    with _lock(upload_id):
        session = _load(upload_id)
        if session.received != session.size:
            raise OctopError(
                ErrorCode.APP_SOURCE_INVALID,
                f"upload incomplete: {session.received}/{session.size} bytes",
            )
        return session


def discard_session(upload_id: str) -> None:
    with _lock(upload_id):
        shutil.rmtree(_session_dir(upload_id), ignore_errors=True)
        _LOCKS.pop(upload_id, None)


def cleanup_stale(*, now: int | None = None) -> None:
    current = int(time.time()) if now is None else now
    cutoff = current - UPLOAD_SESSION_TTL_SECONDS
    root = _root()
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        meta = directory / "meta.json"
        try:
            created = int(json.loads(meta.read_text(encoding="utf-8")).get("created_at") or 0)
        except (OSError, ValueError, TypeError):
            created = 0
        if created <= cutoff:
            shutil.rmtree(directory, ignore_errors=True)


__all__ = [
    "UPLOAD_CHUNK_BYTES",
    "UPLOAD_SESSION_TTL_SECONDS",
    "UploadSession",
    "append_chunk",
    "cleanup_stale",
    "discard_session",
    "finish_session",
    "get_session",
    "start_session",
]
