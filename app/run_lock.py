from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.config import RUNTIME_DIR, ensure_directories

WRITER_LOCK_PATH = RUNTIME_DIR / "writer.lock"
WRITER_LOCK_STALE_AFTER = timedelta(hours=1)
logger = logging.getLogger(__name__)


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
            if _cleanup_stale_lock(lock_path):
                fd = os.open(str(lock_path), flags)
            else:
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


def _cleanup_stale_lock(lock_path: Path) -> bool:
    payload = _read_lock_payload(lock_path)
    if not _is_stale_lock_payload(payload):
        return False
    try:
        lock_path.unlink(missing_ok=True)
        logger.warning("Removed stale writer lock: %s", _format_lock_summary(payload))
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


def _is_stale_lock_payload(payload: dict[str, object]) -> bool:
    if not payload:
        return True
    pid = _safe_int(payload.get("pid"))
    started_at = _parse_lock_started_at(payload.get("started_at"))
    if pid is not None and not _pid_is_running(pid):
        return True
    if started_at is not None and datetime.now(timezone.utc) - started_at > WRITER_LOCK_STALE_AFTER:
        return True
    return False


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _parse_lock_started_at(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _format_lock_summary(payload: dict[str, object]) -> str:
    operation = str(payload.get("operation") or "unknown operation")
    pid = payload.get("pid")
    started_at = str(payload.get("started_at") or "").strip()
    parts = [f"operation={operation}"]
    if pid not in {None, ""}:
        parts.append(f"pid={pid}")
    if started_at:
        parts.append(f"started_at={started_at}")
    return ", ".join(parts)


def _read_lock_summary(lock_path: Path) -> str | None:
    payload = _read_lock_payload(lock_path)
    if not payload:
        return None
    return _format_lock_summary(payload)


def _read_lock_payload(lock_path: Path) -> dict[str, object]:
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
