from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import app
from data_pipeline_diagnostics.cli.cmd_seed import run_seed
from data_pipeline_diagnostics.cli.features import extract_features, normalize_request
from data_pipeline_diagnostics.cli.workspace import init_workspace
from data_pipeline_diagnostics.generator.cache import identity_digest
from data_pipeline_diagnostics.scenario import (
    parse_scenario_json,
    scenario_content_hash,
    validate_semantics,
)

REPO = Path(__file__).resolve().parents[2]
MINI_ID = "minimal_fixture"
MINI_BYTES = (REPO / "tests/scenario/fixtures/valid/minimal.json").read_bytes()
FAILING_ID = "failing_row_count"
FAILING_BYTES = (REPO / "tests/generator/fixtures/failing_row_count.json").read_bytes()

MINI_VALIDATED = validate_semantics(parse_scenario_json(MINI_BYTES))
OTHER_SEED = 7


def stage(root: Path, scenario_id: str = MINI_ID, raw: bytes = MINI_BYTES) -> Path:
    assert init_workspace(root, [(scenario_id, raw)]) is True
    return root


def parse(argv: list[str]) -> argparse.Namespace:
    return app.build_parser().parse_args(argv)


def run(root: Path, argv: list[str], cache: Path, **kwargs):
    return run_seed(root, parse(argv), cache_root=cache, **kwargs)


def read_entry(root: Path, scenario_id: str = MINI_ID) -> dict:
    return json.loads((root / "scenarios" / scenario_id / "entry.json").read_text())


@pytest.fixture(scope="module")
def shared_cache(tmp_path_factory):
    return tmp_path_factory.mktemp("cli-seed-cache")


@pytest.fixture(scope="module")
def ws_seeded(tmp_path_factory):
    return stage(tmp_path_factory.mktemp("cli-seed-ws"))


def test_seed_other_updates_seed_pointer_and_summarizes(ws_seeded, shared_cache, capsys):
    before = (ws_seeded / "scenarios" / MINI_ID / "scenario.json").read_bytes()
    code = run(ws_seeded, ["seed", MINI_ID, str(OTHER_SEED)], shared_cache)
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert f"scenario: {MINI_ID}" in out
    assert f"seed: {OTHER_SEED}" in out
    assert "state: prepared" in out
    assert "models: staging=3 intermediate=3 output=2" in out
    assert "name layer rows" in out
    assert "largest raw table:" in out
    assert "dbt build: success" in out
    assert "output " in out
    assert "working copy created:" in out
    workdir = ws_seeded / "scenarios" / MINI_ID / "work"
    assert f"workdir: {workdir.resolve()}" in out
    assert (ws_seeded / "scenarios" / MINI_ID / "scenario.json").read_bytes() == before
    entry = read_entry(ws_seeded)
    assert entry["seed"] == OTHER_SEED
    assert entry["instance"]["path"] == str(workdir.resolve())
    assert entry["instance"]["digest"] == identity_digest(MINI_VALIDATED, OTHER_SEED)
    assert entry["scenario_hash"] == scenario_content_hash(MINI_VALIDATED.scenario)


def test_same_seed_restores_pristine_copy(ws_seeded, shared_cache, capsys):
    workdir = ws_seeded / "scenarios" / MINI_ID / "work"
    before_entry = (ws_seeded / "scenarios" / MINI_ID / "entry.json").read_bytes()
    target = workdir / "scenario.json"
    target.write_bytes(b"tampered")
    code = run(ws_seeded, ["seed", MINI_ID, str(OTHER_SEED)], shared_cache)
    out, _ = capsys.readouterr()
    assert code == 0
    assert "working copy replaced:" in out
    entry = json.loads((ws_seeded / "scenarios" / MINI_ID / "entry.json").read_bytes())
    assert entry["instance"] == json.loads(before_entry)["instance"]
    baseline = shared_cache / "v1" / entry["instance"]["digest"] / "scenario.json"
    assert target.read_bytes() == baseline.read_bytes()


@pytest.mark.parametrize(
    "argv",
    [
        ["seed", MINI_ID, "-1"],
        ["seed", MINI_ID, str(2**63)],
        ["seed", MINI_ID, "abc"],
    ],
)
def test_bad_seed_exits_2(tmp_path, shared_cache, capsys, argv):
    root = stage(tmp_path / "ws")
    with pytest.raises(SystemExit) as exc:
        app.main(["--workspace", str(root), *argv])
    assert exc.value.code == 2
    _, err = capsys.readouterr()
    assert "Traceback" not in err
    entry = read_entry(root)
    assert entry["seed"] == 0
    assert entry["instance"] is None
    assert not (root / "scenarios" / MINI_ID / "work").exists()


def test_unknown_id_exits_2(tmp_path, shared_cache, capsys):
    root = stage(tmp_path / "ws")
    assert app.main(["--workspace", str(root), "seed", "nope_001", "3"]) == 2
    _, err = capsys.readouterr()
    assert "unknown scenario" in err
    assert "Traceback" not in err


def test_hand_edited_scenario_violating_saved_requirement(tmp_path, shared_cache, capsys):
    root = stage(tmp_path / "ws")
    scenario_file = root / "scenarios" / MINI_ID / "scenario.json"
    edited = json.loads(scenario_file.read_text(encoding="utf-8"))
    edited["domain"] = "changedomain"
    scenario_file.write_text(json.dumps(edited), encoding="utf-8")

    def forbidden(validated, seed, cache_root):
        raise AssertionError("must not build")

    features = extract_features(validate_semantics(parse_scenario_json(MINI_BYTES)))
    good = normalize_request(
        scenario_id=MINI_ID,
        domain="testdomain",
        size="small",
        composite_keys="forbidden",
        staging=sorted(features.staging_ops),
        intermediate=sorted(features.intermediate_features),
        joins="inner" if features.join_types == {"inner"} else "auto",
        metrics=sorted(features.metric_functions),
    ).to_dict()
    entry_file = root / "scenarios" / MINI_ID / "entry.json"
    entry = json.loads(entry_file.read_text(encoding="utf-8"))
    entry["origin"] = "authored"
    entry["requirements"] = good
    entry_file.write_text(json.dumps(entry), encoding="utf-8")
    code = run(root, ["seed", MINI_ID, "5"], shared_cache, prepare=forbidden)
    _, err = capsys.readouterr()
    assert code == 4
    assert "domain-mismatch" in err
    entry = read_entry(root)
    assert entry["seed"] == 0
    assert entry["instance"] is None
    assert not (root / "scenarios" / MINI_ID / "work").exists()


def test_failing_imported_seed_keeps_previous_state(tmp_path, shared_cache, capsys):
    root = stage(tmp_path / "fws", FAILING_ID, FAILING_BYTES)
    code = run(root, ["seed", FAILING_ID, "3"], shared_cache)
    _, err = capsys.readouterr()
    assert code == 5
    assert "failure_record" in err
    assert "Traceback" not in err
    entry = read_entry(root, FAILING_ID)
    assert entry["seed"] == 0
    assert entry["instance"] is None
    assert not (root / "scenarios" / FAILING_ID / "work").exists()


def test_dispatch_wires_seed(tmp_path, monkeypatch):
    seen = {}

    def recorder(workspace, parsed):
        seen["seed"] = parsed.data_seed
        return 0

    monkeypatch.setattr(app, "run_seed", recorder)
    code = app.main(["--workspace", str(tmp_path / "ws"), "seed", MINI_ID, "9"])
    assert code == 0
    assert seen == {"seed": 9}
