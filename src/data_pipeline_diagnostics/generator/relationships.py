"""Relationship sampling + row-count conditioning (§§9.3–9.4, 11.3).

Row counts come from per-table ``rows/<table>`` streams (exact when
``min == max``). When sampled counts make a one-to-one (or another
without-replacement) constraint impossible, only the involved tables'
streams are resampled, deterministically, up to ``ROW_COUNT_RETRY_LIMIT``
rounds; unresolvable combinations raise :class:`GenerationFailure` — the
seed is never switched.

FK tuples sample from the caller-built target universe (distinct,
wholly-non-null key tuples in target row-index order; G08 assembles it)
using the dependent's ``foreign_key/<rel>/<dep>/<side>`` stream: with
replacement by default, without replacement for ``one_to_one`` and for
unique-constrained dependents. Composite tuples are atomic (one ``None``
for a null tuple, never partial). Empty or too-small universes, orphans,
partial-null tuples and synthetic keys all raise ``GenerationFailure``.

Relationship resolution is owned by semantic validation; this module reuses
its resolver (no duplicate orientation logic) and treats an unresolved
record as a defensive component-boundary failure. Raw key-dependency cycles
are E135 upstream and generation order is the G08 planner's job: this
module never blocks or invents values, it fails fast with the tables,
columns and relationship identified.

Output lists are aligned to the dependent's internal zero-based row index
(ascending); the index itself is never serialized (G09).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from data_pipeline_diagnostics.generator.raw_constraints import (
    RETRY_LIMIT,
    GenerationFailure,
)
from data_pipeline_diagnostics.generator.rng import (
    foreign_key_stream_name,
    rows_stream_name,
    stream,
)
from data_pipeline_diagnostics.scenario.semantic import (
    ResolvedRelationship,
    _resolve_relationships,
)
from data_pipeline_diagnostics.scenario.types import RowCount

__all__ = [
    "ROW_COUNT_RETRY_LIMIT",
    "CountLink",
    "FkPlan",
    "direct_fk_plan",
    "resolve_relationships",
    "sample_fk_tuples",
    "sample_row_counts",
]

ROW_COUNT_RETRY_LIMIT = 1000


@dataclass(frozen=True)
class CountLink:
    """Without-replacement size constraint: the dependent needs that many
    distinct target tuples, so ``counts[dependent] <= counts[target]`` must
    hold (one-to-one relationships and unique-constrained dependents)."""

    relationship: str
    dependent_table: str
    target_table: str


@dataclass(frozen=True)
class FkPlan:
    """§11.3 sampling-table row for one dependent side: which stream draws,
    and whether draws are without replacement."""

    relationship: str
    dependent_table: str
    target_side: str
    without_replacement: bool


def resolve_relationships(
    relationships: Sequence[object], raw_by_name: Mapping[str, object]
) -> dict[str, ResolvedRelationship]:
    """Resolve orientation/dependent/bridge views via semantic validation's
    resolver (single source of truth; E131 guarantees carry over)."""
    return _resolve_relationships(list(relationships), dict(raw_by_name))


def sample_row_counts(
    *,
    scenario_id: str,
    data_seed: int,
    rows: Mapping[str, RowCount],
    links: Sequence[CountLink] = (),
) -> dict[str, int]:
    """Sample per-table row counts with deterministic 1:1 conditioning.

    Tables with ``min == max`` are exact (no stream). Each ranged table draws
    from its own ``rows/<table>`` stream; on a link violation only the
    involved tables' streams advance. Unknown link tables are a caller bug
    (``ValueError``); exhausted conditioning is ``GenerationFailure``.
    """
    for link in links:
        for table in (link.dependent_table, link.target_table):
            if table not in rows:
                raise ValueError(
                    f"count link {link.relationship!r} references unknown table {table!r}"
                )
    streams = {}
    counts: dict[str, int] = {}
    for table, spec in rows.items():
        if spec.min == spec.max:
            counts[table] = spec.min
        else:
            rng = stream(scenario_id, data_seed, rows_stream_name(table))
            streams[table] = (rng, spec)
            counts[table] = rng.randint(spec.min, spec.max)
    for _ in range(ROW_COUNT_RETRY_LIMIT + 1):
        violated = [
            link for link in links if counts[link.dependent_table] > counts[link.target_table]
        ]
        if not violated:
            return counts
        involved = sorted(
            {table for link in violated for table in (link.dependent_table, link.target_table)}
        )
        if not any(table in streams for table in involved):
            break
        for table in involved:
            if table in streams:
                rng, spec = streams[table]
                counts[table] = rng.randint(spec.min, spec.max)
    raise GenerationFailure(
        table="*",
        column=None,
        reason="row-count-unresolvable",
        detail="no materializable row-count combination for "
        + ", ".join(sorted({link.relationship for link in violated})),
    )


def direct_fk_plan(resolved: ResolvedRelationship, *, unique_dependent: bool = False) -> FkPlan:
    """Map a resolved direct relationship to its §11.3 sampling row.

    ``many_to_many`` has two independent sides — sample each with
    :func:`sample_fk_tuples` directly instead of this helper.
    """
    match resolved.cardinality:
        case "one_to_many":
            return FkPlan(
                relationship=resolved.name,
                dependent_table=resolved.right_table,
                target_side="left",
                without_replacement=bool(unique_dependent),
            )
        case "many_to_one":
            return FkPlan(
                relationship=resolved.name,
                dependent_table=resolved.left_table,
                target_side="right",
                without_replacement=bool(unique_dependent),
            )
        case "one_to_one":
            if resolved.dependent_table is None or resolved.target_table is None:
                raise GenerationFailure(
                    table="*",
                    column=None,
                    reason="unresolved-relationship",
                    detail=f"one_to_one {resolved.name!r} has no resolved "
                    "dependent/target side (E131 invariant violated)",
                )
            target_side = "left" if resolved.target_table == resolved.left_table else "right"
            return FkPlan(
                relationship=resolved.name,
                dependent_table=resolved.dependent_table,
                target_side=target_side,
                without_replacement=True,
            )
        case "many_to_many":
            raise ValueError(
                f"relationship {resolved.name!r} is many_to_many: sample each "
                "bridge side independently with sample_fk_tuples"
            )
        case _:
            raise ValueError(f"unknown cardinality: {resolved.cardinality!r}")


def sample_fk_tuples(
    *,
    scenario_id: str,
    data_seed: int,
    relationship: str,
    dependent_table: str,
    target_side: str,
    universe: Sequence[tuple[object, ...]],
    row_count: int,
    null_mask: Sequence[bool],
    without_replacement: bool,
) -> list[tuple[object, ...] | None]:
    """Sample one dependent side's FK tuples in dependent row-index order.

    ``universe`` is the target's distinct wholly-non-null key tuples in
    target row-index order (built by the caller). Null-masked rows yield
    ``None`` without consuming the stream; every other row draws from the
    side's ``foreign_key/<rel>/<dep>/<side>`` stream.
    """
    if row_count < 0:
        raise ValueError(f"row_count must be non-negative, got {row_count}")
    if len(null_mask) != row_count:
        raise ValueError(f"null_mask length {len(null_mask)} != row_count {row_count}")
    for entry in universe:
        if not isinstance(entry, tuple):
            raise TypeError(f"universe entries must be tuples, got {type(entry).__name__}")
        if any(component is None for component in entry):
            raise GenerationFailure(
                table=dependent_table,
                column=None,
                reason="target-universe-contains-null",
                detail=f"relationship {relationship!r}: target universe holds a null key component",
            )
    rng = stream(
        scenario_id,
        data_seed,
        foreign_key_stream_name(relationship, dependent_table, target_side),
    )
    used: set[int] = set()
    out: list[tuple[object, ...] | None] = []
    for is_null in null_mask:
        if is_null:
            out.append(None)
            continue
        if not universe:
            raise GenerationFailure(
                table=dependent_table,
                column=None,
                reason="empty-target-universe",
                detail=f"relationship {relationship!r}: no target key universe "
                "for a non-null dependent tuple",
            )
        for _ in range(RETRY_LIMIT):
            index = rng.randint(0, len(universe) - 1)
            if not without_replacement or index not in used:
                break
        else:
            raise GenerationFailure(
                table=dependent_table,
                column=None,
                reason="target-universe-too-small",
                detail=f"relationship {relationship!r}: no unseen target tuple "
                f"in {RETRY_LIMIT} draws",
            )
        used.add(index)
        out.append(universe[index])
    return out
