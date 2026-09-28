"""RawPlan builder + generation-unit ordering + orchestration (§§4.1, 9.1–9.2).

:func:`build_raw_plan` is pure (no RNG draws, no I/O): it derives table
order, row-count specs, leaf/template evaluation order, composite FK groups
with ordered target bindings, hard constraints and stream names from a
``ValidatedScenario``. It holds no dbt models, SQL, tests, paths or fault
metadata.

Execution (:func:`execute_raw_plan`) wires the G03–G07 executors under the
plan with no new value semantics: row counts (G07 conditioning), leaf
columns (G06 nulls + uniqueness), templates in placeholder order (G04
render), FK groups from caller-built target universes (G07 sampling), and a
final primary-key verification. Table-level retry on PK collision is
deliberately absent: a violation raises ``GenerationFailure`` (never an
invalid row); retry budgets belong to the named-stream mechanisms.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from data_pipeline_diagnostics.generator.raw_constraints import (
    GenerationFailure,
    generate_composite_null_mask,
    generate_scalar_column,
)
from data_pipeline_diagnostics.generator.raw_values import generate_template
from data_pipeline_diagnostics.generator.relationships import (
    CountLink,
    direct_fk_plan,
    resolve_relationships,
    sample_fk_tuples,
    sample_row_counts,
)
from data_pipeline_diagnostics.generator.rng import (
    STREAM_SCHEME,
    foreign_key_nulls_stream_name,
    foreign_key_stream_name,
    nulls_stream_name,
    stream,
    values_stream_name,
)
from data_pipeline_diagnostics.scenario.generators import GeneratorSpec
from data_pipeline_diagnostics.scenario.raw import RawColumn
from data_pipeline_diagnostics.scenario.semantic import ValidatedScenario
from data_pipeline_diagnostics.scenario.types import DataType, RowCount

__all__ = [
    "ColumnPlan",
    "FkGroupPlan",
    "RawPlan",
    "TablePlan",
    "build_raw_plan",
    "execute_raw_plan",
    "generate_raw_data",
]

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


@dataclass(frozen=True)
class ColumnPlan:
    """One raw column's generation facts (evaluation position from the table plan)."""

    name: str
    kind: str  # "leaf" | "template" | "foreign_key"
    config: GeneratorSpec
    type: DataType
    nullable: bool
    null_probability: float
    unique: bool
    placeholders: tuple[str, ...] = ()
    relationship: str | None = None
    target_side: str | None = None
    value_stream: str = ""
    null_stream: str = ""


@dataclass(frozen=True)
class TablePlan:
    """One raw table's generation facts."""

    name: str
    rows: RowCount
    columns: tuple[ColumnPlan, ...] = ()
    declaration_column_order: tuple[str, ...] = ()
    primary_key: tuple[str, ...] = ()


@dataclass(frozen=True)
class FkGroupPlan:
    """One atomic FK tuple unit with ordered target bindings.

    ``dependent_columns[i]`` takes its value from the sampled universe
    tuple's position ``i``, whose order is ``target_columns``.
    """

    relationship: str
    dependent_table: str
    target_side: str
    dependent_columns: tuple[str, ...] = ()
    target_table: str = ""
    target_columns: tuple[str, ...] = ()
    null_terms: tuple[tuple[bool, float], ...] = ()
    without_replacement: bool = False
    value_stream: str = ""
    null_stream: str = ""


@dataclass(frozen=True)
class RawPlan:
    """Immutable internal raw execution plan (no dbt/SQL/paths/faults)."""

    scenario_id: str
    stream_scheme: str = STREAM_SCHEME
    table_order: tuple[str, ...] = ()
    tables: tuple[TablePlan, ...] = ()
    fk_groups: tuple[FkGroupPlan, ...] = ()
    count_links: tuple[CountLink, ...] = field(default_factory=tuple)


def _require_validated(validated: object) -> ValidatedScenario:
    if not isinstance(validated, ValidatedScenario):
        raise TypeError(
            "compiler input must be a ValidatedScenario "
            f"(got {type(validated).__name__}); "
            "use prepare_clean_instance_from_json for JSON/path input"
        )
    return validated


