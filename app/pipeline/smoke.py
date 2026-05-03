from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app import config
from app.notify import telegram
from app.pipeline.diagnostics import run_diagnostics
from app.pipeline.digest import run_digest
from app.storage import init_db

REQUIRED_TABLES = {"documents", "source_errors"}
DEFAULT_FIXTURES_DIR = Path("tests/fixtures/regression")
SMOKE_ARTIFACTS_DIR = config.DATA_DIR / "test_artifacts" / "smoke"


@dataclass(slots=True)
class SmokeCheckItem:
    status: str
    label: str
    details: str = ""

    def render(self) -> str:
        suffix = f": {self.details}" if self.details else ""
        return f"[{self.status}] {self.label}{suffix}"


@dataclass(slots=True)
class SmokeCheckResult:
    items: list[SmokeCheckItem] = field(default_factory=list)

    def add_ok(self, label: str, details: str = "") -> None:
        self.items.append(SmokeCheckItem("OK", label, details))

    def add_warn(self, label: str, details: str = "") -> None:
        self.items.append(SmokeCheckItem("WARN", label, details))

    def add_fail(self, label: str, details: str = "") -> None:
        self.items.append(SmokeCheckItem("FAIL", label, details))

    @property
    def warning_count(self) -> int:
        return sum(1 for item in self.items if item.status == "WARN")

    @property
    def failure_count(self) -> int:
        return sum(1 for item in self.items if item.status == "FAIL")

    @property
    def passed(self) -> bool:
        return self.failure_count == 0

    @property
    def exit_code(self) -> int:
        return 0 if self.passed else 1

    def render_text(self) -> str:
        lines = ["AHSTEP Smoke Check"]
        lines.extend(item.render() for item in self.items)
        if self.failure_count:
            lines.append(
                f"Smoke check failed: {self.failure_count} critical issue(s), warnings: {self.warning_count}"
            )
        else:
            lines.append(f"Smoke check passed with warnings: {self.warning_count}")
        return "\n".join(lines)


def run_smoke_check(
    *,
    db_path: Path | str | None = None,
    sources_path: Path | None = None,
    keywords_path: Path | None = None,
    fixtures_dir: Path | None = None,
    report_days: int = 7,
) -> SmokeCheckResult:
    result = SmokeCheckResult()
    resolved_db_path = Path(db_path) if db_path is not None else config.DB_PATH
    resolved_sources_path = sources_path or (config.CONFIG_DIR / "sources.yaml")
    resolved_keywords_path = keywords_path or (config.CONFIG_DIR / "keywords.yaml")
    resolved_fixtures_dir = fixtures_dir or DEFAULT_FIXTURES_DIR

    try:
        config.ensure_directories()
        result.add_ok("config loaded")
    except Exception as exc:
        result.add_fail("config loaded", str(exc))
        return result

    try:
        init_db(resolved_db_path)
        result.add_ok("database reachable", str(Path(resolved_db_path)))
    except Exception as exc:
        result.add_fail("database reachable", str(exc))
        return result

    try:
        table_names = _read_table_names(resolved_db_path)
        missing_tables = sorted(REQUIRED_TABLES - table_names)
        if missing_tables:
            result.add_fail("tables exist", f"missing: {', '.join(missing_tables)}")
        else:
            result.add_ok("tables exist", ", ".join(sorted(REQUIRED_TABLES)))
    except Exception as exc:
        result.add_fail("tables exist", str(exc))

    sources: list[object] = []
    try:
        sources = config.load_sources(resolved_sources_path)
        if not sources:
            result.add_fail("sources loaded", "0")
        else:
            result.add_ok("sources loaded", str(len(sources)))
    except Exception as exc:
        result.add_fail("sources loaded", str(exc))

    try:
        source_roles = sorted({source.source_role for source in sources})
        if not source_roles:
            result.add_fail("source roles", "none found")
        else:
            result.add_ok("source roles", ", ".join(source_roles))
    except Exception as exc:
        result.add_fail("source roles", str(exc))

    try:
        keyword_groups = config.load_keyword_groups(resolved_keywords_path)
        keywords = config.load_keywords(resolved_keywords_path)
        if not keyword_groups or not keywords:
            result.add_fail("keywords loaded", "empty keyword config")
        else:
            result.add_ok(
                "keywords loaded",
                f"{len(keywords)} keywords across {len(keyword_groups)} groups",
            )
    except Exception as exc:
        result.add_fail("keywords loaded", str(exc))

    try:
        diagnostics_text = run_diagnostics(db_path=resolved_db_path)
        if "AHSTEP Source Diagnostics" not in diagnostics_text:
            result.add_fail("diagnostics generated", "unexpected diagnostics output")
        else:
            result.add_ok("diagnostics generated")
    except Exception as exc:
        result.add_fail("diagnostics generated", str(exc))

    try:
        config.ensure_directories()
        SMOKE_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        temp_report_path = SMOKE_ARTIFACTS_DIR / f"smoke_report_{uuid.uuid4().hex}.md"
        generated_report = run_digest(
            days=report_days,
            output_path=str(temp_report_path),
            db_path=resolved_db_path,
            relevant_only=True,
            action_levels=["requires_attention", "watchlist"],
        )
        if generated_report.exists() and generated_report.read_text(encoding="utf-8").strip():
            result.add_ok("report dry-run generated")
        else:
            result.add_fail("report dry-run generated", "empty report output")
        if generated_report.exists():
            generated_report.unlink()
    except Exception as exc:
        result.add_fail("report dry-run generated", str(exc))

    telegram_status = telegram.get_diagnostic_status()
    if telegram_status["telegram_configured"]:
        details = "credentials configured; dry-run only, message not sent"
        if telegram_status["proxy_configured"]:
            details += "; proxy configured"
        result.add_ok("Telegram config", details)
    else:
        result.add_warn("Telegram credentials not configured")

    try:
        fixture_paths = sorted(resolved_fixtures_dir.glob("*.json"))
        if fixture_paths:
            result.add_ok("regression fixtures found", str(len(fixture_paths)))
        else:
            result.add_fail("regression fixtures found", "0")
    except Exception as exc:
        result.add_fail("regression fixtures found", str(exc))

    return result


def _read_table_names(db_path: Path | str) -> set[str]:
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    return {str(row[0]) for row in rows}
