from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlparse

from app.config import setup_logging
from app.notify.telegram import get_diagnostic_status
from app.notify.telegram_bot import run_polling_listener
from app.pipeline.analyze import run_analyze
from app.pipeline.collect import run_collect_with_options
from app.pipeline.diagnostics import run_diagnostics
from app.pipeline.digest import run_demo_report, run_digest
from app.pipeline.tracking import run_check_tracked
from app.pipeline.run import run_pipeline
from app.pipeline.smoke import run_smoke_check
from app.scheduler import run_scheduler, send_test_notification
from app.storage import (
    init_db,
    list_ocr_queue,
    summarize_ocr_queue,
    update_ocr_queue_status,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MVP для мониторинга нормативных актов, новостей и мер поддержки АПК."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Создать SQLite базу данных.")
    collect_parser = subparsers.add_parser(
        "collect", help="Собрать документы из sources.yaml."
    )
    collect_parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Собрать документы только для одного источника по точному name.",
    )
    collect_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество документов на источник в текущем запуске.",
    )
    collect_parser.add_argument(
        "--audit-existing",
        action="store_true",
        help="Переизвлекать existing документы для покрытия extraction audit без вставки дублей.",
    )
    audit_extraction_parser = subparsers.add_parser(
        "audit-extraction",
        help="Переобход existing/new документов для extraction quality audit без изменения action-level.",
    )
    audit_extraction_parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Переобход только одного источника по точному name.",
    )
    audit_extraction_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество документов на источник в текущем запуске.",
    )

    analyze_parser = subparsers.add_parser(
        "analyze", help="Проанализировать неразобранные документы."
    )
    analyze_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Опционально ограничить число документов на анализ.",
    )
    analyze_parser.add_argument(
        "--force",
        action="store_true",
        help="Переанализировать документы, даже если анализ уже был сохранен.",
    )

    report_parser = subparsers.add_parser("report", help="Сформировать markdown-отчет.")
    report_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Период отчета в днях.",
    )
    report_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Путь к выходному markdown-файлу.",
    )
    report_parser.add_argument(
        "--relevant-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Включать в отчет только релевантные документы.",
    )
    report_parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Ограничить количество документов в отчете.",
    )
    report_parser.add_argument(
        "--action-level",
        nargs="+",
        default=["requires_attention", "watchlist"],
        help="Список action_level для включения в отчет.",
    )
    report_parser.add_argument(
        "--include-background",
        action="store_true",
        help="Включать в отчет документы с action_level=background.",
    )
    report_parser.add_argument(
        "--include-section-pages",
        action="store_true",
        help="Включать в отчет watchlist/background страницы типов section_page/category_page/year_archive.",
    )
    report_parser.add_argument(
        "--include-registries",
        action="store_true",
        help="Включать в отчет watchlist/background страницы типов registry/results_protocol/reference_page.",
    )
    report_parser.add_argument(
        "--include-market-background",
        action="store_true",
        help="Включать в отчет отдельный блок глобального и рыночного фона.",
    )
    report_parser.add_argument(
        "--include-full-background",
        action="store_true",
        help="Показывать полный отраслевой фон и фон вне целевой географии без лимита 5 пунктов.",
    )

    diagnostics_parser = subparsers.add_parser(
        "diagnostics",
        help="Показать диагностику качества источников и action_level по базе.",
    )
    diagnostics_parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Опционально ограничить диагностику последними N днями.",
    )

    smoke_parser = subparsers.add_parser(
        "smoke-check",
        help="Безопасная production smoke-проверка без collect и без Telegram send.",
    )
    smoke_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Период для dry-run report.",
    )

    demo_report_parser = subparsers.add_parser(
        "demo-report",
        help="Сформировать безопасный demo-report для docs/demo_report.md.",
    )
    demo_report_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Период demo report в днях.",
    )
    demo_report_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Путь к demo markdown-файлу.",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Полный прогон: init-db -> collect -> analyze -> report.",
    )
    run_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Период отчета в днях.",
    )
    run_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Путь к выходному markdown-файлу.",
    )
    run_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Опционально ограничить число документов на анализ.",
    )
    run_parser.add_argument(
        "--force-analyze",
        action="store_true",
        help="Переанализировать уже размеченные документы.",
    )

    scheduler_parser = subparsers.add_parser(
        "run-scheduler",
        help="Запустить production scheduler.",
    )
    scheduler_parser.add_argument(
        "--once",
        action="store_true",
        help="Выполнить один цикл collect + analyze + notify + report и завершиться.",
    )
    scheduler_parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Период для ежедневного отчета в днях.",
    )

    subparsers.add_parser(
        "notify-test",
        help="Отправить тестовое Telegram-уведомление, если Telegram настроен.",
    )
    subparsers.add_parser(
        "telegram-check",
        help="Проверить Telegram-конфиг и отправить тестовое сообщение.",
    )
    subparsers.add_parser(
        "run-telegram-bot",
        help="Запустить polling listener для интерактивного Telegram-бота.",
    )
    subparsers.add_parser(
        "check-tracked",
        help="Проверить отслеживаемые документы и отправить уведомления об изменениях.",
    )
    ocr_queue_parser = subparsers.add_parser(
        "ocr-queue",
        help="Показать OCR triage queue (pending/in_review по умолчанию).",
    )
    ocr_queue_parser.add_argument(
        "--status",
        choices=["pending", "in_review", "done", "skipped"],
        default=None,
        help="Фильтр очереди по статусу.",
    )
    ocr_queue_parser.add_argument(
        "--priority",
        choices=["high", "medium", "low"],
        default=None,
        help="Фильтр очереди по приоритету.",
    )
    ocr_queue_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Максимум элементов в выводе.",
    )
    ocr_mark_parser = subparsers.add_parser(
        "ocr-mark",
        help="Обновить статус OCR triage элемента по URL.",
    )
    ocr_mark_parser.add_argument("url", help="document_url из OCR очереди.")
    ocr_mark_parser.add_argument(
        "--status",
        required=True,
        choices=["pending", "in_review", "done", "skipped"],
        help="Новый статус.",
    )
    ocr_mark_parser.add_argument(
        "--notes",
        default=None,
        help="Опциональные заметки для triage.",
    )

    return parser


