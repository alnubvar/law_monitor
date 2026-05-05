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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                attempted_at TEXT NOT NULL,
                success_at TEXT,
                error_at TEXT,
                error_message TEXT,
                fetched_count INTEGER DEFAULT 0,
                saved_count INTEGER DEFAULT 0,
                existing_count INTEGER DEFAULT 0,
                duplicates_count INTEGER DEFAULT 0,
                item_errors_count INTEGER DEFAULT 0,
                links_found_count INTEGER DEFAULT 0,
                links_filtered_count INTEGER DEFAULT 0,
                pdf_links_count INTEGER DEFAULT 0,
                docx_links_count INTEGER DEFAULT 0,
                doc_links_count INTEGER DEFAULT 0,
                html_links_count INTEGER DEFAULT 0,
                xml_links_count INTEGER DEFAULT 0,
                unknown_links_count INTEGER DEFAULT 0,
                navigation_filtered_count INTEGER DEFAULT 0,
                archive_filtered_count INTEGER DEFAULT 0,
                external_filtered_count INTEGER DEFAULT 0,
                duplicate_filtered_count INTEGER DEFAULT 0,
                unsupported_filtered_count INTEGER DEFAULT 0,
                pdf_filtered_count INTEGER DEFAULT 0,
                docx_filtered_count INTEGER DEFAULT 0,
                filtered_samples TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_source_audit_source_attempted_at ON source_audit(source_name, attempted_at DESC)"
        )
        source_audit_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(source_audit)").fetchall()
        }
        if "links_found_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN links_found_count INTEGER DEFAULT 0")
        if "links_filtered_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN links_filtered_count INTEGER DEFAULT 0")
        if "pdf_links_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN pdf_links_count INTEGER DEFAULT 0")
        if "docx_links_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN docx_links_count INTEGER DEFAULT 0")
        if "doc_links_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN doc_links_count INTEGER DEFAULT 0")
        if "html_links_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN html_links_count INTEGER DEFAULT 0")
        if "xml_links_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN xml_links_count INTEGER DEFAULT 0")
        if "unknown_links_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN unknown_links_count INTEGER DEFAULT 0")
        if "navigation_filtered_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN navigation_filtered_count INTEGER DEFAULT 0")
        if "archive_filtered_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN archive_filtered_count INTEGER DEFAULT 0")
        if "external_filtered_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN external_filtered_count INTEGER DEFAULT 0")
        if "duplicate_filtered_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN duplicate_filtered_count INTEGER DEFAULT 0")
        if "unsupported_filtered_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN unsupported_filtered_count INTEGER DEFAULT 0")
        if "pdf_filtered_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN pdf_filtered_count INTEGER DEFAULT 0")
        if "docx_filtered_count" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN docx_filtered_count INTEGER DEFAULT 0")
        if "filtered_samples" not in source_audit_columns:
            connection.execute("ALTER TABLE source_audit ADD COLUMN filtered_samples TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS document_extraction_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                document_url TEXT NOT NULL,
                attachment_url TEXT,
                file_type TEXT,
                extracted_type TEXT,
                raw_text_length INTEGER DEFAULT 0,
                has_text INTEGER DEFAULT 0,
                scan_candidate INTEGER DEFAULT 0,
                needs_ocr INTEGER DEFAULT 0,
                page_count INTEGER,
                extraction_error TEXT,
                collected_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_extraction_audit_collected_at ON document_extraction_audit(collected_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_extraction_audit_source ON document_extraction_audit(source_name, collected_at DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_events (
                event_name TEXT PRIMARY KEY,
                updated_at TEXT NOT NULL,
                details TEXT
            )
            """
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
            SET title = ?,
                is_relevant = ?,
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
                analysis.normalized_title,
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


def save_source_audit_record(
    *,
    source_name: str,
    source_url: str,
    enabled: bool,
    attempted_at: datetime,
    success_at: datetime | None,
    error_at: datetime | None,
    error_message: str | None,
    fetched_count: int,
    saved_count: int,
    existing_count: int,
    duplicates_count: int,
    item_errors_count: int,
    links_found_count: int = 0,
    links_filtered_count: int = 0,
    pdf_links_count: int = 0,
    docx_links_count: int = 0,
    doc_links_count: int = 0,
    html_links_count: int = 0,
    xml_links_count: int = 0,
    unknown_links_count: int = 0,
    navigation_filtered_count: int = 0,
    archive_filtered_count: int = 0,
    external_filtered_count: int = 0,
    duplicate_filtered_count: int = 0,
    unsupported_filtered_count: int = 0,
    pdf_filtered_count: int = 0,
    docx_filtered_count: int = 0,
    filtered_samples: str | None = None,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO source_audit (
                source_name, source_url, enabled, attempted_at, success_at, error_at, error_message,
                fetched_count, saved_count, existing_count, duplicates_count, item_errors_count,
                links_found_count, links_filtered_count, pdf_links_count, docx_links_count, doc_links_count,
                html_links_count, xml_links_count, unknown_links_count,
                navigation_filtered_count, archive_filtered_count, external_filtered_count,
                duplicate_filtered_count, unsupported_filtered_count, pdf_filtered_count, docx_filtered_count,
                filtered_samples
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_name,
                source_url,
                int(enabled),
                _serialize_dt(attempted_at),
                _serialize_dt(success_at),
                _serialize_dt(error_at),
                error_message,
                fetched_count,
                saved_count,
                existing_count,
                duplicates_count,
                item_errors_count,
                links_found_count,
                links_filtered_count,
                pdf_links_count,
                docx_links_count,
                doc_links_count,
                html_links_count,
                xml_links_count,
                unknown_links_count,
                navigation_filtered_count,
                archive_filtered_count,
                external_filtered_count,
                duplicate_filtered_count,
                unsupported_filtered_count,
                pdf_filtered_count,
                docx_filtered_count,
                filtered_samples,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def list_latest_source_audit(
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    with _connect_db(db_path) as connection:
        rows = connection.execute(
            """
            SELECT sa.*
            FROM source_audit sa
            JOIN (
                SELECT source_name, MAX(attempted_at) AS max_attempted_at
                FROM source_audit
                GROUP BY source_name
            ) latest
              ON latest.source_name = sa.source_name
             AND latest.max_attempted_at = sa.attempted_at
            ORDER BY sa.source_name ASC
            """
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["attempted_at"] = _parse_dt(payload.get("attempted_at"))
        payload["success_at"] = _parse_dt(payload.get("success_at"))
        payload["error_at"] = _parse_dt(payload.get("error_at"))
        payload["enabled"] = bool(payload.get("enabled"))
        results.append(payload)
    return results


def save_document_extraction_audit(
    *,
    source_name: str,
    source_url: str,
    document_url: str,
    attachment_url: str | None,
    file_type: str,
    extracted_type: str,
    raw_text_length: int,
    has_text: bool,
    scan_candidate: bool,
    needs_ocr: bool,
    page_count: int | None,
    extraction_error: str | None,
    collected_at: datetime | None = None,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO document_extraction_audit (
                source_name, source_url, document_url, attachment_url, file_type, extracted_type,
                raw_text_length, has_text, scan_candidate, needs_ocr, page_count, extraction_error, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_name,
                source_url,
                document_url,
                attachment_url,
                file_type,
                extracted_type,
                raw_text_length,
                int(has_text),
                int(scan_candidate),
                int(needs_ocr),
                page_count,
                extraction_error,
                _serialize_dt(collected_at or datetime.now(timezone.utc)),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def list_recent_document_extraction_audit(
    *,
    days: int | None = 7,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days or 0) if days is not None else None
    with _connect_db(db_path) as connection:
        if cutoff is None:
            rows = connection.execute(
                """
                SELECT * FROM document_extraction_audit
                ORDER BY collected_at DESC, source_name ASC
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM document_extraction_audit
                WHERE collected_at >= ?
                ORDER BY collected_at DESC, source_name ASC
                """,
                (_serialize_dt(cutoff),),
            ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["has_text"] = bool(payload.get("has_text"))
        payload["scan_candidate"] = bool(payload.get("scan_candidate"))
        payload["needs_ocr"] = bool(payload.get("needs_ocr"))
        payload["collected_at"] = _parse_dt(payload.get("collected_at"))
        result.append(payload)
    return result


def mark_runtime_event(
    event_name: str,
    *,
    details: str | None = None,
    occurred_at: datetime | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    timestamp = occurred_at or datetime.now(timezone.utc)
    with _connect_db(db_path) as connection:
        connection.execute(
            """
            INSERT INTO runtime_events(event_name, updated_at, details)
            VALUES (?, ?, ?)
            ON CONFLICT(event_name) DO UPDATE SET
                updated_at=excluded.updated_at,
                details=excluded.details
            """,
            (event_name, _serialize_dt(timestamp), details),
        )
        connection.commit()


def get_runtime_event(
    event_name: str,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any] | None:
    with _connect_db(db_path) as connection:
        row = connection.execute(
            "SELECT event_name, updated_at, details FROM runtime_events WHERE event_name = ?",
            (event_name,),
        ).fetchone()
    if row is None:
        return None
    payload = dict(row)
    payload["updated_at"] = _parse_dt(payload.get("updated_at"))
    return payload
