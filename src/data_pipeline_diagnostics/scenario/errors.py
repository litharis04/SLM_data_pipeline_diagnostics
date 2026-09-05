"""Stable error types for semantic validation (SCENARIO_SPEC §17.1)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SemanticIssue:
    """Structured semantic issue."""

    code: str
    path: str
    message: str
    related: str | None = None


class SemanticValidationError(Exception):
    """Raised when semantic validation fails."""

    def __init__(self, issues: list[SemanticIssue]) -> None:
        # Deterministic ordering by path and code
        self.issues: list[SemanticIssue] = sorted(issues, key=lambda i: (i.path, i.code))
        super().__init__(f"semantic validation failed with {len(self.issues)} issue(s)")

    def __str__(self) -> str:
        return "\n".join(
            f"{i.code} at {i.path}: {i.message}" + (f" (related: {i.related})" if i.related else "")
            for i in self.issues
        )


# Stable error codes – keep ordering deterministic
class ErrorCode:
    UNIQUE_RAW_TABLE = "E100"
    UNIQUE_MODEL = "E101"
    RAW_MODEL_COLLISION = "E102"
    UNIQUE_RELATIONSHIP = "E103"
    UNIQUE_ASSERTION = "E104"
    MISSING_REF = "E105"
    INVALID_PK = "E106"
    GENERATOR_TYPE_MISMATCH = "E107"
    CATEGORICAL_HOMOGENEOUS = "E108"
    TEMPLATE_PLACEHOLDER = "E109"
    TEMPLATE_CYCLE = "E110"
    FOREIGN_KEY_SIDE = "E111"
    FAKER_TYPE = "E112"
    RELATIONSHIP_ARITY = "E113"
    RELATIONSHIP_TYPE = "E114"
    UNIQUE_SIDE = "E115"
    ONE_TO_ONE_UNIQUE = "E116"
    BRIDGE_TABLE = "E117"
    STAGING_1TO1 = "E118"
    STAGING_SOURCE_COLUMN = "E119"
    STAGING_OPERATION_CHAIN = "E120"
    STAGING_GRAIN = "E121"
    DAG_CYCLE = "E122"
    LAYER_DEPENDENCY = "E123"
    JOIN_KEY_MISMATCH = "E124"
    GRAIN_IMPOSSIBLE = "E125"
    METRIC_TYPE = "E126"
    DISCONNECTED = "E127"
    CONTRADICTORY_ASSERTION = "E128"
    INVALID_EXPRESSION_TYPE = "E129"
    INVALID_CONDITION_TYPE = "E130"
    UNSUPPORTED_JOIN_LINEAGE = "E131"
    CONTRADICTORY_JOIN_MAPPING = "E132"
    IMPOSSIBLE_JOIN_GRAIN = "E133"
    NON_DETERMINISTIC_DEDUP = "E134"
    # generic
    UNKNOWN = "E999"


class ScenarioParseError(Exception):
    """Project-level parse error wrapping Pydantic ValidationError (§18.1)."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "P001",
        path: str = "",
        original: Exception | None = None,
    ) -> None:
        self.code = code
        self.path = path
        self.message = message
        self.original = original
        super().__init__(f"{code} at {path}: {message}" if path else f"{code}: {message}")
