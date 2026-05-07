from __future__ import annotations

import logging
import sqlite3
from hashlib import sha256
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import DB_PATH, ensure_directories
from app.extractors.date_extractor import infer_published_at
from app.llm.enrichment import EnrichmentResult
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
                ocr_status TEXT DEFAULT 'not_needed',
                ocr_text_length INTEGER DEFAULT 0,
                ocr_error TEXT,
                ocr_pages_processed INTEGER DEFAULT 0,
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
        extraction_audit_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(document_extraction_audit)").fetchall()
        }
        if "ocr_status" not in extraction_audit_columns:
            connection.execute("ALTER TABLE document_extraction_audit ADD COLUMN ocr_status TEXT DEFAULT 'not_needed'")
        if "ocr_text_length" not in extraction_audit_columns:
            connection.execute("ALTER TABLE document_extraction_audit ADD COLUMN ocr_text_length INTEGER DEFAULT 0")
        if "ocr_error" not in extraction_audit_columns:
            connection.execute("ALTER TABLE document_extraction_audit ADD COLUMN ocr_error TEXT")
        if "ocr_pages_processed" not in extraction_audit_columns:
            connection.execute("ALTER TABLE document_extraction_audit ADD COLUMN ocr_pages_processed INTEGER DEFAULT 0")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ocr_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_url TEXT NOT NULL UNIQUE,
                source_name TEXT NOT NULL,
                title TEXT,
                priority TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'pending',
                reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                notes TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_ocr_queue_status_priority ON ocr_queue(status, priority, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_ocr_queue_updated_at ON ocr_queue(updated_at DESC)"
        )
        ocr_queue_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(ocr_queue)").fetchall()
        }
        if "title" not in ocr_queue_columns:
            connection.execute("ALTER TABLE ocr_queue ADD COLUMN title TEXT")
        if "priority" not in ocr_queue_columns:
            connection.execute("ALTER TABLE ocr_queue ADD COLUMN priority TEXT NOT NULL DEFAULT 'medium'")
        if "status" not in ocr_queue_columns:
            connection.execute("ALTER TABLE ocr_queue ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        if "reason" not in ocr_queue_columns:
            connection.execute("ALTER TABLE ocr_queue ADD COLUMN reason TEXT")
        if "created_at" not in ocr_queue_columns:
            connection.execute("ALTER TABLE ocr_queue ADD COLUMN created_at TEXT")
            connection.execute(
                """
                UPDATE ocr_queue
                SET created_at = COALESCE(updated_at, ?)
                WHERE created_at IS NULL OR created_at = ''
                """,
                (_serialize_dt(datetime.now(timezone.utc)),),
            )
        if "updated_at" not in ocr_queue_columns:
            connection.execute("ALTER TABLE ocr_queue ADD COLUMN updated_at TEXT")
            connection.execute(
                """
                UPDATE ocr_queue
                SET updated_at = COALESCE(created_at, ?)
                WHERE updated_at IS NULL OR updated_at = ''
                """,
                (_serialize_dt(datetime.now(timezone.utc)),),
            )
        if "notes" not in ocr_queue_columns:
            connection.execute("ALTER TABLE ocr_queue ADD COLUMN notes TEXT")
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ocr_queue_document_url ON ocr_queue(document_url)"
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                chat_id TEXT PRIMARY KEY,
                default_period_days INTEGER NOT NULL DEFAULT 7,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tracking_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                document_url TEXT NOT NULL,
                document_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_checked_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracking_items_chat_active ON tracking_items(chat_id, active)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracking_items_url_active ON tracking_items(document_url, active)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tracking_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking_item_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                status_hash TEXT NOT NULL,
                support_status TEXT,
                application_status TEXT,
                deadline_text TEXT,
                terms_text TEXT,
                is_active INTEGER,
                title TEXT,
                summary TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracking_snapshots_item_checked ON tracking_snapshots(tracking_item_id, checked_at DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tracking_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tracking_item_id INTEGER NOT NULL,
                detected_at TEXT NOT NULL,
                change_summary TEXT NOT NULL,
                old_hash TEXT,
                new_hash TEXT,
                notified_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tracking_events_item_detected ON tracking_events(tracking_item_id, detected_at DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS document_enrichments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER,
                document_url TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                executive_summary TEXT,
                business_impact TEXT,
                recommended_action TEXT,
                deadline_hint TEXT,
                confidence REAL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_document_enrichments_key ON document_enrichments(document_url, provider, model)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_document_enrichments_updated_at ON document_enrichments(updated_at DESC)"
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


def save_document_enrichment(
    *,
    document_id: int | None,
    document_url: str,
    provider: str,
    model: str,
    enrichment: EnrichmentResult,
    db_path: Path | str = DB_PATH,
) -> int:
    now = datetime.now(timezone.utc)
    normalized_provider = (provider or "").strip() or "unknown"
    normalized_model = (model or "").strip()
    with _connect_db(db_path) as connection:
        connection.execute(
            """
            INSERT INTO document_enrichments (
                document_id,
                document_url,
                provider,
                model,
                executive_summary,
                business_impact,
                recommended_action,
                deadline_hint,
                confidence,
                error,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_url, provider, model) DO UPDATE SET
                document_id = excluded.document_id,
                executive_summary = excluded.executive_summary,
                business_impact = excluded.business_impact,
                recommended_action = excluded.recommended_action,
                deadline_hint = excluded.deadline_hint,
                confidence = excluded.confidence,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (
                document_id,
                document_url,
                normalized_provider,
                normalized_model,
                enrichment.executive_summary,
                enrichment.business_impact,
                enrichment.recommended_action,
                enrichment.deadline_hint,
                enrichment.confidence,
                enrichment.error,
                _serialize_dt(now),
                _serialize_dt(now),
            ),
        )
        row = connection.execute(
            """
            SELECT id
            FROM document_enrichments
            WHERE document_url = ? AND provider = ? AND model = ?
            LIMIT 1
            """,
            (document_url, normalized_provider, normalized_model),
        ).fetchone()
        connection.commit()
    return int(row["id"]) if row is not None else 0


def get_document_enrichment(
    document_url: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any] | None:
    clauses = ["document_url = ?"]
    parameters: list[Any] = [document_url]
    if provider is not None:
        clauses.append("provider = ?")
        parameters.append(provider)
    if model is not None:
        clauses.append("model = ?")
        parameters.append(model)
    query = (
        "SELECT * FROM document_enrichments "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY updated_at DESC LIMIT 1"
    )
    with _connect_db(db_path) as connection:
        row = connection.execute(query, tuple(parameters)).fetchone()
    if row is None:
        return None
    return _row_to_document_enrichment(row)


def list_document_enrichments(
    document_urls: list[str],
    *,
    provider: str | None = None,
    model: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, dict[str, Any]]:
    normalized_urls = [url.strip() for url in document_urls if (url or "").strip()]
    if not normalized_urls:
        return {}
    placeholders = ", ".join("?" for _ in normalized_urls)
    clauses = [f"document_url IN ({placeholders})"]
    parameters: list[Any] = list(normalized_urls)
    if provider is not None:
        clauses.append("provider = ?")
        parameters.append(provider)
    if model is not None:
        clauses.append("model = ?")
        parameters.append(model)
    query = (
        "SELECT * FROM document_enrichments "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY updated_at DESC, id DESC"
    )
    with _connect_db(db_path) as connection:
        rows = connection.execute(query, tuple(parameters)).fetchall()
    by_url: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _row_to_document_enrichment(row)
        document_url = str(payload.get("document_url") or "").strip()
        if document_url and document_url not in by_url:
            by_url[document_url] = payload
    return by_url


def count_document_enrichments(db_path: Path | str = DB_PATH) -> int:
    with _connect_db(db_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM document_enrichments"
        ).fetchone()
    if row is None:
        return 0
    return int(row["count"] or 0)


def _row_to_document_enrichment(row: sqlite3.Row) -> dict[str, Any]:
    payload: dict[str, Any] = dict(row)
    payload["created_at"] = _parse_dt(payload.get("created_at"))
    payload["updated_at"] = _parse_dt(payload.get("updated_at"))
    if payload.get("confidence") is not None:
        payload["confidence"] = float(payload["confidence"])
    return payload


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
    ocr_status: str = "not_needed",
    ocr_text_length: int = 0,
    ocr_error: str | None = None,
    ocr_pages_processed: int = 0,
    page_count: int | None = None,
    extraction_error: str | None = None,
    collected_at: datetime | None = None,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO document_extraction_audit (
                source_name, source_url, document_url, attachment_url, file_type, extracted_type,
                raw_text_length, has_text, scan_candidate, needs_ocr, ocr_status, ocr_text_length, ocr_error,
                ocr_pages_processed, page_count, extraction_error, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ocr_status,
                int(ocr_text_length),
                ocr_error,
                int(ocr_pages_processed),
                page_count,
                extraction_error,
                _serialize_dt(collected_at or datetime.now(timezone.utc)),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def determine_ocr_priority(
    *,
    source_name: str,
    action_level: str | None,
) -> str:
    normalized_level = (action_level or "").strip().lower()
    if normalized_level in {"requires_attention", "watchlist"}:
        return "high"
    if (source_name or "").strip() == "Нормативные акты Краснодарского края":
        return "high"
    if normalized_level == "irrelevant":
        return "low"
    return "medium"


def upsert_ocr_queue_item(
    *,
    document_url: str,
    source_name: str,
    title: str | None,
    priority: str,
    reason: str,
    db_path: Path | str = DB_PATH,
) -> int:
    normalized_url = (document_url or "").strip()
    if not normalized_url:
        raise ValueError("document_url must not be empty")
    normalized_priority = (priority or "").strip().lower()
    if normalized_priority not in {"high", "medium", "low"}:
        raise ValueError(f"Unsupported OCR priority: {priority}")
    timestamp = _serialize_dt(datetime.now(timezone.utc))
    assert timestamp is not None
    with _connect_db(db_path) as connection:
        connection.execute(
            """
            INSERT INTO ocr_queue(
                document_url, source_name, title, priority, status, reason, created_at, updated_at, notes
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, NULL)
            ON CONFLICT(document_url) DO UPDATE SET
                source_name = excluded.source_name,
                title = excluded.title,
                priority = excluded.priority,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            """,
            (
                normalized_url,
                source_name,
                title,
                normalized_priority,
                reason,
                timestamp,
                timestamp,
            ),
        )
        row = connection.execute(
            "SELECT id FROM ocr_queue WHERE document_url = ? LIMIT 1",
            (normalized_url,),
        ).fetchone()
        connection.commit()
    return int(row["id"]) if row is not None else 0


def list_ocr_queue(
    *,
    status: str | None = None,
    statuses: list[str] | None = None,
    priority: str | None = None,
    priorities: list[str] | None = None,
    source_name: str | None = None,
    limit: int = 20,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    status_filters = statuses[:] if statuses else []
    if status:
        status_filters = [status]
    normalized_statuses = [
        value.strip().lower()
        for value in status_filters
        if value and value.strip().lower() in {"pending", "in_review", "done", "skipped"}
    ]
    priority_filters = priorities[:] if priorities else []
    if priority:
        priority_filters = [priority]
    normalized_priorities = [
        value.strip().lower()
        for value in priority_filters
        if value and value.strip().lower() in {"high", "medium", "low"}
    ]
    query = """
        SELECT *
        FROM ocr_queue
    """
    params: list[Any] = []
    where_clauses: list[str] = []
    if normalized_statuses:
        placeholders = ", ".join("?" for _ in normalized_statuses)
        where_clauses.append(f"status IN ({placeholders})")
        params.extend(normalized_statuses)
    if normalized_priorities:
        placeholders = ", ".join("?" for _ in normalized_priorities)
        where_clauses.append(f"priority IN ({placeholders})")
        params.extend(normalized_priorities)
    if source_name and source_name.strip():
        where_clauses.append("source_name = ?")
        params.append(source_name.strip())
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)
    query += """
        ORDER BY
            CASE priority
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                WHEN 'low' THEN 3
                ELSE 4
            END ASC,
            CASE status
                WHEN 'pending' THEN 1
                WHEN 'in_review' THEN 2
                WHEN 'done' THEN 3
                WHEN 'skipped' THEN 4
                ELSE 5
            END ASC,
            updated_at DESC,
            id DESC
        LIMIT ?
    """
    params.append(safe_limit)
    with _connect_db(db_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["created_at"] = _parse_dt(payload.get("created_at"))
        payload["updated_at"] = _parse_dt(payload.get("updated_at"))
        result.append(payload)
    return result


def list_pending_ocr_queue(
    *,
    source_name: str | None = None,
    limit: int = 20,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    return list_ocr_queue(
        statuses=["pending"],
        source_name=source_name,
        limit=limit,
        db_path=db_path,
    )


def ocr_queue_item_exists(
    *,
    document_url: str,
    db_path: Path | str = DB_PATH,
) -> bool:
    normalized_url = (document_url or "").strip()
    if not normalized_url:
        return False
    with _connect_db(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM ocr_queue WHERE document_url = ? LIMIT 1",
            (normalized_url,),
        ).fetchone()
    return row is not None


def update_ocr_queue_status(
    *,
    document_url: str,
    status: str,
    notes: str | None = None,
    db_path: Path | str = DB_PATH,
) -> bool:
    normalized_url = (document_url or "").strip()
    normalized_status = (status or "").strip().lower()
    if not normalized_url:
        return False
    if normalized_status not in {"pending", "in_review", "done", "skipped"}:
        raise ValueError(f"Unsupported OCR queue status: {status}")
    timestamp = _serialize_dt(datetime.now(timezone.utc))
    assert timestamp is not None
    with _connect_db(db_path) as connection:
        if notes is None:
            cursor = connection.execute(
                """
                UPDATE ocr_queue
                SET status = ?, updated_at = ?
                WHERE document_url = ?
                """,
                (normalized_status, timestamp, normalized_url),
            )
        else:
            cursor = connection.execute(
                """
                UPDATE ocr_queue
                SET status = ?, notes = ?, updated_at = ?
                WHERE document_url = ?
                """,
                (normalized_status, notes, timestamp, normalized_url),
            )
        connection.commit()
    return bool(cursor.rowcount)


def update_document_text_by_url(
    *,
    document_url: str,
    raw_text: str,
    content_hash: str,
    local_file_path: str | None = None,
    document_type: str = "pdf",
    error: str | None = None,
    db_path: Path | str = DB_PATH,
) -> int:
    normalized_url = (document_url or "").strip()
    if not normalized_url:
        return 0
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE documents
            SET raw_text = ?,
                content_hash = ?,
                local_file_path = COALESCE(?, local_file_path),
                document_type = ?,
                status = 'collected',
                error = ?
            WHERE url = ?
            """,
            (
                raw_text,
                content_hash,
                local_file_path,
                document_type,
                error,
                normalized_url,
            ),
        )
        connection.commit()
    return int(cursor.rowcount or 0)


def reprioritize_high_value_ocr_queue(db_path: Path | str = DB_PATH) -> int:
    timestamp = _serialize_dt(datetime.now(timezone.utc))
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE ocr_queue
            SET priority = 'high', updated_at = ?
            WHERE status = 'pending'
              AND priority != 'high'
              AND document_url IN (
                  SELECT url FROM documents
                  WHERE action_level IN ('requires_attention', 'watchlist')
              )
            """,
            (timestamp,),
        )
        connection.commit()
    return int(cursor.rowcount or 0)


def summarize_ocr_queue(
    *,
    db_path: Path | str = DB_PATH,
) -> dict[str, int]:
    with _connect_db(db_path) as connection:
        pending_row = connection.execute(
            "SELECT COUNT(*) AS total FROM ocr_queue WHERE status = 'pending'"
        ).fetchone()
        high_row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM ocr_queue
            WHERE priority = 'high'
              AND status IN ('pending', 'in_review')
            """
        ).fetchone()
        done_skipped_row = connection.execute(
            "SELECT COUNT(*) AS total FROM ocr_queue WHERE status IN ('done', 'skipped')"
        ).fetchone()
        in_review_row = connection.execute(
            "SELECT COUNT(*) AS total FROM ocr_queue WHERE status = 'in_review'"
        ).fetchone()
        done_row = connection.execute(
            "SELECT COUNT(*) AS total FROM ocr_queue WHERE status = 'done'"
        ).fetchone()
        skipped_row = connection.execute(
            "SELECT COUNT(*) AS total FROM ocr_queue WHERE status = 'skipped'"
        ).fetchone()
        high_priority_pending_row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM ocr_queue
            WHERE priority = 'high'
              AND status = 'pending'
            """
        ).fetchone()
    return {
        "pending": int(pending_row["total"]) if pending_row else 0,
        "high_priority": int(high_row["total"]) if high_row else 0,
        "high_priority_pending": int(high_priority_pending_row["total"]) if high_priority_pending_row else 0,
        "in_review": int(in_review_row["total"]) if in_review_row else 0,
        "done": int(done_row["total"]) if done_row else 0,
        "skipped": int(skipped_row["total"]) if skipped_row else 0,
        "done_skipped": int(done_skipped_row["total"]) if done_skipped_row else 0,
    }


def list_unresolved_scan_candidate_audit(
    *,
    source_name: str | None = None,
    limit: int = 200,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 1000))
    params: list[Any] = []
    where_clauses = [
        "COALESCE(file_type, extracted_type) = 'pdf'",
        "scan_candidate = 1",
        "COALESCE(ocr_status, 'not_needed') != 'success'",
    ]
    if source_name and source_name.strip():
        where_clauses.append("source_name = ?")
        params.append(source_name.strip())

    query = f"""
        SELECT a.*
        FROM document_extraction_audit a
        INNER JOIN (
            SELECT document_url, MAX(id) AS max_id
            FROM document_extraction_audit
            WHERE document_url IS NOT NULL AND trim(document_url) != ''
            GROUP BY document_url
        ) latest ON latest.max_id = a.id
        WHERE {" AND ".join(where_clauses)}
        ORDER BY a.collected_at DESC, a.id DESC
        LIMIT ?
    """
    params.append(safe_limit)
    with _connect_db(db_path) as connection:
        rows = connection.execute(query, tuple(params)).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["has_text"] = bool(payload.get("has_text"))
        payload["scan_candidate"] = bool(payload.get("scan_candidate"))
        payload["needs_ocr"] = bool(payload.get("needs_ocr"))
        payload["ocr_status"] = str(payload.get("ocr_status") or "not_needed")
        payload["ocr_text_length"] = int(payload.get("ocr_text_length") or 0)
        payload["ocr_pages_processed"] = int(payload.get("ocr_pages_processed") or 0)
        payload["collected_at"] = _parse_dt(payload.get("collected_at"))
        result.append(payload)
    return result


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
        payload["ocr_status"] = str(payload.get("ocr_status") or "not_needed")
        payload["ocr_text_length"] = int(payload.get("ocr_text_length") or 0)
        payload["ocr_pages_processed"] = int(payload.get("ocr_pages_processed") or 0)
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


def set_user_default_period_days(
    chat_id: str | int,
    days: int,
    *,
    db_path: Path | str = DB_PATH,
) -> None:
    normalized_days = max(1, min(int(days), 365))
    with _connect_db(db_path) as connection:
        connection.execute(
            """
            INSERT INTO user_preferences(chat_id, default_period_days, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                default_period_days=excluded.default_period_days,
                updated_at=excluded.updated_at
            """,
            (
                str(chat_id),
                normalized_days,
                _serialize_dt(datetime.now(timezone.utc)),
            ),
        )
        connection.commit()


def get_user_default_period_days(
    chat_id: str | int,
    *,
    db_path: Path | str = DB_PATH,
    fallback: int = 7,
) -> int:
    with _connect_db(db_path) as connection:
        row = connection.execute(
            "SELECT default_period_days FROM user_preferences WHERE chat_id = ?",
            (str(chat_id),),
        ).fetchone()
    if row is None:
        return fallback
    try:
        value = int(row["default_period_days"])
    except (TypeError, ValueError):
        return fallback
    return max(1, min(value, 365))


def search_documents(
    query: str,
    *,
    db_path: Path | str = DB_PATH,
    limit: int = 5,
) -> list[RawDocument]:
    normalized = (query or "").strip()
    if not normalized:
        return []
    pattern = f"%{normalized}%"
    normalized_like = f"%{normalized.lower()}%"
    safe_limit = max(1, min(int(limit), 20))
    fetch_limit = max(20, safe_limit * 10)
    with _connect_db(db_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE title LIKE ? COLLATE NOCASE
               OR lower(title) LIKE ? COLLATE NOCASE
               OR source_name LIKE ? COLLATE NOCASE
               OR summary LIKE ? COLLATE NOCASE
               OR raw_text LIKE ? COLLATE NOCASE
            ORDER BY
                CASE action_level
                    WHEN 'requires_attention' THEN 1
                    WHEN 'watchlist' THEN 2
                    WHEN 'background' THEN 3
                    WHEN 'irrelevant' THEN 4
                    ELSE 5
                END ASC,
                CASE
                    WHEN lower(title) = lower(?) THEN 1
                    WHEN lower(title) LIKE lower(?) THEN 2
                    WHEN summary LIKE ? COLLATE NOCASE THEN 3
                    WHEN source_name LIKE ? COLLATE NOCASE THEN 4
                    ELSE 5
                END ASC,
                COALESCE(published_at, collected_at) DESC,
                collected_at DESC
            LIMIT ?
            """,
            (
                pattern,
                normalized_like,
                pattern,
                pattern,
                pattern,
                normalized,
                pattern,
                pattern,
                pattern,
                fetch_limit,
            ),
        ).fetchall()
    documents = [_row_to_document(row) for row in rows]
    preferred = [
        document for document in documents if document.action_level in {"requires_attention", "watchlist"}
    ]
    background = [document for document in documents if document.action_level == "background"]
    ranked = preferred[:safe_limit]
    if len(ranked) < safe_limit:
        ranked.extend(background[: safe_limit - len(ranked)])
    return ranked[:safe_limit]


def get_document_by_url(
    url: str,
    *,
    db_path: Path | str = DB_PATH,
) -> RawDocument | None:
    normalized_url = (url or "").strip()
    if not normalized_url:
        return None
    with _connect_db(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM documents WHERE url = ? LIMIT 1",
            (normalized_url,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_document(row)


def update_document_from_tracking_refresh(
    *,
    document_url: str,
    raw_text: str,
    content_hash: str,
    document_type: str,
    published_at: datetime | None,
    analysis: AnalysisResult,
    db_path: Path | str = DB_PATH,
) -> int:
    normalized_url = (document_url or "").strip()
    if not normalized_url:
        return 0
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE documents
            SET title = ?,
                raw_text = ?,
                content_hash = ?,
                document_type = ?,
                published_at = COALESCE(?, published_at),
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
                status = 'analyzed'
            WHERE url = ?
            """,
            (
                analysis.normalized_title or "",
                raw_text,
                content_hash,
                document_type,
                _serialize_dt(published_at),
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
                normalized_url,
            ),
        )
        connection.commit()
        return int(cursor.rowcount or 0)


def compute_tracking_status_hash(
    *,
    support_status: str | None,
    application_status: str | None,
    deadline_text: str | None,
    terms_text: str | None,
    is_active: bool | None,
    title: str | None,
    summary: str | None,
) -> str:
    payload = "||".join(
        [
            (support_status or "").strip(),
            (application_status or "").strip(),
            (deadline_text or "").strip(),
            (terms_text or "").strip(),
            "" if is_active is None else ("1" if is_active else "0"),
            (title or "").strip(),
            (summary or "").strip(),
        ]
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def get_active_tracking_item(
    chat_id: str | int,
    document_url: str,
    *,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any] | None:
    with _connect_db(db_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM tracking_items
            WHERE chat_id = ? AND document_url = ? AND active = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (str(chat_id), document_url.strip()),
        ).fetchone()
    if row is None:
        return None
    payload = dict(row)
    payload["active"] = bool(payload.get("active"))
    payload["created_at"] = _parse_dt(payload.get("created_at"))
    payload["last_checked_at"] = _parse_dt(payload.get("last_checked_at"))
    return payload


def create_tracking_item(
    *,
    chat_id: str | int,
    document_url: str,
    document_id: int | None,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO tracking_items(chat_id, document_url, document_id, active, created_at)
            VALUES (?, ?, ?, 1, ?)
            """,
            (
                str(chat_id),
                document_url.strip(),
                document_id,
                _serialize_dt(datetime.now(timezone.utc)),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def deactivate_tracking_item(
    *,
    chat_id: str | int,
    document_url: str,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE tracking_items
            SET active = 0
            WHERE chat_id = ? AND document_url = ? AND active = 1
            """,
            (str(chat_id), document_url.strip()),
        )
        connection.commit()
        return int(cursor.rowcount or 0)


def list_active_tracking_items(
    *,
    chat_id: str | int,
    limit: int = 10,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 100))
    with _connect_db(db_path) as connection:
        rows = connection.execute(
            """
            SELECT ti.*,
                   d.title AS document_title,
                   d.source_name AS source_name
            FROM tracking_items ti
            LEFT JOIN documents d ON d.id = ti.document_id
            WHERE ti.chat_id = ? AND ti.active = 1
            ORDER BY ti.created_at DESC
            LIMIT ?
            """,
            (str(chat_id), safe_limit),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["active"] = bool(payload.get("active"))
        payload["created_at"] = _parse_dt(payload.get("created_at"))
        payload["last_checked_at"] = _parse_dt(payload.get("last_checked_at"))
        result.append(payload)
    return result


def list_all_active_tracking_items(
    *,
    db_path: Path | str = DB_PATH,
) -> list[dict[str, Any]]:
    with _connect_db(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM tracking_items
            WHERE active = 1
            ORDER BY id ASC
            """
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        payload["active"] = bool(payload.get("active"))
        payload["created_at"] = _parse_dt(payload.get("created_at"))
        payload["last_checked_at"] = _parse_dt(payload.get("last_checked_at"))
        result.append(payload)
    return result


def update_tracking_item_last_checked(
    tracking_item_id: int,
    *,
    checked_at: datetime | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    with _connect_db(db_path) as connection:
        connection.execute(
            """
            UPDATE tracking_items
            SET last_checked_at = ?
            WHERE id = ?
            """,
            (_serialize_dt(checked_at or datetime.now(timezone.utc)), tracking_item_id),
        )
        connection.commit()


def save_tracking_snapshot(
    *,
    tracking_item_id: int,
    status_hash: str,
    support_status: str | None,
    application_status: str | None,
    deadline_text: str | None,
    terms_text: str | None,
    is_active: bool | None,
    title: str | None,
    summary: str | None,
    checked_at: datetime | None = None,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO tracking_snapshots(
                tracking_item_id, checked_at, status_hash, support_status, application_status,
                deadline_text, terms_text, is_active, title, summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tracking_item_id,
                _serialize_dt(checked_at or datetime.now(timezone.utc)),
                status_hash,
                support_status,
                application_status,
                deadline_text,
                terms_text,
                None if is_active is None else int(is_active),
                title,
                summary,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def get_latest_tracking_snapshot(
    tracking_item_id: int,
    *,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any] | None:
    with _connect_db(db_path) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM tracking_snapshots
            WHERE tracking_item_id = ?
            ORDER BY checked_at DESC, id DESC
            LIMIT 1
            """,
            (tracking_item_id,),
        ).fetchone()
    if row is None:
        return None
    payload = dict(row)
    payload["checked_at"] = _parse_dt(payload.get("checked_at"))
    if payload.get("is_active") is not None:
        payload["is_active"] = bool(payload["is_active"])
    return payload


def save_tracking_event(
    *,
    tracking_item_id: int,
    change_summary: str,
    old_hash: str | None,
    new_hash: str | None,
    detected_at: datetime | None = None,
    notified_at: datetime | None = None,
    db_path: Path | str = DB_PATH,
) -> int:
    with _connect_db(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO tracking_events(
                tracking_item_id, detected_at, change_summary, old_hash, new_hash, notified_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                tracking_item_id,
                _serialize_dt(detected_at or datetime.now(timezone.utc)),
                change_summary,
                old_hash,
                new_hash,
                _serialize_dt(notified_at),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
