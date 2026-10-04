"""Relationship sampling + row-count conditioning (§§9.3–9.4, 11.3).

Row counts come from per-table ``rows/<table>`` streams (exact when
``min == max``). When sampled counts make a one-to-one (or another
without-replacement) constraint impossible, or exceed a table's
composite-PK tuple capacity, only the involved tables' streams are
resampled, deterministically, up to ``ROW_COUNT_RETRY_LIMIT`` rounds;
unresolvable combinations raise :class:`GenerationFailure` — the
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


def _capacity_bound(cap: object, counts: Mapping[str, int]) -> int:
    """Evaluate one dependent capacity descriptor against sampled counts."""
    bound = cap.multiplier
    for parent in cap.parent_tables:
        bound *= counts[parent]
    return bound


def _reachable_uppers(
    rows: Mapping[str, RowCount],
    links: Sequence[CountLink],
    capmap: Mapping[str, int],
    capacities: Sequence[object],
) -> dict[str, int]:
    """Greatest per-table counts satisfying all supported count constraints,
    computed without RNG draws. Starts from declared maxima (plus static
    caps) and tightens monotonically to a fixpoint; strictly decreasing
    naturals bounded below by zero always terminate. Deterministic: the
    fixpoint is unique and constraints are visited in input order."""
    upper = {table: spec.max for table, spec in rows.items()}
    for table, cap in capmap.items():
        upper[table] = min(upper[table], cap)
    changed = True
    while changed:
        changed = False
        for link in links:
            dep, tgt = link.dependent_table, link.target_table
            if upper[dep] > upper[tgt]:
                upper[dep] = upper[tgt]
                changed = True
        for cap in capacities:
            bound = cap.multiplier
            for parent in cap.parent_tables:
                bound *= upper[parent]
            if upper[cap.dependent_table] > bound:
                upper[cap.dependent_table] = bound
                changed = True
    return upper


def _apply_count_fallback(
    counts: dict[str, int],
    upper: Mapping[str, int],
    rows: Mapping[str, RowCount],
    links: Sequence[CountLink],
    capmap: Mapping[str, int],
    capacities: Sequence[object],
) -> None:
    """Constructive fallback after exhausted resampling: set the counts of
    every table in the violated connected components to the previously
    computed reachable upper counts. The component union is built over ALL
    supported count constraints (static-only caps form isolated vertices);
    untouched counts keep their sampled proposals and no RNG draws occur.
    Exact counts and declared intervals are preserved: every table reaching
    this point passed the ``upper >= rows.min`` pre-check, and exact tables
    have ``upper ==`` their fixed count. Verifies everything before
    generating values; a residual violation is a defensive failure."""
    adjacency: dict[str, set[str]] = {table: set() for table in rows}
    for link in links:
        adjacency[link.dependent_table].add(link.target_table)
        adjacency[link.target_table].add(link.dependent_table)
    for cap in capacities:
        for parent in cap.parent_tables:
            adjacency[cap.dependent_table].add(parent)
            adjacency[parent].add(cap.dependent_table)
    seeds: set[str] = set()
    for link in links:
        if counts[link.dependent_table] > counts[link.target_table]:
            seeds.add(link.dependent_table)
            seeds.add(link.target_table)
    for table, cap in capmap.items():
        if counts[table] > cap:
            seeds.add(table)
    for cap in capacities:
        bound = cap.multiplier
        for parent in cap.parent_tables:
            bound *= counts[parent]
        if counts[cap.dependent_table] > bound:
            seeds.add(cap.dependent_table)
            seeds.update(cap.parent_tables)
    component: set[str] = set()
    stack = sorted(seeds)
    while stack:
        node = stack.pop()
        if node in component:
            continue
        component.add(node)
        stack.extend(sorted(adjacency[node] - component))
    for table in component:
        counts[table] = upper[table]
    for table, cap in capmap.items():
        if counts[table] > cap:
            raise GenerationFailure(
                table=table,
                column=None,
                reason="row-count-capacity-exceeded",
                detail=f"reachable upper counts still violate PK capacity {cap} for {table}",
            )
    for cap in capacities:
        if counts[cap.dependent_table] > _capacity_bound(cap, counts):
            raise GenerationFailure(
                table=cap.dependent_table,
                column=None,
                reason="row-count-capacity-exceeded",
                detail=f"reachable upper counts still violate PK capacity for {cap.dependent_table}",
            )
    violated = [link for link in links if counts[link.dependent_table] > counts[link.target_table]]
    if violated:
        raise GenerationFailure(
            table="*",
            column=None,
            reason="row-count-unresolvable",
            detail="no materializable row-count combination for "
            + ", ".join(sorted({link.relationship for link in violated})),
        )


def sample_row_counts(
    *,
    scenario_id: str,
    data_seed: int,
    rows: Mapping[str, RowCount],
    links: Sequence[CountLink] = (),
    caps: Mapping[str, int] | None = None,
    capacity_constraints: Sequence[object] = (),
) -> dict[str, int]:
    """Sample per-table row counts with deterministic conditioning.

    Tables with ``min == max`` are exact (no stream). Each ranged table draws
    from its own ``rows/<table>`` stream; on a link violation, capacity
    overrun, or dependent-capacity violation, only the involved tables'
    streams advance. ``caps`` bounds per-table counts by composite-PK tuple
    capacity (a table needing more distinct keys than its domain allows is
    resampled, not failed outright — §9.3); a range that cannot satisfy its
    cap fails fast as a range-level pigeonhole. ``capacity_constraints``
    accepts dependent capacity descriptors with ``dependent_table: str``,
    ``multiplier: int`` and ``parent_tables: tuple[str, ...]`` attributes,
    evaluated as ``counts[dependent] <= multiplier * prod(counts[parent])``
    against sampled (not declared-maximum) parent counts. Unknown
    link/cap/capacity tables are a caller bug (``ValueError``); exhausted
    conditioning falls back to verified reachable upper counts, and only a
    still-violated combination raises ``GenerationFailure``.
    """
    capmap = dict(caps or {})
    capacities = list(capacity_constraints)
    for table in capmap:
        if table not in rows:
            raise ValueError(f"capacity cap references unknown table {table!r}")
    for link in links:
        for table in (link.dependent_table, link.target_table):
            if table not in rows:
                raise ValueError(
                    f"count link {link.relationship!r} references unknown table {table!r}"
                )
    for cap in capacities:
        if cap.dependent_table not in rows:
            raise ValueError(
                f"capacity constraint references unknown table {cap.dependent_table!r}"
            )
        for parent in cap.parent_tables:
            if parent not in rows:
                raise ValueError(f"capacity constraint references unknown parent table {parent!r}")
    for table, cap in capmap.items():
        if rows[table].min > cap:
            raise GenerationFailure(
                table=table,
                column=None,
                reason="row-count-capacity-exceeded",
                detail=f"rows.min {rows[table].min} exceeds PK capacity {cap}",
            )
    upper = _reachable_uppers(rows, links, capmap, [])
    for table in rows:
        if upper[table] < rows[table].min:
            raise GenerationFailure(
                table=table,
                column=None,
                reason="row-count-unresolvable",
                detail=f"rows.min {rows[table].min} exceeds reachable upper {upper[table]} "
                "under CountLink ranges",
            )
    upper = _reachable_uppers(rows, links, capmap, capacities)
    for table in rows:
        if upper[table] < rows[table].min:
            raise GenerationFailure(
                table=table,
                column=None,
                reason="row-count-capacity-exceeded",
                detail=f"rows.min {rows[table].min} exceeds reachable PK capacity {upper[table]}",
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
    for resample_round in range(ROW_COUNT_RETRY_LIMIT + 1):
        violated = [
            link for link in links if counts[link.dependent_table] > counts[link.target_table]
        ]
        overcap = sorted(table for table, cap in capmap.items() if counts[table] > cap)
        violated_caps = []
        for cap in capacities:
            if counts[cap.dependent_table] > _capacity_bound(cap, counts):
                violated_caps.append(cap)
        if not violated and not overcap and not violated_caps:
            return counts
        for table in overcap:
            if table not in streams:
                raise GenerationFailure(
                    table=table,
                    column=None,
                    reason="row-count-capacity-exceeded",
                    detail=f"exact count {counts[table]} exceeds PK capacity {capmap[table]}",
                )
        involved = sorted(
            {table for link in violated for table in (link.dependent_table, link.target_table)}
            | set(overcap)
            | {
                table
                for cap in violated_caps
                for table in (cap.dependent_table, *cap.parent_tables)
            }
        )
        if not any(table in streams for table in involved):
            break
        if resample_round >= ROW_COUNT_RETRY_LIMIT:
            break
        for table in involved:
            if table in streams:
                rng, spec = streams[table]
                counts[table] = rng.randint(spec.min, spec.max)
    _apply_count_fallback(counts, upper, rows, links, capmap, capacities)
    return counts


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
