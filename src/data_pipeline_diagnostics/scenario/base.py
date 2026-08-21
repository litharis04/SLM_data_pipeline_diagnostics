"""Base contract model for the scenario language.

Every contract model MUST inherit from :class:`ContractModel` with strict,
closed and immutable behaviour as defined in ``docs/SCENARIO_SPEC.md`` §3.2.
"""

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Common Pydantic base for all scenario contract models."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )
