from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import app, bundle, cmd_list, workspace
from data_pipeline_diagnostics.scenario import parse_scenario_json

REPO_SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
FAKE_IDS = ["energy_meters_002", "agriculture_exports_001", "agriculture_coop_001"]


def make_bundle() -> list[tuple[str, bytes]]:
    return [(sid, (REPO_SCENARIOS / f"{sid}.json").read_bytes()) for sid in FAKE_IDS]


def stage(root: Path) -> Path:
    assert workspace.init_workspace(root, make_bundle()) is True
    return root


def run_list(root: Path, capsys) -> tuple[int, str, str]:
    code = app.main(["--workspace", str(root), "list"])
    out, err = capsys.readouterr()
    return code, out, err


def parse_rows(out: str) -> list[list[str]]:
    lines = out.strip().splitlines()
    assert lines[0] == cmd_list.HEADER
    return [line.split(maxsplit=5) for line in lines[1:]]


def test_unprepared_entries_list_seed_0_unprepared_no_size(tmp_path, capsys):
    root = stage(tmp_path / "ws")
    code, out, err = run_list(root, capsys)
    assert code == 0
    assert err == ""
    rows = parse_rows(out)
    assert [row[0] for row in rows] == sorted(FAKE_IDS)
    for sid, domain, seed, state, size, description in rows:
        raw = (REPO_SCENARIOS / f"{sid}.json").read_bytes()
        scenario = parse_scenario_json(raw)
        assert domain == scenario.domain
        assert description == scenario.description
        assert seed == "0"
        assert state == "unprepared"
        assert size == "-"


def test_prepared_entries_show_observed_size(tmp_path, capsys):
    root = stage(tmp_path / "ws")
    cases = {FAKE_IDS[0]: [120, 500], FAKE_IDS[1]: [5_000, 12]}
    for sid, counts in cases.items():
        inst = tmp_path / f"inst_{sid}"
        inst.mkdir()
        tables = [{"name": f"t{i}", "row_count": count} for i, count in enumerate(counts)]
        (inst / "instance_record.json").write_text(
            json.dumps({"raw_tables": tables}), encoding="utf-8"
        )
        entry_file = root / "scenarios" / sid / "entry.json"
        entry = json.loads(entry_file.read_text(encoding="utf-8"))
        entry["instance"] = workspace.instance_pointer(inst.resolve(), "0" * 64)
        entry_file.write_text(json.dumps(entry), encoding="utf-8")

    code, out, _ = run_list(root, capsys)
    assert code == 0
    by_id = {row[0]: row for row in parse_rows(out)}
    assert by_id[FAKE_IDS[0]][4] == "small"
    assert by_id[FAKE_IDS[0]][3] == "prepared"
    assert by_id[FAKE_IDS[1]][4] == "medium"
    assert by_id[FAKE_IDS[2]][3] == "unprepared"
    assert by_id[FAKE_IDS[2]][4] == "-"


@pytest.mark.parametrize(
    ("max_rows", "category"),
    [
        (0, "small"),
        (1, "small"),
        (1_000, "small"),
        (1_001, "medium"),
        (10_000, "medium"),
        (10_001, "large"),
        (100_000, "large"),
        (100_001, "outside-presets"),
    ],
)
def test_classify_raw_size_boundaries(max_rows, category):
    assert cmd_list.classify_raw_size(max_rows) == category


def test_order_deterministic_across_locales(tmp_path, monkeypatch, capsys):
    root = stage(tmp_path / "ws")
    outputs = []
    for locale in ("C", "en_US.UTF-8"):
        monkeypatch.setenv("LC_ALL", locale)
        code, out, _ = run_list(root, capsys)
        assert code == 0
        outputs.append(out)
    assert outputs[0] == outputs[1]
    assert [row[0] for row in parse_rows(outputs[0])] == sorted(FAKE_IDS)


def test_corrupt_scenario_json_lists_invalid_marker(tmp_path, capsys):
    root = stage(tmp_path / "ws")
    (root / "scenarios" / FAKE_IDS[0] / "scenario.json").write_bytes(b"{oops")
    code, out, _ = run_list(root, capsys)
    assert code == 0
    by_id = {row[0]: row for row in parse_rows(out)}
    assert by_id[FAKE_IDS[0]][1] == "invalid"
    assert by_id[FAKE_IDS[0]][5] == "invalid"
    assert by_id[FAKE_IDS[0]][2] == "0"
    assert by_id[FAKE_IDS[0]][3] == "unprepared"
    assert by_id[FAKE_IDS[1]][1] != "invalid"
    assert by_id[FAKE_IDS[2]][1] != "invalid"


def test_missing_scenario_dir_lists_invalid_row(tmp_path, capsys):
    root = stage(tmp_path / "ws")
    shutil.rmtree(root / "scenarios" / FAKE_IDS[0])
    code, out, _ = run_list(root, capsys)
    assert code == 0
    by_id = {row[0]: row for row in parse_rows(out)}
    assert set(by_id) == set(FAKE_IDS)
    assert by_id[FAKE_IDS[0]][1] == "invalid"
    assert by_id[FAKE_IDS[0]][2] == "invalid"
    assert by_id[FAKE_IDS[0]][3] == "invalid"
    assert by_id[FAKE_IDS[0]][4] == "-"
    assert by_id[FAKE_IDS[0]][5] == "invalid"


def test_dangling_instance_pointer_shows_unknown(tmp_path, capsys):
    root = stage(tmp_path / "ws")
    entry_file = root / "scenarios" / FAKE_IDS[0] / "entry.json"
    entry = json.loads(entry_file.read_text(encoding="utf-8"))
    entry["instance"] = workspace.instance_pointer(tmp_path / "absent", "0" * 64)
    entry_file.write_text(json.dumps(entry), encoding="utf-8")
    code, out, _ = run_list(root, capsys)
    assert code == 0
    by_id = {row[0]: row for row in parse_rows(out)}
    assert by_id[FAKE_IDS[0]][3] == "prepared"
    assert by_id[FAKE_IDS[0]][4] == "unknown"


def test_first_list_bootstraps_through_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bundle, "iter_bundled_scenarios", make_bundle)
    root = tmp_path / "ws"
    assert not root.exists()
    code = app.main(["--workspace", str(root), "list"])
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert (root / "catalog.json").is_file()
    assert [row[0] for row in parse_rows(out)] == sorted(FAKE_IDS)


def test_repeat_list_changes_no_files(tmp_path, capsys):
    root = stage(tmp_path / "ws")

    def snapshot() -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    before = snapshot()
    for _ in range(2):
        code, _, err = run_list(root, capsys)
        assert code == 0
        assert err == ""
    assert snapshot() == before


def test_corrupt_catalog_exits_5(tmp_path, capsys):
    root = stage(tmp_path / "ws")
    (root / "catalog.json").write_bytes(b"{oops")
    code, _, err = run_list(root, capsys)
    assert code == 5
    assert err.strip() != ""
    assert "Traceback" not in err


def test_instance_pointer_requires_absolute_path(tmp_path):
    pointer = workspace.instance_pointer(tmp_path.resolve(), "0" * 64)
    assert pointer == {"path": str(tmp_path.resolve()), "digest": "0" * 64}
    with pytest.raises(ValueError):
        workspace.instance_pointer(Path("relative"), "0" * 64)
    with pytest.raises(ValueError):
        workspace.instance_pointer(tmp_path.resolve(), "")
