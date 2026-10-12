from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import app, cmd_delete
from data_pipeline_diagnostics.cli.cmd_delete import run_delete
from data_pipeline_diagnostics.cli.workspace import init_workspace

REPO = Path(__file__).resolve().parents[2]
IDS = ["agriculture_coop_001", "agriculture_exports_001", "energy_meters_002"]


def bundle() -> list[tuple[str, bytes]]:
    return [(sid, (REPO / "scenarios" / f"{sid}.json").read_bytes()) for sid in IDS]


def stage(root: Path) -> Path:
    assert init_workspace(root, bundle()) is True
    target = root / "scenarios" / IDS[0] / "work"
    target.mkdir(parents=True)
    (target / "pipeline.duckdb").write_bytes(b"dummy")
    (root / "cache" / "v1" / "baseline").mkdir(parents=True)
    (root / "cache" / "v1" / "baseline" / "x.parquet").write_bytes(b"dummy")
    (root / "authoring" / "run-1").mkdir(parents=True)
    (root / "authoring" / "run-1" / "log.json").write_bytes(b"{}")
    (root / "config.json").write_text(
        json.dumps(
            {
                "profiles": {
                    "openrouter": {
                        "provider": "openrouter",
                        "model": "m",
                        "key_env": "K",
                        "max_output_tokens": 1,
                    }
                },
                "active_provider": "openrouter",
            }
        ),
        encoding="utf-8",
    )
    return root


def parse(argv: list[str]):
    return app.build_parser().parse_args(argv)


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_delete_removes_exact_triple(tmp_path, capsys):
    root = stage(tmp_path / "ws")
    before = snapshot(root)
    code = run_delete(root, parse(["delete", IDS[0]]))
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert IDS[0] in out
    assert not (root / "scenarios" / IDS[0]).exists()
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    assert catalog["scenario_ids"] == [IDS[1], IDS[2]]
    after = snapshot(root)
    removed = {str(Path("scenarios") / IDS[0] / name) for name in ("scenario.json", "entry.json")}
    removed.add(str(Path("scenarios") / IDS[0] / "work" / "pipeline.duckdb"))
    assert set(before) - set(after) == removed
    for path, content in before.items():
        if path not in removed and Path(path).parts[0] != "catalog.json":
            assert after[path] == content
    assert after["config.json"] == before["config.json"]


def test_unknown_id_exits_2(tmp_path, capsys):
    root = stage(tmp_path / "ws")
    before = snapshot(root)
    assert run_delete(root, parse(["delete", "nope_001"])) == 2
    _, err = capsys.readouterr()
    assert "unknown scenario" in err
    assert "Traceback" not in err
    assert snapshot(root) == before


@pytest.mark.parametrize("bad", ["../escape", "/abs/path", "a/b", "", "Bad"])
def test_path_escapes_rejected_before_touching_disk(tmp_path, capsys, bad):
    before = sorted(tmp_path.iterdir())
    root = tmp_path / "ws"
    assert run_delete(root, parse(["delete", bad])) == 2
    _, err = capsys.readouterr()
    assert "Traceback" not in err
    assert sorted(tmp_path.iterdir()) == before
    assert not (tmp_path / "escape").exists()


def test_crash_before_catalog_update_is_resumable(tmp_path, monkeypatch, capsys):
    root = stage(tmp_path / "ws")

    def boom(path, payload):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(cmd_delete, "atomic_write_json", boom)
    assert run_delete(root, parse(["delete", IDS[0]])) == 5
    _, err = capsys.readouterr()
    assert "Traceback" not in err
    assert not (root / "scenarios" / IDS[0]).exists()
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    assert IDS[0] in catalog["scenario_ids"]
    monkeypatch.undo()
    assert run_delete(root, parse(["delete", IDS[0]])) == 0
    capsys.readouterr()
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    assert IDS[0] not in catalog["scenario_ids"]
    assert not (root / "scenarios" / IDS[0]).exists()


def test_deleted_example_stays_absent_from_list(tmp_path, capsys):
    root = stage(tmp_path / "ws")
    assert run_delete(root, parse(["delete", IDS[0]])) == 0
    capsys.readouterr()
    assert app.main(["--workspace", str(root), "list"]) == 0
    out, _ = capsys.readouterr()
    assert IDS[0] not in out
    assert IDS[1] in out and IDS[2] in out
    assert app.main(["--workspace", str(root), "list"]) == 0
    out, _ = capsys.readouterr()
    assert IDS[0] not in out


def test_dispatch_wires_delete(tmp_path, monkeypatch):
    seen = {}

    def recorder(workspace, parsed):
        seen["id"] = parsed.scenario_id
        return 0

    monkeypatch.setattr(app, "run_delete", recorder)
    code = app.main(["--workspace", str(tmp_path / "ws"), "delete", IDS[0]])
    assert code == 0
    assert seen == {"id": IDS[0]}