def _print_telegram_diagnostics(*, sent: bool) -> None:
    status = get_diagnostic_status()
    print(f"Telegram configured: {'yes' if status['telegram_configured'] else 'no'}")
    print(f"Proxy configured: {'yes' if status['proxy_configured'] else 'no'}")
    print(f"Send result: {'success' if sent else 'fail'}")


def _render_ocr_queue_lines(
    *,
    rows: list[dict[str, object]],
    summary: dict[str, int],
    status_filter: str | None,
    priority_filter: str | None,
) -> list[str]:
    def _short_title(value: object, *, max_len: int = 56) -> str:
        raw = str(value or "(no title)").strip()
        if len(raw) <= max_len:
            return raw
        return f"{raw[: max_len - 3].rstrip()}..."

    def _short_url(value: object, *, max_len: int = 44) -> str:
        raw = str(value or "").strip()
        if not raw:
            return "-"
        parsed = urlparse(raw)
        short = f"{parsed.netloc}{parsed.path}" if parsed.netloc else raw
        if len(short) <= max_len:
            return short
        return f"{short[: max_len - 3].rstrip()}..."

    lines = [
        "OCR triage queue",
        f"Pending: {summary['pending']}",
        f"High priority pending: {summary['high_priority_pending']}",
        f"Done/skipped: {summary['done_skipped']}",
    ]
    if status_filter:
        lines.append(f"Filter: {status_filter}")
    if priority_filter:
        lines.append(f"Priority: {priority_filter}")
    if not rows:
        lines.append("No OCR queue items for selected filter.")
        return lines
    lines.append("")
    lines.append("Top items:")
    lines.append("PRIORITY STATUS     SOURCE                                  TITLE                                                    URL")
    lines.append("-------- ---------- --------------------------------------- -------------------------------------------------------- --------------------------------------------")
    for row in rows:
        priority = str(row.get("priority") or "-")
        status = str(row.get("status") or "-")
        source = str(row.get("source_name") or "-")
        title = _short_title(row.get("title"))
        short_url = _short_url(row.get("document_url"))
        lines.append(
            f"{priority:<8} {status:<10} {source[:39]:<39} {title:<56} {short_url}"
        )
    return lines


