from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_pipeline_diagnostics.cli import app
from data_pipeline_diagnostics.cli.cmd_connect import (
    ProviderProfile,
    child_environ,
    configured_credential_envs,
    run_connect,
)
from data_pipeline_diagnostics.cli.providers import FakeTransport
from data_pipeline_diagnostics.cli.workspace import init_workspace

OR_KEY = "sk-or-v1-TESTKEY007"
GEM_KEY = "AIzaTESTKEY007"
OR_ENV = "OPENROUTER_API_KEY"
GEM_ENV = "GEMINI_API_KEY"


def stage(root: Path) -> Path:
    assert init_workspace(root, []) is True
    return root


def parse(argv: list[str]):
    return app.build_parser().parse_args(argv)


def read_config(root: Path) -> dict:
    return json.loads((root / "config.json").read_text(encoding="utf-8"))


def or_key_script(models=("m1", "m2")):
    return [
        {"data": {"label": "test", "limit": 100}},
        {"data": [{"id": mid} for mid in models]},
    ]


def test_openrouter_success_persists_profile(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    monkeypatch.delenv(GEM_ENV, raising=False)
    root = stage(tmp_path / "ws")
    fake = FakeTransport(or_key_script())
    code = run_connect(
        root, parse(["connect", "--provider", "openrouter", "--model", "m2"]), sender=fake
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert out.strip() == f"connected openrouter model=m2 key_env={OR_ENV}"
    assert OR_KEY not in out
    config = read_config(root)
    assert config["profiles"]["openrouter"] == {
        "provider": "openrouter",
        "model": "m2",
        "key_env": OR_ENV,
        "max_output_tokens": 32_768,
    }
    assert config["active_provider"] == "openrouter"
    assert len(fake.calls) == 2
    assert all(call["body"] is None for call in fake.calls)
    assert fake.calls[0]["url"].endswith("/key")
    assert fake.calls[1]["url"].endswith("/models")
    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {OR_KEY}"
    assert all(OR_KEY not in call["url"] for call in fake.calls)


def test_gemini_success_custom_key_env_and_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CUSTOM_GEM", GEM_KEY)
    root = stage(tmp_path / "ws")
    fake = FakeTransport([{"name": "models/g1"}])
    code = run_connect(
        root,
        parse(
            [
                "connect",
                "--provider",
                "gemini",
                "--model",
                "g1",
                "--key-env",
                "CUSTOM_GEM",
                "--max-output-tokens",
                "1024",
            ]
        ),
        sender=fake,
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert "gemini" in out and "g1" in out and "CUSTOM_GEM" in out
    assert GEM_KEY not in out
    config = read_config(root)
    assert config["profiles"]["gemini"] == {
        "provider": "gemini",
        "model": "g1",
        "key_env": "CUSTOM_GEM",
        "max_output_tokens": 1024,
    }
    assert config["active_provider"] == "gemini"
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"].endswith("/models/g1")
    assert "?" not in fake.calls[0]["url"]
    assert fake.calls[0]["headers"]["x-goog-api-key"] == GEM_KEY


def test_profiles_coexist_update_in_place(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    monkeypatch.setenv(GEM_ENV, GEM_KEY)
    root = stage(tmp_path / "ws")
    capsys.readouterr()
    assert (
        run_connect(
            root,
            parse(["connect", "--provider", "gemini", "--model", "g1"]),
            sender=FakeTransport([{"name": "x"}]),
        )
        == 0
    )
    capsys.readouterr()
    assert (
        run_connect(
            root,
            parse(["connect", "--provider", "openrouter", "--model", "m1"]),
            sender=FakeTransport(or_key_script()),
        )
        == 0
    )
    capsys.readouterr()
    mid = read_config(root)
    assert set(mid["profiles"]) == {"gemini", "openrouter"}
    assert mid["active_provider"] == "openrouter"
    assert (
        run_connect(
            root,
            parse(["connect", "--provider", "openrouter", "--model", "m2"]),
            sender=FakeTransport(or_key_script()),
        )
        == 0
    )
    capsys.readouterr()
    after = read_config(root)
    assert after["profiles"]["gemini"] == mid["profiles"]["gemini"]
    assert after["profiles"]["openrouter"]["model"] == "m2"
    assert after["profiles"]["openrouter"]["key_env"] == OR_ENV
    assert after["profiles"]["openrouter"]["max_output_tokens"] == 32_768
    assert after["active_provider"] == "openrouter"


def test_free_router_skips_catalog(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    fake = FakeTransport([{"data": {"label": "t"}}])
    code = run_connect(
        root,
        parse(["connect", "--provider", "openrouter", "--model", "openrouter/free"]),
        sender=fake,
    )
    capsys.readouterr()
    assert code == 0
    assert len(fake.calls) == 1
    assert read_config(root)["profiles"]["openrouter"]["model"] == "openrouter/free"


@pytest.mark.parametrize(
    "script",
    [
        [("http", 401, {"error": {"code": "invalid_key", "message": "bad"}}, {})],
        [("http", 429, {"error": {"code": 429, "message": "slow down"}}, {"Retry-After": "5"})],
        [("timeout",)],
        [("network", "connection refused")],
    ],
)
def test_provider_failure_persists_nothing(tmp_path, monkeypatch, capsys, script):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    before = (root / "config.json").read_bytes()
    fake = FakeTransport(script)
    code = run_connect(
        root, parse(["connect", "--provider", "openrouter", "--model", "m1"]), sender=fake
    )
    out, err = capsys.readouterr()
    assert code == 3
    assert out == ""
    assert err.strip() != "" and "Traceback" not in err
    assert OR_KEY not in err and OR_KEY not in out
    assert (root / "config.json").read_bytes() == before


def test_unknown_model_persists_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    monkeypatch.setenv(GEM_ENV, GEM_KEY)
    root = stage(tmp_path / "ws")
    before = (root / "config.json").read_bytes()
    fake = FakeTransport(or_key_script(models=("m1",)))
    code = run_connect(
        root, parse(["connect", "--provider", "openrouter", "--model", "nope"]), sender=fake
    )
    _, err = capsys.readouterr()
    assert code == 3
    assert "unknown model" in err
    assert (root / "config.json").read_bytes() == before
    fake404 = FakeTransport([("http", 404, {"error": {"message": "not found"}}, {})])
    code = run_connect(
        root, parse(["connect", "--provider", "gemini", "--model", "nope"]), sender=fake404
    )
    _, err = capsys.readouterr()
    assert code == 3
    assert "unknown model" in err
    assert (root / "config.json").read_bytes() == before


@pytest.mark.parametrize("setup", ["missing", "empty", "custom-missing"])
def test_missing_key_fails_before_any_request(tmp_path, monkeypatch, capsys, setup):
    monkeypatch.delenv(OR_ENV, raising=False)
    root = stage(tmp_path / "ws")
    if setup == "empty":
        monkeypatch.setenv(OR_ENV, "   ")
        argv = ["connect", "--provider", "openrouter", "--model", "m1"]
    elif setup == "custom-missing":
        argv = ["connect", "--provider", "openrouter", "--model", "m1", "--key-env", "NOPE_VAR"]
    else:
        argv = ["connect", "--provider", "openrouter", "--model", "m1"]
    fake = FakeTransport(or_key_script())
    code = run_connect(root, parse(argv), sender=fake)
    _, err = capsys.readouterr()
    assert code == 3
    assert ("OPENROUTER_API_KEY" if setup != "custom-missing" else "NOPE_VAR") in err
    assert fake.calls == []


@pytest.mark.parametrize("flag", ["0", "-5", "abc"])
def test_bad_budget_exits_2(flag):
    with pytest.raises(SystemExit) as exc:
        parse(["connect", "--provider", "openrouter", "--model", "m1", "--max-output-tokens", flag])
    assert exc.value.code == 2


def test_unknown_provider_and_bad_inputs(tmp_path, monkeypatch, capsys):
    root = stage(tmp_path / "ws")
    with pytest.raises(SystemExit) as exc:
        app.main(["--workspace", str(root), "connect", "--provider", "bogus", "--model", "m"])
    assert exc.value.code == 2
    monkeypatch.setenv(OR_ENV, OR_KEY)
    assert run_connect(root, parse(["connect", "--provider", "openrouter", "--model", "   "])) == 2
    capsys.readouterr()
    assert (
        run_connect(
            root,
            parse(
                ["connect", "--provider", "openrouter", "--model", "m1", "--key-env", "not a var"]
            ),
        )
        == 2
    )
    capsys.readouterr()


def test_key_absent_from_files_on_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    body = {"error": {"code": "bad", "message": f"key {OR_KEY} rejected"}}
    fake = FakeTransport([("http", 401, body, {})])
    code = run_connect(
        root, parse(["connect", "--provider", "openrouter", "--model", "m1"]), sender=fake
    )
    out, err = capsys.readouterr()
    assert code == 3
    blob = (root / "config.json").read_bytes().decode()
    assert OR_KEY not in blob and OR_KEY not in out and OR_KEY not in err


def test_corrupt_config_exits_5(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(OR_ENV, OR_KEY)
    root = stage(tmp_path / "ws")
    (root / "config.json").write_bytes(b"{oops")
    fake = FakeTransport(or_key_script())
    code = run_connect(
        root, parse(["connect", "--provider", "openrouter", "--model", "m1"]), sender=fake
    )
    _, err = capsys.readouterr()
    assert code == 5
    assert "Traceback" not in err
    assert fake.calls == []


def test_child_environ_excludes_configured_credentials():
    config = {
        "profiles": {
            "openrouter": {
                "provider": "openrouter",
                "model": "m",
                "key_env": OR_ENV,
                "max_output_tokens": 1,
            },
            "gemini": {
                "provider": "gemini",
                "model": "g",
                "key_env": "CUSTOM_GEM",
                "max_output_tokens": 1,
            },
        },
        "active_provider": "openrouter",
    }
    assert configured_credential_envs(config) == {OR_ENV, "CUSTOM_GEM"}
    base = {OR_ENV: "a", "CUSTOM_GEM": "b", "PATH": "/bin", "OTHER": "x"}
    scrubbed = child_environ(config, base)
    assert scrubbed == {"PATH": "/bin", "OTHER": "x"}
    assert base[OR_ENV] == "a"


def test_profile_round_trip_validation():
    profile = ProviderProfile(provider="gemini", model="g", key_env="K", max_output_tokens=5)
    assert ProviderProfile.from_dict(profile.to_dict()) == profile
    with pytest.raises(ValueError):
        ProviderProfile.from_dict({**profile.to_dict(), "max_output_tokens": 0})
    with pytest.raises(ValueError):
        ProviderProfile.from_dict({**profile.to_dict(), "provider": "bogus"})


def test_dispatch_wires_connect(tmp_path, monkeypatch):
    seen = {}

    def recorder(workspace, parsed):
        seen["workspace"] = workspace
        seen["provider"] = parsed.provider
        return 0

    monkeypatch.setattr(app, "run_connect", recorder)
    code = app.main(
        ["--workspace", str(tmp_path / "ws"), "connect", "--provider", "openrouter", "--model", "m"]
    )
    assert code == 0
    assert seen == {"workspace": (tmp_path / "ws").resolve(), "provider": "openrouter"}