def _dependent_unique(
    dependent_columns: Sequence[str],
    primary_key: Sequence[str],
    unique_columns: Sequence[str],
) -> bool:
    """Whether the dependent tuple is unique-constrained, so sampling without
    replacement overrides the default: the PK is covered by the tuple, or one
    component is individually unique."""
    if primary_key and set(primary_key) <= set(dependent_columns):
        return True
    unique = set(unique_columns)
    return any(col in unique for col in dependent_columns)


def build_raw_plan(validated: ValidatedScenario) -> RawPlan:
    """Build the immutable raw execution plan (pure: no RNG, no I/O)."""
    scenario = _require_validated(validated).scenario
    scenario_id = str(scenario.scenario_id)
    tables = list(scenario.raw_tables)
    table_index = {str(t.name): i for i, t in enumerate(tables)}
    columns_by_table = {str(t.name): list(t.columns) for t in tables}
    pk_by_table = {str(t.name): tuple(str(c) for c in t.primary_key) for t in tables}
    resolved = resolve_relationships(list(scenario.relationships), dict(validated.raw_by_name))

    fk_groups: list[FkGroupPlan] = []
    for rel in scenario.relationships:
        record = resolved[str(rel.name)]
        if record.cardinality == "many_to_many":
            sides = (
                ("left", record.bridge_left_columns, record.left_table, record.left_columns),
                ("right", record.bridge_right_columns, record.right_table, record.right_columns),
            )
            for side, dep_cols, target_table, target_cols in sides:
                if (
                    record.bridge_table is None
                    or dep_cols is None
                    or target_table is None
                    or target_cols is None
                ):
                    raise GenerationFailure(
                        table="*",
                        column=None,
                        reason="unresolved-relationship",
                        detail=f"many_to_many {rel.name!r} side {side!r} unresolved",
                    )
                fk_groups.append(
                    _group_plan(
                        str(rel.name),
                        str(record.bridge_table),
                        side,
                        tuple(str(c) for c in dep_cols),
                        str(target_table),
                        tuple(str(c) for c in target_cols),
                        columns_by_table,
                        pk_by_table,
                        without_replacement=False,
                    )
                )
            continue
        plan = direct_fk_plan(record)
        if record.dependent_columns is None or record.target_columns is None:
            raise GenerationFailure(
                table="*",
                column=None,
                reason="unresolved-relationship",
                detail=f"{record.cardinality} {rel.name!r} has no resolved sides",
            )
        target_table = record.left_table if plan.target_side == "left" else record.right_table
        dep_columns = tuple(str(c) for c in record.dependent_columns)
        fk_groups.append(
            _group_plan(
                str(rel.name),
                plan.dependent_table,
                plan.target_side,
                dep_columns,
                str(target_table),
                tuple(str(c) for c in record.target_columns),
                columns_by_table,
                pk_by_table,
                without_replacement=plan.without_replacement,
            )
        )

    table_order = _order_tables(table_index, fk_groups)

    count_links: list[CountLink] = []
    for group in fk_groups:
        source = next(r for r in scenario.relationships if str(r.name) == group.relationship)
        if source.cardinality == "one_to_one" or group.without_replacement:
            count_links.append(
                CountLink(
                    relationship=group.relationship,
                    dependent_table=group.dependent_table,
                    target_table=group.target_table,
                )
            )

    groups_by_table: dict[str, list[FkGroupPlan]] = {name: [] for name in table_index}
    for group in fk_groups:
        groups_by_table[group.dependent_table].append(group)

    table_plans: list[TablePlan] = []
    for name in table_order:
        spec = next(t for t in tables if str(t.name) == name)
        table_plans.append(
            TablePlan(
                name=name,
                rows=spec.rows,
                columns=tuple(_order_columns(name, list(spec.columns), groups_by_table[name])),
                declaration_column_order=tuple(str(c.name) for c in spec.columns),
                primary_key=pk_by_table[name],
            )
        )

    return RawPlan(
        scenario_id=scenario_id,
        stream_scheme=STREAM_SCHEME,
        table_order=tuple(table_order),
        tables=tuple(table_plans),
        fk_groups=tuple(fk_groups),
        count_links=tuple(count_links),
    )


