"""G19 tests: content-addressed cache + immutable handoff (§§7, 19).

All scenarios run on the tiny ``minimal.json`` (fast hermetic builds).
Builds are counted by wrapping ``cache.build_clean_instance``; each test
uses a fresh cache root so counters stay local. Handoff isolation is proven
by mutating a handed-off copy and re-preparing (still a verified hit).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import data_pipeline_diagnostics.generator.records as records_module
from data_pipeline_diagnostics.generator import cache
from data_pipeline_diagnostics.generator.cache import (
    prepare_clean_instance,
    verify_cache_entry,
)
from data_pipeline_diagnostics.generator.records import _python_version_key
from data_pipeline_diagnostics.scenario.parsing import parse_scenario_json
from data_pipeline_diagnostics.scenario.semantic import validate_semantics

REPO = Path(__file__).resolve().parents[2]
MINIMAL = REPO / "tests" / "scenario" / "fixtures" / "valid" / "minimal.json"


def _validated():
    return validate_semantics(parse_scenario_json(MINIMAL.read_bytes()))


def _validated_with_description(note: str):
    data = json.loads(MINIMAL.read_bytes())
    data["description"] = note
    return validate_semantics(parse_scenario_json(json.dumps(data)))


class _BuildCounter:
    def __init__(self, monkeypatch):
        self.calls = 0
        original = cache.build_clean_instance

        def counting(*, validated, data_seed, instance_dir, write_success=True):
            self.calls += 1
            return original(
                validated=validated,
                data_seed=data_seed,
                instance_dir=instance_dir,
                write_success=write_success,
            )

        monkeypatch.setattr(cache, "build_clean_instance", counting)


def test_repeat_preparation_returns_verified_hit(tmp_path):
    validated = _validated()
    first = prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert not first.cache_hit
    assert (first.instance_dir / "SUCCESS").is_file()
    second = prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert second.cache_hit
    assert second.instance_digest == first.instance_digest
    assert second.instance_dir != first.instance_dir
    entry = tmp_path / "cache" / "v1" / first.instance_digest
    assert verify_cache_entry(entry, first.instance_digest) is not None


def test_build_counter_counts_one_build(monkeypatch, tmp_path):
    counter = _BuildCounter(monkeypatch)
    validated = _validated()
    prepare_clean_instance(validated, 11, tmp_path / "cache")
    prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert counter.calls == 1


def test_pinned_version_change_misses(monkeypatch, tmp_path):
    validated = _validated()
    before = prepare_clean_instance(validated, 11, tmp_path / "cache")
    monkeypatch.setattr(records_module, "DBT_RENDERER_VERSION", "9.9.9")
    after = prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert not after.cache_hit
    assert after.instance_digest != before.instance_digest


def test_seed_and_content_change_miss(tmp_path):
    validated = _validated()
    base = prepare_clean_instance(validated, 11, tmp_path / "cache")
    reseeded = prepare_clean_instance(validated, 12, tmp_path / "cache")
    assert not reseeded.cache_hit
    assert reseeded.instance_digest != base.instance_digest
    edited = prepare_clean_instance(
        _validated_with_description("cache-miss probe"), 11, tmp_path / "cache"
    )
    assert not edited.cache_hit
    assert edited.instance_digest != base.instance_digest


def test_python_patch_change_does_not_miss():
    from types import SimpleNamespace

    patch_a = SimpleNamespace(major=3, minor=14, micro=7)
    patch_b = SimpleNamespace(major=3, minor=14, micro=9)
    assert _python_version_key(patch_a) == _python_version_key(patch_b) == "3.14"
    identity = records_module.identity_object(_validated(), 11)
    assert identity["python"] == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert identity["python"].count(".") == 1


def test_mutating_copy_leaves_baseline_untouched(monkeypatch, tmp_path):
    counter = _BuildCounter(monkeypatch)
    validated = _validated()
    first = prepare_clean_instance(validated, 11, tmp_path / "cache")
    (first.instance_dir / "junk.txt").write_text("fault-injection scratch\n", encoding="utf-8")
    (first.instance_dir / "dbt" / "models" / "sources.yml").write_text(
        "tampered\n", encoding="utf-8"
    )
    entry = tmp_path / "cache" / "v1" / first.instance_digest
    assert verify_cache_entry(entry, first.instance_digest) is not None
    again = prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert again.cache_hit
    assert counter.calls == 1
    assert not (again.instance_dir / "junk.txt").exists()


def test_plain_directory_layout_no_service(tmp_path):
    validated = _validated()
    inst = prepare_clean_instance(validated, 11, tmp_path / "cache")
    assert (tmp_path / "cache" / "v1" / inst.instance_digest).is_dir()
    leftovers = [
        p.name
        for p in (tmp_path / "cache").rglob("*")
        if p.suffix in {".db", ".sqlite", ".sqlite3"} or p.name.endswith(".lock")
    ]
    assert leftovers == []
    digest = hashlib.sha256(b"probe").hexdigest()
    assert verify_cache_entry(tmp_path / "cache" / "v1" / digest, digest) is None
