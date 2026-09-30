#!/usr/bin/env python3
"""Dataset-build gate: one clean baseline per corpus scenario (§21.4).

For each ``scenarios/*.json`` (sorted, sliceable via ``--offset``/``--limit``
for block execution) at one ``--seed``: parse, validate, and
``prepare_clean_instance`` into ``--cache-root``. Records per-scenario
pass/fail plus the failure-record path on failure.

On ANY failure the run stops, the workspace is kept, and the report names
the owning layer per GENERATOR_SPEC §17 / PIPELINE_SPEC §7 (defect, omitted
invariant, or data-incompatible content — fixed at the responsible layer,
never by weakening). Usage::

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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    files = sorted(Path(args.scenarios_dir).glob("*.json"))
    selection = files[args.offset : None if args.limit is None else args.offset + args.limit]
    print(f"corpus={len(files)} offset={args.offset} limit={args.limit} seed={args.seed}")
    print(f"cache={args.cache_root}")
    results: list[dict] = []
    failures = 0
    for index, path in enumerate(selection, start=args.offset):
        label = f"[{index + 1}/{len(files)}] {path.stem}"
        started = time.time()
        try:
            validated = validate_semantics(parse_scenario_file(path))
            inst = prepare_clean_instance(validated, args.seed, args.cache_root)
        except GenerationFailure as exc:
            elapsed = time.time() - started
            print(f"{label} FAIL ({elapsed:.1f}s) {exc.reason}: {exc}", flush=True)
            results.append(
                {
                    "scenario": path.stem,
                    "ok": False,
                    "detail": f"{exc.reason}: {exc}",
                    "seconds": round(elapsed, 1),
                }
            )
            failures += 1
            break
        except Exception as exc:  # noqa: BLE001 - stop-and-report gate behavior
            elapsed = time.time() - started
            print(
                f"{label} FAIL ({elapsed:.1f}s) unexpected: {type(exc).__name__}: {exc}", flush=True
            )
            results.append(
                {
                    "scenario": path.stem,
                    "ok": False,
                    "detail": f"unexpected: {type(exc).__name__}: {exc}",
                    "seconds": round(elapsed, 1),
                }
            )
            failures += 1
            break
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