def _group_plan(
    relationship: str,
    dependent_table: str,
    target_side: str,
    dependent_columns: tuple[str, ...],
    target_table: str,
    target_columns: tuple[str, ...],
    columns_by_table: Mapping[str, Sequence[RawColumn]],
    pk_by_table: Mapping[str, Sequence[str]],
    *,
    without_replacement: bool,
) -> FkGroupPlan:
    by_name = {str(col.name): col for col in columns_by_table[dependent_table]}
    try:
        terms = tuple(
            (bool(by_name[col].nullable), float(by_name[col].null_probability))
            for col in dependent_columns
        )
    except KeyError as exc:
        raise GenerationFailure(
            table=dependent_table,
            column=None,
            reason="unknown-fk-column",
            detail=f"relationship {relationship!r} references unknown column {exc}",
        ) from exc
    if not without_replacement:
        without_replacement = _dependent_unique(
            dependent_columns,
            pk_by_table[dependent_table],
            [str(col.name) for col in columns_by_table[dependent_table] if col.unique],
        )
    return FkGroupPlan(
        relationship=relationship,
        dependent_table=dependent_table,
        target_side=target_side,
        dependent_columns=dependent_columns,
        target_table=target_table,
        target_columns=target_columns,
        null_terms=terms,
        without_replacement=without_replacement,
        value_stream=foreign_key_stream_name(relationship, dependent_table, target_side),
        null_stream=foreign_key_nulls_stream_name(relationship, dependent_table, target_side),
    )


def _order_tables(table_index: Mapping[str, int], fk_groups: Sequence[FkGroupPlan]) -> list[str]:
    """Target tables before dependents (Kahn; declaration-order tiebreak).

    Self-loops resolve intra-table (key leaves precede FK units), so they add
    no table-level edge. A leftover cycle is a defensive component-boundary
    failure identifying tables, columns and relationships.
    """
    need: dict[str, set[str]] = {name: set() for name in table_index}
    for group in fk_groups:
        dep, target = group.dependent_table, group.target_table
        if dep == target:
            continue
        if dep not in table_index or target not in table_index:
            raise GenerationFailure(
                table=dep,
                column=None,
                reason="unknown-fk-table",
                detail=f"relationship {group.relationship!r} references unknown "
                f"table (dep={dep!r}, target={target!r})",
            )
        need[dep].add(target)
    ordered: list[str] = []
    ready = sorted([name for name, deps in need.items() if not deps], key=table_index.__getitem__)
    while ready:
        name = ready.pop(0)
        ordered.append(name)
        for dependent, deps in need.items():
            if name in deps:
                deps.remove(name)
                if not deps and dependent not in ordered and dependent not in ready:
                    ready.append(dependent)
        ready.sort(key=table_index.__getitem__)
    if len(ordered) != len(table_index):
        stuck = sorted(set(table_index) - set(ordered))
        involved = [g for g in fk_groups if g.dependent_table in stuck]
        raise GenerationFailure(
            table="*",
            column=None,
            reason="raw-dependency-cycle",
            detail="raw key dependency cycle involving tables "
            + ", ".join(stuck)
            + "; columns "
            + ", ".join(sorted({c for g in involved for c in g.dependent_columns}))
            + "; relationships "
            + ", ".join(sorted({g.relationship for g in involved})),
        )
    return ordered


