from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import RUNTIME_DIR, ensure_directories

WRITER_LOCK_PATH = RUNTIME_DIR / "writer.lock"


class WriterLockHeldError(RuntimeError):
    def __init__(self, *, lock_path: Path, holder_summary: str | None = None) -> None:
        self.lock_path = lock_path
        self.holder_summary = holder_summary
        message = "Another write operation is already running."
        if holder_summary:
            message = f"{message} {holder_summary}"
        super().__init__(message)


@contextmanager
def writer_lock(operation: str) -> Iterator[None]:
    ensure_directories()
    lock_path = WRITER_LOCK_PATH
    payload = {
        "operation": operation,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd: int | None = None
    try:
        try:
            fd = os.open(str(lock_path), flags)
        except FileExistsError as exc:
            raise WriterLockHeldError(
                lock_path=lock_path,
                holder_summary=_read_lock_summary(lock_path),
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            json.dump(payload, handle, ensure_ascii=False)
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if lock_path.exists():
                stored_payload = _read_lock_payload(lock_path)
                if int(stored_payload.get("pid") or -1) == os.getpid() and str(stored_payload.get("operation") or "") == operation:
                    lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def describe_writer_lock() -> str | None:
    return _read_lock_summary(WRITER_LOCK_PATH)


def _read_lock_summary(lock_path: Path) -> str | None:
    payload = _read_lock_payload(lock_path)
    if not payload:
        return None
    operation = str(payload.get("operation") or "unknown operation")
    pid = payload.get("pid")
    started_at = str(payload.get("started_at") or "").strip()
    parts = [f"operation={operation}"]
    if pid not in {None, ""}:
        parts.append(f"pid={pid}")
    if started_at:
        parts.append(f"started_at={started_at}")
    return ", ".join(parts)


def _read_lock_payload(lock_path: Path) -> dict[str, object]:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
