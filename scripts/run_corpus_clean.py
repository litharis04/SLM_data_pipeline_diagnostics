#!/usr/bin/env python3
"""Dataset-build gate: one clean baseline per corpus scenario (§21.4).

For each ``scenarios/*.json`` (sorted, sliceable via ``--offset``/``--limit``
for block execution) at one ``--seed``: parse, validate, and
``prepare_clean_instance`` into ``--cache-root``. Records per-scenario
pass/fail plus the failure-record path on failure.

The run CONTINUES past failures (unlike the early stop-on-first-failure
blocks): every failing scenario gets a per-failure evidence report
``<reports-dir>/corpus_fail_<scenario>_<date>.md`` with stage/category,
failing nodes, counts/universes, and the preserved workspace path, plus
empty triage sections (mechanism, systematic-vs-luck, owning layer,
candidate fix) filled during the bulk triage after the full corpus.
Owning-layer fixes happen at the responsible layer per GENERATOR_SPEC §17 /
PIPELINE_SPEC §7 — never by weakening. Usage::

    uv run scripts/run_corpus_clean.py --offset 0 --limit 12 --log artifacts/corpus_clean_2026-09-29.md
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from data_pipeline_diagnostics.generator.cache import prepare_clean_instance
from data_pipeline_diagnostics.generator.raw_constraints import GenerationFailure
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_file
from data_pipeline_diagnostics.scenario.semantic import validate_semantics


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Corpus clean-baseline gate run.")
    parser.add_argument("--cache-root", default="artifacts/corpus_cache")
    parser.add_argument("--scenarios-dir", default="scenarios")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--log", default=None)
    parser.add_argument("--reports-dir", default="artifacts")
    return parser.parse_args()


def _failed_nodes(workspace: Path) -> list[str]:
    """Non-passing dbt nodes with messages from a preserved workspace."""
    import json

    run_results = workspace / "dbt" / "target" / "run_results.json"
    if not run_results.is_file():
        return []
    try:
        results = json.loads(run_results.read_text(encoding="utf-8"))["results"]
    except Exception:
        return []
    lines = []
    for result in results:
        if str(result.get("status", "")) not in ("success", "pass"):
            message = str(result.get("message") or "")[:300]
            lines.append(f"- `{result.get('unique_id')}` {result.get('status')}: {message}")
    return lines


def _workspace_counts(workspace: Path) -> list[str]:
    """Raw row counts per table from a preserved workspace (best effort)."""
    db_path = workspace / "pipeline.duckdb"
    if not db_path.is_file():
        return []
    try:
        import duckdb

        db = duckdb.connect(str(db_path), read_only=True)
        try:
            tables = [
                row[0]
                for row in db.execute(
                    "SELECT table_name FROM duckdb_tables() "
                    "WHERE schema_name = 'raw' ORDER BY table_name"
                ).fetchall()
            ]
            return [
                f"- raw.{table}: "
                f"{db.execute(f'SELECT COUNT(*) FROM "raw"."{table}"').fetchone()[0]} rows"
                for table in tables
            ]
        finally:
            db.close()
    except Exception:
        return []


def _write_failure_report(
    *,
    reports_dir: Path,
    scenario: str,
    seed: int,
    date: str,
    reason: str,
    message: str,
    workspace: Path | None,
) -> Path:
    """Evidence report for one failing scenario; analysis sections stay TBD
    for the bulk triage (systematic-vs-luck verdict needs the full picture)."""
    target = reports_dir / f"corpus_fail_{scenario}_{date}.md"
    lines = [
        f"# Clean failure: {scenario} ({date})",
        "",
        f"- seed: {seed}",
        f"- reason: {reason}",
        f"- message: {message}",
        f"- workspace: `{workspace if workspace is not None else 'n/a (parse/validation stage)'}`",
        "",
        "## Evidence",
        "",
    ]
    if workspace is not None:
        record_path = workspace / "failure_record.json"
        if record_path.is_file():
            import json

            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                lines.append(
                    f"- failure_record: stage `{record.get('stage')}`, "
                    f"category `{record.get('category')}`, "
                    f"exit_status `{record.get('exit_status')}`"
                )
            except Exception:
                lines.append("- failure_record: unreadable")
        nodes = _failed_nodes(workspace)
        lines.append(f"- non-passing dbt nodes ({len(nodes)}):")
        lines.extend(nodes if nodes else ["  - none (no run_results — raw/generate stage)"])
        counts = _workspace_counts(workspace)
        if counts:
            lines.append("- raw row counts:")
            lines.extend(counts)
    else:
        lines.append("- no workspace: failure happened before materialization.")
    lines.extend(
        [
            "",
            "## Triage (bulk review after the full corpus)",
            "",
            "- Mechanism: TBD",
            "- Systematic vs seed luck (capacity math): TBD",
            "- Owning layer (generator / semantic / scenario content): TBD",
            "- Candidate fix + required verification: TBD",
            "",
        ]
    )
    reports_dir.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> int:
    args = _parse_args()
    files = sorted(Path(args.scenarios_dir).glob("*.json"))
    selection = files[args.offset : None if args.limit is None else args.offset + args.limit]
    print(f"corpus={len(files)} offset={args.offset} limit={args.limit} seed={args.seed}")
    print(f"cache={args.cache_root}")
    results: list[dict] = []
    failures = 0
    today = datetime.now(UTC).date().isoformat()
    reports_dir = Path(args.reports_dir)
    for index, path in enumerate(selection, start=args.offset):
        label = f"[{index + 1}/{len(files)}] {path.stem}"
        started = time.time()
        try:
            validated = validate_semantics(parse_scenario_file(path))
            inst = prepare_clean_instance(validated, args.seed, args.cache_root)
        except GenerationFailure as exc:
            elapsed = time.time() - started
            print(f"{label} FAIL ({elapsed:.1f}s) {exc.reason}: {exc}", flush=True)
            report = _write_failure_report(
                reports_dir=reports_dir,
                scenario=path.stem,
                seed=args.seed,
                date=today,
                reason=exc.reason,
                message=str(exc),
                workspace=getattr(exc, "workspace", None),
            )
            print(f"  report: {report}", flush=True)
            results.append(
                {
                    "scenario": path.stem,
                    "ok": False,
                    "detail": f"{exc.reason} (report: {report.name})",
                    "seconds": round(elapsed, 1),
                }
            )
            failures += 1
            continue
        except Exception as exc:  # noqa: BLE001 - collect-and-continue gate behavior
            elapsed = time.time() - started
            print(
                f"{label} FAIL ({elapsed:.1f}s) unexpected: {type(exc).__name__}: {exc}",
                flush=True,
            )
            report = _write_failure_report(
                reports_dir=reports_dir,
                scenario=path.stem,
                seed=args.seed,
                date=today,
                reason="unexpected-error",
                message=f"{type(exc).__name__}: {exc}",
                workspace=None,
            )
            print(f"  report: {report}", flush=True)
            results.append(
                {
                    "scenario": path.stem,
                    "ok": False,
                    "detail": f"unexpected (report: {report.name})",
                    "seconds": round(elapsed, 1),
                }
            )
            failures += 1
            continue
        elapsed = time.time() - started
        hit = "hit" if inst.cache_hit else "miss"
        print(f"{label} PASS ({elapsed:.1f}s) digest={inst.instance_digest[:12]} {hit}", flush=True)
        results.append(
            {
                "scenario": path.stem,
                "ok": True,
                "detail": inst.instance_digest,
                "seconds": round(elapsed, 1),
            }
        )
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print(f"done: {passed}/{total} passed")
    if args.log is not None:
        lines = [
            f"# Corpus clean run ({datetime.now(UTC).date().isoformat()})",
            "",
            f"Scope: offset={args.offset} limit={args.limit} seed={args.seed} "
            f"cache=`{args.cache_root}` — {passed}/{total} passed.",
            "",
            "| scenario | result | detail | seconds |",
            "| --- | --- | --- | --- |",
        ]
        lines.extend(
            f"| {r['scenario']} | {'PASS' if r['ok'] else 'FAIL'} | {r['detail']} | {r['seconds']} |"
            for r in results
        )
        lines.append("")
        Path(args.log).write_text("\n".join(lines), encoding="utf-8")
        print(f"log: {args.log}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