def _order_columns(
    table: str,
    columns: Sequence[RawColumn],
    groups: Sequence[FkGroupPlan],
) -> list[ColumnPlan]:
    """Leaf/template/FK-group units in dependency order (declaration tiebreak)."""
    group_of = {col: group for group in groups for col in group.dependent_columns}
    index_of = {str(col.name): i for i, col in enumerate(columns)}
    for col in group_of:
        if col not in index_of:
            raise GenerationFailure(
                table=table,
                column=None,
                reason="unknown-fk-column",
                detail=f"FK group references unknown column {col!r}",
            )
    units: dict[str, dict] = {}
    for col in columns:
        name = str(col.name)
        if name in group_of:
            continue  # covered by its FK group unit
        if col.generator.kind == "template_string":
            placeholders = tuple(dict.fromkeys(_PLACEHOLDER_RE.findall(col.generator.template)))
            for placeholder in placeholders:
                if placeholder not in index_of:
                    raise GenerationFailure(
                        table=table,
                        column=name,
                        reason="missing-template-placeholder",
                        detail=f"placeholder {{{placeholder}}} is not a column",
                    )
            units[f"template:{name}"] = {
                "decl": index_of[name],
                "deps": set(placeholders),
                "column": col,
            }
        elif col.generator.kind == "foreign_key":
            raise GenerationFailure(
                table=table,
                column=name,
                reason="orphan-fk-column",
                detail=f"column {name!r} has a foreign_key generator outside any group",
            )
        else:
            units[f"leaf:{name}"] = {"decl": index_of[name], "deps": set(), "column": col}
    for group in groups:
        units[f"fk:{group.relationship}/{group.target_side}"] = {
            "decl": min(index_of[col] for col in group.dependent_columns),
            "deps": set(),
            "group": group,
        }
    by_name = {str(col.name): col for col in columns}
    for unit in units.values():
        resolved_deps = set()
        for dep in unit["deps"]:
            if dep in group_of:
                group = group_of[dep]
                resolved_deps.add(f"fk:{group.relationship}/{group.target_side}")
            elif by_name[dep].generator.kind == "template_string":
                resolved_deps.add(f"template:{dep}")
            else:
                resolved_deps.add(f"leaf:{dep}")
        unit["deps"] = resolved_deps
    plans: list[ColumnPlan] = []
    for key in _topo_units(table, units):
        unit = units[key]
        if "group" in unit:
            group = unit["group"]
            for col_name in group.dependent_columns:
                col = by_name[col_name]
                plans.append(
                    _column_plan(
                        col,
                        "foreign_key",
                        (),
                        group.relationship,
                        group.target_side,
                        group.value_stream,
                        group.null_stream,
                    )
                )
        else:
            col = unit["column"]
            is_template = col.generator.kind == "template_string"
            plans.append(
                _column_plan(
                    col,
                    "template" if is_template else "leaf",
                    tuple(dict.fromkeys(_PLACEHOLDER_RE.findall(col.generator.template)))
                    if is_template
                    else (),
                    None,
                    None,
                    values_stream_name(table, str(col.name)),
                    nulls_stream_name(table, str(col.name)),
                )
            )
    return plans


def _column_plan(
    col: RawColumn,
    kind: str,
    placeholders: tuple[str, ...],
    relationship: str | None,
    target_side: str | None,
    value_stream: str,
    null_stream: str,
) -> ColumnPlan:
    return ColumnPlan(
        name=str(col.name),
        kind=kind,
        config=col.generator,
        type=col.type,
        nullable=bool(col.nullable),
        null_probability=float(col.null_probability),
        unique=bool(col.unique),
        placeholders=placeholders,
        relationship=relationship,
        target_side=target_side,
        value_stream=value_stream,
        null_stream=null_stream,
    )


def _topo_units(table: str, units: Mapping[str, dict]) -> list[str]:
    deps = {key: set(unit["deps"]) - {key} for key, unit in units.items()}
    ordered: list[str] = []
    ready = sorted(
        [key for key, unit_deps in deps.items() if not unit_deps],
        key=lambda key: units[key]["decl"],
    )
    while ready:
        key = ready.pop(0)
        ordered.append(key)
        for other, unit_deps in deps.items():
            if key in unit_deps:
                unit_deps.remove(key)
                if not unit_deps and other not in ordered and other not in ready:
                    ready.append(other)
        ready.sort(key=lambda key: units[key]["decl"])
    if len(ordered) != len(units):
        stuck = sorted(
            {
                part.split(":", 1)[1].split("/")[0]
                for part in set(units) - set(ordered)
                if ":" in part
            }
        )
        raise GenerationFailure(
            table=table,
            column=None,
            reason="template-dependency-cycle",
            detail=f"column dependency cycle involving {', '.join(stuck)}",
        )
    return ordered


def execute_raw_plan(plan: RawPlan, data_seed: int) -> dict[str, list[dict[str, object]]]:
    """Execute the plan's units in order (wires G03–G07, no new semantics)."""
    if type(data_seed) is not int or not 0 <= data_seed <= 2**63 - 1:
        raise ValueError(f"data_seed must be a strict int in [0, 2**63 - 1], got {data_seed!r}")
    counts = sample_row_counts(
        scenario_id=plan.scenario_id,
        data_seed=data_seed,
        rows={table.name: table.rows for table in plan.tables},
        links=list(plan.count_links),
    )
    groups = {(g.relationship, g.dependent_table, g.target_side): g for g in plan.fk_groups}
    tables: dict[str, list[dict[str, object]]] = {}
    for table in plan.tables:
        rows = _generate_table(
            plan.scenario_id, data_seed, table, counts[table.name], tables, groups
        )
        order = table.declaration_column_order
        tables[table.name] = [{name: row[name] for name in order} for row in rows]
    return tables


