"""C13: wheel/sdist resources and clean-environment install proof."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE_DATA = REPO / "src" / "data_pipeline_diagnostics"
BUNDLED = PACKAGE_DATA / "bundled_scenarios"
SPEC_COPY = PACKAGE_DATA / "SCENARIO_SPEC.md"
REPO_SPEC = REPO / "docs" / "SCENARIO_SPEC.md"
REPO_SCENARIOS = REPO / "scenarios"

UV = shutil.which("uv")
TINY_ID = "agriculture_coop_001"


def repo_scenario_ids() -> list[str]:
    return sorted(path.stem for path in REPO_SCENARIOS.glob("*.json"))


def test_packaged_data_matches_repo_corpus():
    assert SPEC_COPY.read_bytes() == REPO_SPEC.read_bytes()
    assert sorted(path.name for path in BUNDLED.glob("*.json")) == [
        f"{stem}.json" for stem in repo_scenario_ids()
    ]
    for stem in repo_scenario_ids():
        assert (BUNDLED / f"{stem}.json").read_bytes() == (
            REPO_SCENARIOS / f"{stem}.json"
        ).read_bytes()


def test_no_dev_path_references_in_cli():
    cli_dir = REPO / "src" / "data_pipeline_diagnostics" / "cli"
    for path in sorted(cli_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "tasks/" not in text, path.name
        assert "artifacts/" not in text, path.name


def build_distributions(out_dir: Path) -> tuple[Path, Path]:
    assert UV is not None, "uv is required packaging tooling"
    completed = subprocess.run(
        [UV, "build", "--out-dir", str(out_dir)],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    wheels = sorted(out_dir.glob("*.whl"))
    sdists = sorted(out_dir.glob("*.tar.gz"))
    assert len(wheels) == 1 and len(sdists) == 1
    return wheels[0], sdists[0]


def test_distributions_carry_resources(tmp_path):
    wheel, sdist = build_distributions(tmp_path / "dist")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "data_pipeline_diagnostics/SCENARIO_SPEC.md" in names
        bundled = [n for n in names if n.startswith("data_pipeline_diagnostics/bundled_scenarios/")]
        assert sorted(n.rsplit("/", 1)[1] for n in bundled if n.endswith(".json")) == [
            f"{stem}.json" for stem in repo_scenario_ids()
        ]
        entry_points = [n for n in names if n.endswith("entry_points.txt")]
        assert len(entry_points) == 1
        assert (
            "plgen = data_pipeline_diagnostics.cli.app:main"
            in archive.read(entry_points[0]).decode()
        )
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
        assert sum(1 for n in names if n.endswith("/SCENARIO_SPEC.md")) == 1
        assert sum(1 for n in names if "bundled_scenarios" in n and n.endswith(".json")) == len(
            repo_scenario_ids()
        )


def make_venv(path: Path) -> Path:
    assert UV is not None, "uv is required packaging tooling"
    completed = subprocess.run(
        [UV, "venv", "--python", "3.14", str(path)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    return path / "bin" / "python"


def install(venv_python: Path, package: Path) -> None:
    completed = subprocess.run(
        [UV, "pip", "install", "--python", str(venv_python), str(package)],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]


def clean_env(extra_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("OPENROUTER_API_KEY", None)
    env.pop("GEMINI_API_KEY", None)
    env["PATH"] = str(extra_path) + os.pathsep + env.get("PATH", "")
    return env


def schema_hash_expression() -> str:
    return (
        "import hashlib, json; "
        "from data_pipeline_diagnostics.scenario import get_scenario_json_schema; "
        "print(hashlib.sha256(json.dumps(get_scenario_json_schema(), sort_keys=True).encode()).hexdigest())"
    )


def test_clean_install_runs_plgen_outside_checkout(tmp_path):
    wheel, _ = build_distributions(tmp_path / "dist")
    venv_python = make_venv(tmp_path / "clean-venv")
    install(venv_python, wheel)
    venv_bin = venv_python.parent
    assert (venv_bin / "plgen").is_file()

    work = tmp_path / "work"
    work.mkdir()
    env = clean_env(venv_bin)

    version = subprocess.run(
        [str(venv_bin / "plgen"), "--version"],
        capture_output=True,
        text=True,
        cwd=str(work),
        env=env,
        timeout=120,
    )
    assert version.returncode == 0
    assert "plgen 0.1.0" in version.stdout

    listing = subprocess.run(
        [str(venv_bin / "plgen"), "list"],
        capture_output=True,
        text=True,
        cwd=str(work),
        env=env,
        timeout=300,
    )
    assert listing.returncode == 0, listing.stderr
    assert TINY_ID in listing.stdout
    assert (work / "pipeline_workspace" / "catalog.json").is_file()

    opening = subprocess.run(
        [str(venv_bin / "plgen"), "open", TINY_ID],
        capture_output=True,
        text=True,
        cwd=str(work),
        env=env,
        timeout=600,
    )
    assert opening.returncode == 0, opening.stderr[-4000:]
    assert f"scenario: {TINY_ID}" in opening.stdout
    assert "state: prepared" in opening.stdout

    bundle_path = subprocess.run(
        [
            str(venv_python),
            "-c",
            "from importlib.resources import files; "
            "print(files('data_pipeline_diagnostics') / 'bundled_scenarios')",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert bundle_path.returncode == 0
    assert "site-packages" in bundle_path.stdout
    assert str(REPO) not in bundle_path.stdout

    installed_hash = subprocess.run(
        [str(venv_python), "-c", schema_hash_expression()],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert installed_hash.returncode == 0
    from data_pipeline_diagnostics.scenario import get_scenario_json_schema

    expected = hashlib.sha256(
        json.dumps(get_scenario_json_schema(), sort_keys=True).encode()
    ).hexdigest()
    assert installed_hash.stdout.strip() == expected

    outside_checkout = str(REPO) + os.sep
    for path in sorted((work / "pipeline_workspace").rglob("*")):
        if path.is_file() and path.stat().st_size < 1_000_000:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError, OSError:
                continue
            assert outside_checkout not in text, path


def test_sdist_install_resolves_identical_resources(tmp_path):
    _, sdist = build_distributions(tmp_path / "dist")
    venv_python = make_venv(tmp_path / "sdist-venv")
    install(venv_python, sdist)
    env = clean_env(venv_python.parent)
    bundle_path = subprocess.run(
        [
            str(venv_python),
            "-c",
            "from importlib.resources import files; "
            "print(files('data_pipeline_diagnostics') / 'bundled_scenarios')",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert bundle_path.returncode == 0
    assert "site-packages" in bundle_path.stdout
    installed_hash = subprocess.run(
        [str(venv_python), "-c", schema_hash_expression()],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert installed_hash.returncode == 0
    from data_pipeline_diagnostics.scenario import get_scenario_json_schema

    expected = hashlib.sha256(
        json.dumps(get_scenario_json_schema(), sort_keys=True).encode()
    ).hexdigest()
    assert installed_hash.stdout.strip() == expected
    spec_check = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import hashlib; from importlib.resources import files; "
            "print(hashlib.sha256((files('data_pipeline_diagnostics') / 'SCENARIO_SPEC.md').read_bytes()).hexdigest())",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert spec_check.returncode == 0
    assert spec_check.stdout.strip() == hashlib.sha256(REPO_SPEC.read_bytes()).hexdigest()
