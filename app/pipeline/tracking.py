from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import DB_PATH, load_keyword_groups, load_keywords, load_sources
from app.models import CollectedItem, RawDocument
from app.notify.telegram import send_message_to_chat
from app.pipeline.collect import extract_document
from app.pipeline.deduplicate import compute_content_hash
from app.storage import (
    compute_tracking_status_hash,
    get_document_by_url,
    get_latest_tracking_snapshot,
    init_db,
    list_all_active_tracking_items,
    mark_runtime_event,
    save_tracking_event,
    save_tracking_snapshot,
    update_document_from_tracking_refresh,
    update_tracking_item_last_checked,
)
from app.llm.mock_client import MockLLMClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackingCheckResult:
    checked: int
    changed: int
    notified: int
    errors: int


def run_check_tracked(
    *,
    db_path: Path | str | None = None,
    notify: bool = True,
) -> TrackingCheckResult:
    resolved_db_path = db_path or DB_PATH
    init_db(resolved_db_path)
    items = list_all_active_tracking_items(db_path=resolved_db_path)
    if not items:
        mark_runtime_event("check_tracked", details="checked=0; changed=0; notified=0", db_path=resolved_db_path)
        return TrackingCheckResult(checked=0, changed=0, notified=0, errors=0)

    sources_by_name = {source.name: source for source in load_sources() if source.enabled}
    client = MockLLMClient(load_keywords(), keyword_groups=load_keyword_groups())

    checked = 0
    changed = 0
    notified = 0
    errors = 0
    now = datetime.now(timezone.utc)

    for item in items:
        tracking_item_id = int(item["id"])
        chat_id = str(item["chat_id"])
        document_url = str(item["document_url"])
        try:
            current_document = _refresh_document_for_tracking(
                document_url=document_url,
                db_path=resolved_db_path,
                client=client,
                sources_by_name=sources_by_name,
            )
            if current_document is None:
                errors += 1
                continue

            checked += 1
            new_hash = compute_tracking_status_hash(
                support_status=current_document.support_status,
                application_status=current_document.application_status,
                deadline_text=current_document.deadline_text,
                terms_text=current_document.terms_text,
                is_active=current_document.is_active,
                title=current_document.title,
                summary=current_document.summary,
            )
            previous_snapshot = get_latest_tracking_snapshot(tracking_item_id, db_path=resolved_db_path)
            old_hash = previous_snapshot["status_hash"] if previous_snapshot else None

            save_tracking_snapshot(
                tracking_item_id=tracking_item_id,
                checked_at=now,
                status_hash=new_hash,
                support_status=current_document.support_status,
                application_status=current_document.application_status,
                deadline_text=current_document.deadline_text,
                terms_text=current_document.terms_text,
                is_active=current_document.is_active,
                title=current_document.title,
                summary=current_document.summary,
                db_path=resolved_db_path,
            )
            update_tracking_item_last_checked(tracking_item_id, checked_at=now, db_path=resolved_db_path)

            if old_hash is not None and old_hash != new_hash:
                changed += 1
                change_summary = _build_change_summary(previous_snapshot or {}, current_document)
                notified_at = None
                if notify:
                    sent = send_message_to_chat(
                        chat_id=chat_id,
                        text=_build_tracking_notification_text(current_document, change_summary),
                    )
                    if sent:
                        notified += 1
                        notified_at = datetime.now(timezone.utc)
                save_tracking_event(
                    tracking_item_id=tracking_item_id,
                    detected_at=now,
                    change_summary=change_summary,
                    old_hash=old_hash,
                    new_hash=new_hash,
                    notified_at=notified_at,
                    db_path=resolved_db_path,
                )
        except Exception as exc:
            errors += 1
            logger.warning("check-tracked failed for url=%s: %s", document_url, exc)
            continue

    mark_runtime_event(
        "check_tracked",
        details=f"checked={checked}; changed={changed}; notified={notified}; errors={errors}",
        db_path=resolved_db_path,
    )
    return TrackingCheckResult(checked=checked, changed=changed, notified=notified, errors=errors)


def _refresh_document_for_tracking(
    *,
    document_url: str,
    db_path: Path | str,
    client: MockLLMClient,
    sources_by_name: dict[str, object],
) -> RawDocument | None:
    document = get_document_by_url(document_url, db_path=db_path)
    if document is None:
        return None

    source_config = sources_by_name.get(document.source_name)
    if source_config is None:
        return document

    try:
        item = CollectedItem(
            source_name=document.source_name,
            source_url=document.source_url,
            level=document.level,
            region=document.region,
            title=document.title,
            url=document.url,
            published_at=document.published_at,
            document_type=document.document_type or "unknown",
        )
        extracted = extract_document(item, source_config)
        refreshed_raw_text = extracted.raw_text or document.raw_text or ""
        analysis = client.analyze_document(
            document.title,
            refreshed_raw_text,
            source_name=document.source_name,
            url=document.url,
            level=document.level,
            region=document.region,
        )
        refreshed_hash = compute_content_hash(
            refreshed_raw_text,
            fallback=f"{document.title}\n{document.url}",
        )
        update_document_from_tracking_refresh(
            document_url=document.url,
            raw_text=refreshed_raw_text,
            content_hash=refreshed_hash,
            document_type=extracted.document_type or document.document_type or "unknown",
            published_at=extracted.published_at or document.published_at,
            analysis=analysis,
            db_path=db_path,
        )
        return get_document_by_url(document_url, db_path=db_path) or document
    except Exception as exc:
        logger.warning("tracking refresh failed for url=%s: %s", document.url, exc)
        return document


def _build_change_summary(previous_snapshot: dict[str, object], document: RawDocument) -> str:
    changes: list[str] = []
    old_support = str(previous_snapshot.get("support_status") or "")
    new_support = str(document.support_status or "")
    if old_support != new_support:
        changes.append(f"статус меры: {old_support or 'н/д'} -> {new_support or 'н/д'}")

    old_application = str(previous_snapshot.get("application_status") or "")
    new_application = str(document.application_status or "")
    if old_application != new_application:
        changes.append(f"статус подачи: {old_application or 'н/д'} -> {new_application or 'н/д'}")

    old_deadline = str(previous_snapshot.get("deadline_text") or "")
    new_deadline = str(document.deadline_text or "")
    if old_deadline != new_deadline:
        changes.append("изменился дедлайн")

    old_terms = str(previous_snapshot.get("terms_text") or "")
    new_terms = str(document.terms_text or "")
    if old_terms != new_terms:
        changes.append("изменились условия")

    old_is_active = previous_snapshot.get("is_active")
    if isinstance(old_is_active, bool) and document.is_active is not None and old_is_active != document.is_active:
        changes.append("изменился признак активности")

    old_title = str(previous_snapshot.get("title") or "")
    if old_title != (document.title or ""):
        changes.append("обновился заголовок")

    old_summary = str(previous_snapshot.get("summary") or "")
    if old_summary != (document.summary or ""):
        changes.append("обновилось описание")

    if not changes:
        return "Обновились признаки документа"
    return "; ".join(changes)


def _build_tracking_notification_text(document: RawDocument, change_summary: str) -> str:
    lines = [
        "⭐ Обновление по отслеживаемому документу",
        f"Документ: {document.title}",
        f"Источник: {document.source_name}",
        f"Изменения: {change_summary}",
        f"Ссылка: {document.url}",
    ]
    return "\n".join(lines)