def _generate_table(
    scenario_id: str,
    data_seed: int,
    table: TablePlan,
    count: int,
    finished: Mapping[str, list[dict[str, object]]],
    groups: Mapping[tuple[str, str, str], FkGroupPlan],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [{} for _ in range(count)]
    sampled: set[tuple[str, str, str]] = set()
    for cplan in table.columns:
        if cplan.kind == "leaf":
            column = RawColumn(
                name=cplan.name,  # type: ignore[arg-type]
                type=cplan.type,
                nullable=cplan.nullable,
                null_probability=cplan.null_probability,
                unique=cplan.unique,
                generator=cplan.config,
            )
            single_pk = len(table.primary_key) == 1 and table.primary_key[0] == cplan.name
            values = generate_scalar_column(
                scenario_id=scenario_id,
                data_seed=data_seed,
                table=table.name,
                column=column,
                row_count=count,
                force_non_null=cplan.name in table.primary_key,
                enforce_unique=True if (cplan.unique or single_pk) else None,
            )
            for row, value in zip(rows, values):
                row[cplan.name] = value
        elif cplan.kind == "template":
            rng = stream(scenario_id, data_seed, cplan.value_stream)
            for i, row in enumerate(rows):
                row[cplan.name] = generate_template(cplan.config, rng, i, row)  # type: ignore[arg-type]
        else:
            key = (cplan.relationship or "", table.name, cplan.target_side or "")
            if key in sampled:
                continue
            group = groups[key]
            target_rows = finished[group.target_table]
            universe = _target_universe(target_rows, group.target_columns)
            if any(col in table.primary_key for col in group.dependent_columns):
                terms = tuple((False, 0.0) for _ in group.null_terms)
            else:
                terms = group.null_terms
            null_mask = generate_composite_null_mask(
                scenario_id=scenario_id,
                data_seed=data_seed,
                relationship=group.relationship,
                dependent_table=table.name,
                target_side=group.target_side,
                row_count=count,
                components=list(terms),
            )
            tuples = sample_fk_tuples(
                scenario_id=scenario_id,
                data_seed=data_seed,
                relationship=group.relationship,
                dependent_table=table.name,
                target_side=group.target_side,
                universe=universe,
                row_count=count,
                null_mask=null_mask,
                without_replacement=group.without_replacement,
            )
            width = len(group.dependent_columns)
            for row, item in zip(rows, tuples):
                values = item if item is not None else (None,) * width
                for col_name, value in zip(group.dependent_columns, values):
                    row[col_name] = value
            sampled.add(key)
    _verify_primary_key(table.name, table.primary_key, rows)
    return rows


def _target_universe(
    target_rows: Sequence[Mapping[str, object]], key_columns: Sequence[str]
) -> list[tuple[object, ...]]:
    """Distinct wholly-non-null key tuples in target row-index order."""
    return list(
        dict.fromkeys(
            tuple(row[col] for col in key_columns)
            for row in target_rows
            if all(row[col] is not None for col in key_columns)
        )
    )


def _verify_primary_key(
    table: str, primary_key: Sequence[str], rows: Sequence[Mapping[str, object]]
) -> None:
    if not primary_key:
        return
    seen: set[tuple[object, ...]] = set()
    for i, row in enumerate(rows):
        key = tuple(row[col] for col in primary_key)
        if any(part is None for part in key):
            raise GenerationFailure(
                table=table,
                column=None,
                reason="null-primary-key",
                detail=f"row {i} has a null primary-key component",
            )
        if key in seen:
            raise GenerationFailure(
                table=table,
                column=None,
                reason="duplicate-primary-key",
                detail=f"row {i} repeats primary key {key!r}",
            )
        seen.add(key)


def generate_raw_data(
    validated: ValidatedScenario, data_seed: int
) -> dict[str, list[dict[str, object]]]:
    """Convenience: build the plan for ``validated`` and execute it."""
    return execute_raw_plan(build_raw_plan(validated), data_seed)