def main() -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
        print("Database initialized.")
        return 0

    if args.command == "collect":
        count = run_collect_with_options(
            source_name=args.source,
            limit=args.limit,
            audit_existing=args.audit_existing,
        )
        print(f"Collected {count} new documents.")
        return 0

    if args.command == "audit-extraction":
        count = run_collect_with_options(
            source_name=args.source,
            limit=args.limit,
            audit_existing=True,
        )
        print(f"Extraction audit completed. New documents inserted: {count}.")
        return 0

    if args.command == "analyze":
        count = run_analyze(limit=args.limit, reanalyze=args.force)
        print(f"Analyzed {count} documents.")
        return 0

    if args.command == "report":
        output_path = run_digest(
            days=args.days,
            output_path=args.output,
            relevant_only=args.relevant_only,
            max_items=args.max_items,
            action_levels=args.action_level,
            include_background=args.include_background,
            include_section_pages=args.include_section_pages,
            include_registries=args.include_registries,
            include_market_background=args.include_market_background,
            include_full_background=args.include_full_background,
        )
        print(f"Report saved to {Path(output_path).resolve()}")
        return 0

    if args.command == "diagnostics":
        print(run_diagnostics(days=args.days))
        return 0

    if args.command == "smoke-check":
        result = run_smoke_check(report_days=args.days)
        print(result.render_text())
        return result.exit_code

    if args.command == "demo-report":
        output_path = run_demo_report(days=args.days, output_path=args.output)
        print(f"Demo report saved to {Path(output_path).resolve()}")
        return 0

    if args.command == "run":
        result = run_pipeline(
            days=args.days,
            output_path=args.output,
            analyze_limit=args.limit,
            force_reanalyze=args.force_analyze,
        )
        print(
            "Pipeline finished. "
            f"Collected={result.collected_count}, "
            f"Analyzed={result.analyzed_count}, "
            f"Report={Path(result.report_path).resolve()}"
        )
        return 0

    if args.command == "run-scheduler":
        run_scheduler(once=args.once, days=args.days)
        print("Scheduler run completed." if args.once else "Scheduler started.")
        return 0

    if args.command == "notify-test":
        sent = send_test_notification()
        _print_telegram_diagnostics(sent=sent)
        if not sent:
            print(
                "Details: check TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "
                "TELEGRAM_PROXY_URL and logs."
            )
        return 0

    if args.command == "telegram-check":
        sent = send_test_notification(command_name="telegram-check")
        _print_telegram_diagnostics(sent=sent)
        if not sent:
            print("Details: Telegram config is incomplete or delivery failed.")
        return 0

    if args.command == "run-telegram-bot":
        run_polling_listener()
        return 0

    if args.command == "check-tracked":
        result = run_check_tracked()
        print(
            "Tracked check finished. "
            f"Checked={result.checked}, Changed={result.changed}, "
            f"Notified={result.notified}, Errors={result.errors}"
        )
        return 0

    if args.command == "ocr-queue":
        status_values = [args.status] if args.status else ["pending", "in_review"]
        priority_values = [args.priority] if args.priority else None
        rows = list_ocr_queue(
            statuses=status_values,
            priorities=priority_values,
            limit=args.limit,
        )
        summary = summarize_ocr_queue()
        print(
            "\n".join(
                _render_ocr_queue_lines(
                    rows=rows,
                    summary=summary,
                    status_filter=args.status,
                    priority_filter=args.priority,
                )
            )
        )
        return 0

    if args.command == "ocr-mark":
        updated = update_ocr_queue_status(
            document_url=args.url,
            status=args.status,
            notes=args.notes,
        )
        if not updated:
            print("OCR queue item not found for provided URL.")
            return 1
        print(f"OCR queue updated: status={args.status}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
