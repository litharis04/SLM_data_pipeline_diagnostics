"""Null insertion and hard raw constraints (GENERATOR_SPEC §§11.1–11.2).

Proposals come from :mod:`raw_values` (G03–G05); this module applies nulls
and enforces single-column hard constraints:

- scalar nullable column: one Bernoulli draw per row from its
  ``nulls/<table>/<column>`` stream (``0.0`` never, ``1.0`` always null —
  both fall out of ``rng.random() < p`` with no special-casing);
  ``nullable: false`` and PK members never consult the null stream;
- composite FK nulls: one tuple-level draw per row from the
  ``nulls/foreign_key/...`` stream — components are all-null or all-non-null
  (``nullable``/``null_probability`` agreement is the E111 semantic invariant;
  a mismatch raises here defensively and never arrives from validated input);
- ``unique: true``: non-null values unique via without-replacement selection
  for categoricals (G04 path) or proposal retry for open-domain generators.

Retry behavior (implementation-versioned, part of cache identity via the raw
generator version): proposal retries consume only the column's own values
stream — the per-row null decision is drawn once and kept. At most
``RETRY_LIMIT`` proposal draws are spent per row; exhaustion raises
structured :class:`GenerationFailure` (never truncation or invalid rows).

Cross-column tuple uniqueness (composite PK/FK target checks) is the table
assembler's job (G07–G08), not this module's: per-column ``unique``
enforcement here covers ``unique: true`` and single-column PKs. Callers
generating composite-PK members MUST pass ``enforce_unique=False`` (tuple
uniqueness is enforced later over the assembled rows).
"""

from __future__ import annotations

from collections.abc import Sequence

from data_pipeline_diagnostics.generator.raw_values import (
    ExhaustedDomain,
    generate_scalar,
)
from data_pipeline_diagnostics.generator.rng import (
    foreign_key_nulls_stream_name,
    nulls_stream_name,
    values_stream_name,
)
from data_pipeline_diagnostics.generator.rng import (
    stream as make_stream,
)
from data_pipeline_diagnostics.scenario.raw import RawColumn

__all__ = [
    "RETRY_LIMIT",
    "GenerationFailure",
    "generate_composite_null_mask",
    "generate_scalar_column",
    "generate_scalar_null_mask",
]

RETRY_LIMIT = 1000


class GenerationFailure(Exception):
    """Structured raw-generation failure (no truncation, no invalid rows).

    ``reason`` is a stable machine-readable category (e.g.
    ``"unique-domain-exhausted"``) for future ``failure_record.json``
    mapping; ``detail`` carries the human-readable context.
    """

    def __init__(self, *, table: str, column: str | None, reason: str, detail: str = "") -> None:
        self.table = table
        self.column = column
        self.reason = reason
        self.detail = detail
        message = f"{table}.{column or '*'}: {reason}"
        if detail:
            message += f": {detail}"
        super().__init__(message)


def generate_scalar_null_mask(
    *,
    scenario_id: str,
    data_seed: int,
    table: str,
    column: str,
    row_count: int,
    nullable: bool,
    null_probability: float,
    force_non_null: bool = False,
) -> list[bool]:
    """Per-row null decisions (``True`` = null) for one scalar column.

    Non-nullable columns and ``force_non_null`` (PK members) yield all
    ``False`` without consuming the null stream.
    """
    if row_count < 0:
        raise ValueError(f"row_count must be non-negative, got {row_count}")
    if force_non_null or not nullable:
        return [False] * row_count
    rng = make_stream(scenario_id, data_seed, nulls_stream_name(table, column))
    return [rng.random() < null_probability for _ in range(row_count)]


