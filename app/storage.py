from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import DB_PATH, ensure_directories
from app.extractors.date_extractor import infer_published_at
from app.models import AnalysisResult, RawDocument, SourceErrorRecord

logger = logging.getLogger(__name__)


def _serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.debug("Could not parse datetime value: %s", value)
        return None


def _get_connection(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    ensure_directories()
    resolved_path = Path(db_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(resolved_path))
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def _connect_db(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    connection = _get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def init_db(db_path: Path | str = DB_PATH) -> None:
    with _connect_db(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                level TEXT NOT NULL,
                region TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                published_at TEXT,
                collected_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                raw_text TEXT,
                local_file_path TEXT,
                document_type TEXT,
                is_relevant INTEGER,
                relevance_reason TEXT,
                topic TEXT,
                importance TEXT,
                action_level TEXT,
                page_type TEXT,
                summary TEXT,
                impact TEXT,
                support_status TEXT,
                is_active INTEGER,
                is_continuous INTEGER,
                application_status TEXT,
                npa_number TEXT,
                deadline_text TEXT,
                terms_text TEXT,
                business_signal TEXT,
                risk_notes TEXT,
                notified INTEGER DEFAULT 0,
                status TEXT,
                error TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_collected_at ON documents(collected_at)"
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(documents)").fetchall()
        }
        if "action_level" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN action_level TEXT")
        if "page_type" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN page_type TEXT")
        if "notified" not in columns:
            connection.execute(
                "ALTER TABLE documents ADD COLUMN notified INTEGER DEFAULT 0"
            )
            connection.execute(
                """
                UPDATE documents
                SET notified = 1
                WHERE action_level = 'requires_attention' AND status = 'analyzed'
                """
            )
        if "support_status" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN support_status TEXT")
        if "is_active" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN is_active INTEGER")
        if "is_continuous" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN is_continuous INTEGER")
        if "application_status" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN application_status TEXT")
        if "npa_number" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN npa_number TEXT")
        if "deadline_text" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN deadline_text TEXT")
        if "terms_text" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN terms_text TEXT")
        if "business_signal" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN business_signal TEXT")
        if "risk_notes" not in columns:
            connection.execute("ALTER TABLE documents ADD COLUMN risk_notes TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                error TEXT NOT NULL,
                collected_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_errors_collected_at ON source_errors(collected_at)"
        )
        connection.commit()
    logger.info("Database initialized at %s", db_path)


def save_document(document: RawDocument, db_path: Path | str = DB_PATH) -> int:
    payload = (
        document.source_name,
        document.source_url,
        document.level,
        document.region,
        document.title,
        document.url,
        _serialize_dt(document.published_at),
        _serialize_dt(document.collected_at),
        document.content_hash,
        document.raw_text,
        document.local_file_path,
        document.document_type,
        None if document.is_relevant is None else int(document.is_relevant),
        document.relevance_reason,
        document.topic,
        document.importance,
        document.action_level,
        document.page_type,
        document.summary,
        document.impact,
        document.support_status,
        None if document.is_active is None else int(document.is_active),
        None if document.is_continuous is None else int(document.is_continuous),
        document.application_status,
        document.npa_number,
        document.deadline_text,
        document.terms_text,
        document.business_signal,
        document.risk_notes,
        int(document.notified),
        document.status,
        document.error,
    )
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO documents (
                source_name, source_url, level, region, title, url, published_at,
                collected_at, content_hash, raw_text, local_file_path, document_type,
                is_relevant, relevance_reason, topic, importance, action_level, page_type, summary, impact,
                support_status, is_active, is_continuous, application_status, npa_number, deadline_text,
                terms_text, business_signal, risk_notes, notified, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        connection.commit()
        return int(cursor.lastrowid)


def document_exists_by_url(url: str, db_path: Path | str = DB_PATH) -> bool:
    with _connect_db(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM documents WHERE url = ? LIMIT 1", (url,)
        ).fetchone()
    return row is not None


def update_document_published_at_by_url(
    url: str,
    published_at: datetime,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE documents
            SET published_at = ?
            WHERE url = ?
              AND published_at IS NULL
            """,
            (_serialize_dt(published_at), url),
        )
        connection.commit()
        return int(cursor.rowcount or 0)


def document_exists_by_hash(content_hash: str, db_path: Path | str = DB_PATH) -> bool:
    with _connect_db(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM documents WHERE content_hash = ? LIMIT 1", (content_hash,)
        ).fetchone()
    return row is not None


def save_source_error(
    source_name: str,
    source_url: str,
    error: str,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO source_errors (source_name, source_url, error, collected_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                source_name,
                source_url,
                error,
                _serialize_dt(datetime.now(timezone.utc)),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def clear_source_errors(
    db_path: Path | str = DB_PATH,
    *,
    source_name: str | None = None,
) -> None:
    with _connect_db(db_path) as connection:
        if source_name is None:
            connection.execute("DELETE FROM source_errors")
        else:
            connection.execute(
                "DELETE FROM source_errors WHERE source_name = ?",
                (source_name,),
            )
        connection.commit()


def _row_to_document(row: sqlite3.Row) -> RawDocument:
    payload: dict[str, Any] = dict(row)
    payload["published_at"] = _parse_dt(payload.get("published_at"))
    payload["collected_at"] = _parse_dt(payload.get("collected_at"))
    if payload.get("is_relevant") is not None:
        payload["is_relevant"] = bool(payload["is_relevant"])
    if payload.get("is_active") is not None:
        payload["is_active"] = bool(payload["is_active"])
    if payload.get("is_continuous") is not None:
        payload["is_continuous"] = bool(payload["is_continuous"])
    if payload.get("notified") is not None:
        payload["notified"] = bool(payload["notified"])
    return RawDocument.model_validate(payload)


def list_unanalyzed_documents(
    db_path: Path | str = DB_PATH,
    limit: int | None = None,
    *,
    reanalyze: bool = False,
) -> list[RawDocument]:
    query = "SELECT * FROM documents"
    if not reanalyze:
        query += " WHERE is_relevant IS NULL"
    query += " ORDER BY collected_at ASC"
    parameters: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        parameters = (limit,)
    with _connect_db(db_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [_row_to_document(row) for row in rows]


def update_analysis(
    document_id: int, analysis: AnalysisResult, db_path: Path | str = DB_PATH
) -> None:
    with _connect_db(db_path) as connection:
        connection.execute(
            """
            UPDATE documents
            SET is_relevant = ?,
                relevance_reason = ?,
                topic = ?,
                importance = ?,
                action_level = ?,
                page_type = ?,
                summary = ?,
                impact = ?,
                support_status = ?,
                is_active = ?,
                is_continuous = ?,
                application_status = ?,
                npa_number = ?,
                deadline_text = ?,
                terms_text = ?,
                business_signal = ?,
                risk_notes = ?,
                status = ?
            WHERE id = ?
            """,
            (
                int(analysis.is_relevant),
                analysis.relevance_reason,
                analysis.topic,
                analysis.importance,
                analysis.action_level,
                analysis.page_type,
                analysis.summary,
                analysis.impact,
                analysis.support_status,
                None if analysis.is_active is None else int(analysis.is_active),
                None if analysis.is_continuous is None else int(analysis.is_continuous),
                analysis.application_status,
                analysis.npa_number,
                analysis.deadline_text,
                analysis.terms_text,
                analysis.business_signal,
                analysis.risk_notes,
                "analyzed",
                document_id,
            ),
        )
        connection.commit()


def list_recent_documents(
    db_path: Path | str = DB_PATH,
    days: int = 7,
    relevant_only: bool = False,
    limit: int | None = None,
    action_levels: list[str] | None = None,
) -> list[RawDocument]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = """
        SELECT * FROM documents
        WHERE COALESCE(published_at, collected_at) >= ?
    """
    parameters: list[Any] = [_serialize_dt(cutoff)]
    if relevant_only:
        query += " AND is_relevant = 1"
    if action_levels:
        placeholders = ", ".join("?" for _ in action_levels)
        query += f" AND action_level IN ({placeholders})"
        parameters.extend(action_levels)
    query += """
        ORDER BY
            CASE action_level
                WHEN 'requires_attention' THEN 4
                WHEN 'watchlist' THEN 3
                WHEN 'background' THEN 2
                WHEN 'irrelevant' THEN 1
                ELSE 0
            END DESC,
            CASE importance
                WHEN 'high' THEN 3
                WHEN 'medium' THEN 2
                WHEN 'low' THEN 1
                ELSE 0
            END DESC,
            COALESCE(published_at, collected_at) DESC,
            collected_at DESC
    """
    if limit is not None:
        query += " LIMIT ?"
        parameters.append(limit)
    with _connect_db(db_path) as connection:
        rows = connection.execute(query, tuple(parameters)).fetchall()
    return [_row_to_document(row) for row in rows]


def list_recent_source_errors(
    db_path: Path | str = DB_PATH,
    days: int = 7,
) -> list[SourceErrorRecord]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with _connect_db(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM source_errors
            WHERE collected_at >= ?
            ORDER BY collected_at DESC, source_name ASC
            """,
            (_serialize_dt(cutoff),),
        ).fetchall()
    return [
        SourceErrorRecord.model_validate(
            {
                **dict(row),
                "collected_at": _parse_dt(dict(row).get("collected_at")),
            }
        )
        for row in rows
    ]


def list_documents(
    db_path: Path | str = DB_PATH,
    *,
    days: int | None = None,
) -> list[RawDocument]:
    query = "SELECT * FROM documents"
    parameters: tuple[Any, ...] = ()
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query += " WHERE COALESCE(published_at, collected_at) >= ?"
        parameters = (_serialize_dt(cutoff),)
    query += " ORDER BY source_name ASC, collected_at DESC"
    with _connect_db(db_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [_row_to_document(row) for row in rows]


def backfill_missing_published_at(
    db_path: Path | str = DB_PATH,
    *,
    limit: int | None = None,
) -> int:
    query = """
        SELECT id, title, raw_text, source_name, url
        FROM documents
        WHERE published_at IS NULL
        ORDER BY collected_at DESC
    """
    parameters: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        parameters = (limit,)

    updated = 0
    with _connect_db(db_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
        for row in rows:
            inferred = infer_published_at(
                title=str(row["title"] or ""),
                raw_text=str(row["raw_text"] or ""),
                source_name=str(row["source_name"] or ""),
                url=str(row["url"] or ""),
            )
            if inferred is None:
                continue
            connection.execute(
                """
                UPDATE documents
                SET published_at = ?
                WHERE id = ?
                  AND published_at IS NULL
                """,
                (_serialize_dt(inferred), int(row["id"])),
            )
            updated += 1
        connection.commit()
    return updated


def list_unnotified_requires_attention(
    db_path: Path | str = DB_PATH,
) -> list[RawDocument]:
    with _connect_db(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM documents
            WHERE action_level = 'requires_attention'
              AND is_relevant = 1
              AND COALESCE(notified, 0) = 0
            ORDER BY collected_at ASC
            """
        ).fetchall()
    return [_row_to_document(row) for row in rows]


def mark_documents_notified(
    document_ids: list[int],
    db_path: Path | str = DB_PATH,
) -> int:
    valid_ids = [document_id for document_id in document_ids if document_id is not None]
    if not valid_ids:
        return 0
    placeholders = ", ".join("?" for _ in valid_ids)
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            f"UPDATE documents SET notified = 1 WHERE id IN ({placeholders})",
            tuple(valid_ids),
        )
        connection.commit()
        return int(cursor.rowcount or 0)


def count_documents_by_action_level(
    action_level: str,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM documents WHERE action_level = ?",
            (action_level,),
        ).fetchone()
    return int(row["total"]) if row else 0
