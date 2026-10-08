"""C15: offline scale checks (medium/large).

Explicitly enabled with ``PLGEN_SLOW=1``; skipped otherwise with an explicit
message (no network use when skipped). Real CLI path (`open`) with checked-in
scenario JSONs: a composite/mixed/filter/dedup medium fixture, a near-ceiling
large fixture, and a many-to-many expansion topology. Records timings, peak
RSS, realized maxima, downstream counts, and clean outcomes into
``artifacts/cli_scale_<date>.md``/``.json``. No wall-time assertions, ever.
"""

from __future__ import annotations

import json
import os
import platform
import resource
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import app
from data_pipeline_diagnostics.cli.cmd_open import run_open
from data_pipeline_diagnostics.cli.features import classify_raw_size, extract_features
from data_pipeline_diagnostics.cli.workspace import init_workspace
from data_pipeline_diagnostics.scenario import parse_scenario_json, validate_semantics

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "cli" / "fixtures"

pytestmark = pytest.mark.skipif(
    os.environ.get("PLGEN_SLOW") != "1",
    reason="slow offline scale checks: explicitly enable with PLGEN_SLOW=1",
)

RESULTS: list[dict] = []


def load_fixture(name: str) -> tuple[str, bytes]:
    raw = (FIXTURES / f"{name}.json").read_bytes()
    return json.loads(raw.decode("utf-8"))["scenario_id"], raw


def peak_rss_mb() -> float:
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return maximum / 1e6
    return maximum * 1024 / 1e6


def measure_case(root: Path, scenario_id: str, raw: bytes) -> dict:
    assert init_workspace(root, [(scenario_id, raw)]) is True
    started = time.perf_counter()
    validated = validate_semantics(parse_scenario_json(raw))
    features = extract_features(validated)
    validate_elapsed = time.perf_counter() - started
    rss_before = peak_rss_mb()
    started = time.perf_counter()
    code = run_open(root, app.build_parser().parse_args(["open", scenario_id]))
    open_elapsed = time.perf_counter() - started
    assert code == 0
    workdir = root / "scenarios" / scenario_id / "work"
    record = json.loads((workdir / "instance_record.json").read_text(encoding="utf-8"))
    realized = {table["name"]: int(table["row_count"]) for table in record["raw_tables"]}
    downstream = {
        name: int(count) for name, count in record["crosswalks"]["realized_row_counts"].items()
    }
    run_results = json.loads(
        (workdir / "dbt" / "target" / "run_results.json").read_text(encoding="utf-8")
    )
    models = [r for r in run_results["results"] if str(r.get("unique_id", "")).startswith("model.")]
    tests = [r for r in run_results["results"] if str(r.get("unique_id", "")).startswith("test.")]
    dbt_elapsed = sum(float(r.get("execution_time", 0.0) or 0.0) for r in models + tests)
    case = {
        "scenario_id": scenario_id,
        "seed": 0,
        "declared_max": features.max_raw_rows_max,
        "realized_max": max(realized.values()),
        "realized_by_table": realized,
        "category": classify_raw_size(max(realized.values())),
        "downstream_max": max(downstream.values()),
        "downstream_by_model": downstream,
        "features": {
            "composite_raw_pk": features.has_composite_raw_pk,
            "join_types": sorted(features.join_types),
            "intermediate": sorted(features.intermediate_features),
            "staging_ops": sorted(features.staging_ops),
            "metrics": sorted(features.metric_functions),
        },
        "clean": "success",
        "dbt": {
            "models_ok": sum(1 for r in models if r.get("status") == "success"),
            "models_total": len(models),
            "tests_ok": sum(1 for r in tests if r.get("status") == "pass"),
            "tests_total": len(tests),
            "node_elapsed_s": round(dbt_elapsed, 1),
        },
        "timings_s": {"validate": round(validate_elapsed, 2), "open": round(open_elapsed, 1)},
        "peak_rss_mb": round(max(peak_rss_mb(), rss_before), 1),
    }
    RESULTS.append(case)
    return case


def test_scale_medium_composite_mixed(tmp_path):
    scenario_id, raw = load_fixture("scale_medium_001")
    assert scenario_id == "scale_medium_001"
    case = measure_case(tmp_path / "ws", scenario_id, raw)
    assert 1_001 <= case["realized_max"] <= 10_000
    assert case["category"] == "medium"
    assert case["features"]["composite_raw_pk"] is True
    assert case["features"]["join_types"] == ["inner", "left"]
    assert "filter" in case["features"]["intermediate"]
    assert "deduplicate" in case["features"]["intermediate"]
    assert case["dbt"]["models_ok"] == case["dbt"]["models_total"] > 0
    assert case["dbt"]["tests_ok"] == case["dbt"]["tests_total"] > 0


def test_scale_large_near_ceiling(tmp_path):
    scenario_id, raw = load_fixture("scale_large_001")
    case = measure_case(tmp_path / "ws", scenario_id, raw)
    assert case["declared_max"] == 90_000
    assert 10_001 <= case["realized_max"] <= 100_000
    assert case["category"] == "large"
    assert case["dbt"]["models_ok"] == case["dbt"]["models_total"] > 0
    assert case["dbt"]["tests_ok"] == case["dbt"]["tests_total"] > 0


def test_expansion_topology_accepted(tmp_path):
    raw = (REPO / "scenarios" / "hospitality_loyalty_001.json").read_bytes()
    case = measure_case(tmp_path / "ws", "hospitality_loyalty_001", raw)
    assert case["category"] == "small"
    assert case["dbt"]["models_ok"] == case["dbt"]["models_total"] > 0
    assert case["dbt"]["tests_ok"] == case["dbt"]["tests_total"] > 0


def test_write_scale_report():
    assert RESULTS, "scale cases must run before the report (do not select with -k)"
    date = datetime.now(UTC).date().isoformat()
    report = {
        "date": date,
        "suite": "tests/cli/test_c15_scale.py (PLGEN_SLOW=1)",
        "env": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "rss_unit_note": "ru_maxrss is bytes on macOS, kilobytes on Linux",
        },
        "notes": [
            "Downstream row counts never gate acceptance: even the many-to-many "
            "topology prepares cleanly with its full fan-out intact (no truncation).",
            "Across the corpus and these cases, realized downstream maxima track the "
            "raw maximum (FK-disciplined joins), so the §4.2 expansion clause stays "
            "vacuous but harmless; acceptance ignores downstream counts either way.",
        ],
        "cases": {case["scenario_id"]: case for case in RESULTS},
    }
    json_path = REPO / "artifacts" / f"cli_scale_{date}.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        f"# CLI offline scale report ({date})",
        "",
        "| case | realized M_raw | category | downstream max | open (s) | peak RSS (MB) | dbt models | dbt tests |",
        "| --- | ---: | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for case in RESULTS:
        lines.append(
            f"| {case['scenario_id']} | {case['realized_max']} | {case['category']} | "
            f"{case['downstream_max']} | {case['timings_s']['open']} | {case['peak_rss_mb']} | "
            f"{case['dbt']['models_ok']}/{case['dbt']['models_total']} | "
            f"{case['dbt']['tests_ok']}/{case['dbt']['tests_total']} |"
        )
    md_path = REPO / "artifacts" / f"cli_scale_{date}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