def generate_composite_null_mask(
    *,
    scenario_id: str,
    data_seed: int,
    relationship: str,
    dependent_table: str,
    target_side: str,
    row_count: int,
    components: Sequence[tuple[bool, float]],
) -> list[bool]:
    """Per-row tuple null decisions for one composite FK (``True`` = whole
    tuple null). One draw per row from the tuple's ``nulls/foreign_key/...``
    stream; components are therefore all-null or all-non-null by
    construction. Component ``(nullable, null_probability)`` agreement is
    asserted defensively (E111 owns it upstream)."""
    if row_count < 0:
        raise ValueError(f"row_count must be non-negative, got {row_count}")
    if not components:
        raise ValueError("composite null mask requires at least one component")
    first_nullable, first_probability = components[0]
    for nullable, probability in components[1:]:
        if nullable != first_nullable or probability != first_probability:
            raise GenerationFailure(
                table=dependent_table,
                column=None,
                reason="composite-null-disagreement",
                detail=f"relationship {relationship}: components disagree on "
                f"(nullable, null_probability) (E111 invariant violated)",
            )
    if not first_nullable:
        return [False] * row_count
    rng = make_stream(
        scenario_id,
        data_seed,
        foreign_key_nulls_stream_name(relationship, dependent_table, target_side),
    )
    return [rng.random() < first_probability for _ in range(row_count)]


def _value_key(value: object) -> tuple[str, object]:
    return (type(value).__name__, value)


def generate_scalar_column(
    *,
    scenario_id: str,
    data_seed: int,
    table: str,
    column: RawColumn,
    row_count: int,
    force_non_null: bool = False,
    enforce_unique: bool | None = None,
) -> list[object]:
    """Full non-FK scalar column: proposals + nulls + single-column uniqueness.

    ``foreign_key`` (needs the G07 target universe) and ``template_string``
    (needs the G08 placeholder row) kinds raise ``ValueError`` here.
    Uniqueness defaults to the column's ``unique`` flag; pass
    ``enforce_unique=False`` for composite-PK members (tuple uniqueness is
    enforced later over assembled rows).
    """
    if row_count < 0:
        raise ValueError(f"row_count must be non-negative, got {row_count}")
    if column.generator.kind in ("foreign_key", "template_string"):
        raise ValueError(
            f"column {table}.{column.name}: kind {column.generator.kind!r} "
            "needs relationship/row context (G07/G08), not a scalar column call"
        )
    unique = column.unique if enforce_unique is None else enforce_unique
    if type(unique) is not bool:
        raise TypeError(f"enforce_unique must be bool or None, got {unique!r}")

    null_mask = generate_scalar_null_mask(
        scenario_id=scenario_id,
        data_seed=data_seed,
        table=table,
        column=str(column.name),
        row_count=row_count,
        nullable=column.nullable,
        null_probability=column.null_probability,
        force_non_null=force_non_null,
    )
    values_rng = make_stream(scenario_id, data_seed, values_stream_name(table, str(column.name)))

    is_categorical = column.generator.kind == "categorical"
    seen: set[tuple[str, object]] = set()
    drawn: list[object] = []
    out: list[object] = []
    for i in range(row_count):
        if null_mask[i]:
            out.append(None)
            continue
        if unique and is_categorical:
            try:
                value = generate_scalar(
                    column.generator,
                    values_rng,
                    i,
                    unique=True,
                    already_drawn=drawn,
                )
            except ExhaustedDomain as exc:
                raise GenerationFailure(
                    table=table,
                    column=str(column.name),
                    reason="unique-domain-exhausted",
                    detail=str(exc),
                ) from exc
            drawn.append(value)
            seen.add(_value_key(value))
            out.append(value)
            continue
        for _ in range(RETRY_LIMIT):
            value = generate_scalar(column.generator, values_rng, i)
            if not unique or _value_key(value) not in seen:
                break
        else:
            raise GenerationFailure(
                table=table,
                column=str(column.name),
                reason="unique-retry-limit-exceeded",
                detail=f"no unseen value in {RETRY_LIMIT} proposals for row {i}",
            )
        if unique:
            seen.add(_value_key(value))
        out.append(value)
    return out
