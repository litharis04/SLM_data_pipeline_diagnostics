from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import app, bundle, workspace
from data_pipeline_diagnostics.scenario import parse_scenario_json, scenario_content_hash

REPO_SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"
FAKE_IDS = ["agriculture_coop_001", "energy_meters_002"]


def make_bundle() -> list[tuple[str, bytes]]:
    return [(sid, (REPO_SCENARIOS / f"{sid}.json").read_bytes()) for sid in FAKE_IDS]


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def test_bootstrap_layout_and_minimal_entries(tmp_path):
    root = tmp_path / "ws"
    assert workspace.init_workspace(root, make_bundle()) is True

    assert read_json(root / "config.json") == {"profiles": {}, "active_provider": None}

    catalog = read_json(root / "catalog.json")
    assert set(catalog) == {"bootstrap_major_minor", "scenario_ids"}
    assert catalog["scenario_ids"] == sorted(FAKE_IDS)
    assert catalog["bootstrap_major_minor"] == workspace.package_major_minor()
    assert catalog["bootstrap_major_minor"].count(".") == 1

    for sid, raw in make_bundle():
        scenario_file = root / "scenarios" / sid / "scenario.json"
        entry_file = root / "scenarios" / sid / "entry.json"
        assert scenario_file.read_bytes() == raw
        entry = read_json(entry_file)
        assert set(entry) == {"origin", "seed", "scenario_hash", "instance"}
        assert entry["origin"] == "imported"
        assert entry["seed"] == 0
        assert entry["instance"] is None
        assert entry["scenario_hash"] == scenario_content_hash(parse_scenario_json(raw))

    for dirname in ("cache", "authoring"):
        assert (root / dirname).is_dir()
        assert list((root / dirname).iterdir()) == []
    assert {p.name for p in root.iterdir()} == {
        "config.json",
        "catalog.json",
        "scenarios",
        "cache",
        "authoring",
    }
    assert list(root.rglob("*.tmp-*")) == []


def test_bootstrap_once_no_reimport_no_overwrite(tmp_path):
    root = tmp_path / "ws"
    assert workspace.init_workspace(root, make_bundle()) is True
    catalog_before = (root / "catalog.json").read_bytes()

    shutil.rmtree(root / "scenarios" / FAKE_IDS[1])
    edited = root / "scenarios" / FAKE_IDS[0] / "scenario.json"
    edited.write_bytes(b'{"personal": "edit"}')
    personal = root / "scenarios" / "my_personal_001"
    personal.mkdir(parents=True)
    (personal / "scenario.json").write_bytes(b'{"personal": true}')
    (personal / "entry.json").write_bytes(b"{}")

    assert workspace.init_workspace(root, make_bundle()) is False
    assert not (root / "scenarios" / FAKE_IDS[1]).exists()
    assert edited.read_bytes() == b'{"personal": "edit"}'
    assert (personal / "scenario.json").read_bytes() == b'{"personal": true}'
    assert (personal / "entry.json").read_bytes() == b"{}"
    assert (root / "catalog.json").read_bytes() == catalog_before


def test_crash_between_tmp_write_and_rename_keeps_previous(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    assert workspace.init_workspace(root, make_bundle()) is True
    catalog_before = (root / "catalog.json").read_bytes()
    entry_file = root / "scenarios" / FAKE_IDS[0] / "entry.json"
    entry_before = entry_file.read_bytes()

    def boom(src, dst):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError, match="simulated crash"):
        workspace.atomic_write_json(root / "catalog.json", {"scenario_ids": []})
    with pytest.raises(RuntimeError, match="simulated crash"):
        workspace.atomic_write_json(entry_file, {"origin": "imported"})

    assert (root / "catalog.json").read_bytes() == catalog_before
    assert entry_file.read_bytes() == entry_before


def test_major_minor_only_and_patch_mismatch_ignored(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    monkeypatch.setattr(workspace, "_package_version", lambda: "9.8.7")
    assert workspace.init_workspace(root, make_bundle()) is True
    assert read_json(root / "catalog.json")["bootstrap_major_minor"] == "9.8"

    catalog_before = (root / "catalog.json").read_bytes()
    monkeypatch.setattr(workspace, "_package_version", lambda: "9.8.9")
    assert workspace.init_workspace(root, make_bundle()) is False
    assert workspace.ensure_workspace(root) is False
    assert (root / "catalog.json").read_bytes() == catalog_before


def test_ensure_workspace_uses_bundled_seam(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle, "iter_bundled_scenarios", make_bundle)
    root = tmp_path / "ws"
    assert workspace.ensure_workspace(root) is True
    assert read_json(root / "catalog.json")["scenario_ids"] == sorted(FAKE_IDS)
    assert workspace.ensure_workspace(root) is False


def test_bundle_seam_sorted_stable_and_readonly(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "b_002.json").write_bytes(b'{"b": 2}')
    (src / "a_001.json").write_bytes(b'{"a": 1}')
    before = sorted(p.name for p in src.iterdir())

    monkeypatch.setattr(bundle, "_repository_scenarios_dir", lambda: src)

    def no_package_data(name: str):
        raise FileNotFoundError("no installed bundle yet")

    monkeypatch.setattr(bundle, "files", no_package_data)
    assert bundle.iter_bundled_scenarios() == [("a_001", b'{"a": 1}'), ("b_002", b'{"b": 2}')]
    assert sorted(p.name for p in src.iterdir()) == before


def test_bundle_seam_over_real_source_is_sorted_and_valid():
    pairs = bundle.iter_bundled_scenarios()
    assert len(pairs) > 2
    ids = [sid for sid, _ in pairs]
    assert ids == sorted(ids)
    for sid, raw in pairs:
        assert workspace.validate_scenario_id(sid) == sid
        assert len(raw) > 0


@pytest.mark.parametrize("bad", ["Bad", "a-b", "", "a" * 101, "../evil", "a/b", 123, None])
def test_validate_scenario_id_rejects(bad):
    with pytest.raises(ValueError, match="invalid SCENARIO_ID"):
        workspace.validate_scenario_id(bad)


def test_validate_scenario_id_accepts():
    assert workspace.validate_scenario_id("abc_123") == "abc_123"


@pytest.mark.parametrize("mutate", ["bad_id", "mismatch", "corrupt", "duplicate"])
def test_invalid_bundle_rejected_before_any_writes(tmp_path, mutate):
    sid, raw = make_bundle()[0]
    if mutate == "bad_id":
        items = [("Bad!", raw)]
    elif mutate == "mismatch":
        items = [("zzz_other_001", raw)]
    elif mutate == "corrupt":
        items = [(sid, b"not json")]
    else:
        items = [(sid, raw), (sid, raw)]
    root = tmp_path / "ws"
    with pytest.raises(ValueError):
        workspace.init_workspace(root, items)
    assert not (root / "catalog.json").exists()
    assert not (root / "scenarios").exists()


def test_help_and_version_create_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        app.main(["--help"])
    assert exc.value.code == 0
    with pytest.raises(SystemExit) as exc:
        app.main(["--version"])
    assert exc.value.code == 0
    capsys.readouterr()
    assert list(tmp_path.iterdir()) == []
