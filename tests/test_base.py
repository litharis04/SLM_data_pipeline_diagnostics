"""Tests for ContractModel strict behaviour (SCENARIO_SPEC §3.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_pipeline_diagnostics.scenario.base import ContractModel


class Dummy(ContractModel):
    x: int
    y: str = "default"


def test_extra_forbidden():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Dummy(x=1, extra=1)  # type: ignore[call-arg]


def test_strict_no_coercion():
    with pytest.raises(ValidationError):
        Dummy(x="1", y="a")  # type: ignore[arg-type]


def test_frozen_immutable():
    d = Dummy(x=1)
    with pytest.raises(ValidationError):
        d.x = 2  # type: ignore[misc]


def test_validate_default():
    # defaults must be validated; y has default "default" which is valid,
    # but if default were invalid it should fail at class creation / instantiation
    # Here we test that a model with invalid default fails validation
    with pytest.raises(ValidationError):

        class BadDefault(ContractModel):
            x: int = "bad"  # type: ignore[assignment]

        BadDefault()


def test_allow_inf_nan_rejected():
    class FloatModel(ContractModel):
        v: float

    with pytest.raises(ValidationError, match="finite_number"):
        FloatModel(v=float("inf"))
    with pytest.raises(ValidationError, match="finite_number"):
        FloatModel(v=float("nan"))
    with pytest.raises(ValidationError, match="finite_number"):
        FloatModel(v=float("-inf"))


def test_config_values():
    cfg = ContractModel.model_config
    assert cfg["extra"] == "forbid"
    assert cfg["strict"] is True
    assert cfg["frozen"] is True
    assert cfg["validate_default"] is True
    assert cfg["allow_inf_nan"] is False
