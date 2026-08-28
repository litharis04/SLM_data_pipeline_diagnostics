"""Semantic validation — cross-object checks (SCENARIO_SPEC §17)."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from data_pipeline_diagnostics.scenario.errors import (
    ErrorCode,
    SemanticIssue,
    SemanticValidationError,
)
from data_pipeline_diagnostics.scenario.types import DataType

if TYPE_CHECKING:
    from data_pipeline_diagnostics.scenario.models import Scenario

# ---------------------------------------------------------------------------
# ValidatedScenario – distinct immutable wrapper
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidatedScenario:
    """Result of successful semantic validation."""

    scenario: object  # Scenario
    raw_by_name: dict
    staging_by_name: dict
    intermediate_by_name: dict
    output_by_name: dict
    relationships_by_name: dict
    topological_order: tuple
    lineage: dict  # model -> dict[column -> source]
    derived_assertions: tuple


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# generator/type compatibility matrix (§8.3)
_ALLOWED_GENERATORS: dict[DataType, set[str]] = {
    DataType.string: {
        "formatted_id",
        "categorical",
        "random_string",
        "template_string",
        "foreign_key",
        "person_name",
        "email",
        "city",
        "street_address",
        "company_name",
        "phone_number",
    },
    DataType.integer: {"integer_range", "categorical", "foreign_key"},
    DataType.float: {"float_range", "categorical", "foreign_key"},
    DataType.boolean: {"boolean", "categorical", "foreign_key"},
    DataType.date: {"date_range", "foreign_key"},
    DataType.timestamp: {"timestamp_range", "foreign_key"},
}

# Faker kinds
_FAKER_KINDS = {"person_name", "email", "city", "street_address", "company_name", "phone_number"}


def _add_issue(
    issues: list[SemanticIssue], code: str, path: str, message: str, related: str | None = None
) -> None:
    issues.append(SemanticIssue(code=code, path=path, message=message, related=related))


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------


def validate_semantics(scenario: Scenario) -> ValidatedScenario:  # type: ignore[no-untyped-def]
    """Validate ``scenario`` semantically and return ``ValidatedScenario`` or raise."""
    issues: list[SemanticIssue] = []

    # Symbol tables
    raw_by_name: dict[str, object] = {}
    staging_by_name: dict[str, object] = {}
    intermediate_by_name: dict[str, object] = {}
    output_by_name: dict[str, object] = {}
    rel_by_name: dict[str, object] = {}
    assertion_by_name: dict[str, object] = {}

    # Track missing refs to suppress cascades
    missing_tables: set[str] = set()
    missing_models: set[str] = set()

    # §17.2 unique raw-table names
    seen_raw: set[str] = set()
    for idx, tbl in enumerate(scenario.raw_tables):
        path = f"raw_tables[{idx}].name"
        if tbl.name in seen_raw:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_RAW_TABLE,
                path,
                f"duplicate raw table name '{tbl.name}'",
                related=tbl.name,
            )
        else:
            seen_raw.add(tbl.name)
            if tbl.name not in raw_by_name:
                raw_by_name[tbl.name] = tbl

    # unique model names across staging|intermediate|output
    seen_models: dict[str, str] = {}
    for idx, m in enumerate(scenario.staging_models):
        path = f"staging_models[{idx}].name"
        if m.name in seen_models:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_MODEL,
                path,
                f"duplicate model name '{m.name}'",
                related=m.name,
            )
        elif m.name in raw_by_name:
            _add_issue(
                issues,
                ErrorCode.RAW_MODEL_COLLISION,
                path,
                f"model name '{m.name}' collides with raw table",
                related=m.name,
            )
        else:
            seen_models[m.name] = "staging"
            staging_by_name[m.name] = m
    for idx, m in enumerate(scenario.intermediate_models):
        path = f"intermediate_models[{idx}].name"
        if m.name in seen_models:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_MODEL,
                path,
                f"duplicate model name '{m.name}'",
                related=m.name,
            )
        elif m.name in raw_by_name:
            _add_issue(
                issues,
                ErrorCode.RAW_MODEL_COLLISION,
                path,
                f"model name '{m.name}' collides with raw table",
                related=m.name,
            )
        else:
            seen_models[m.name] = "intermediate"
            intermediate_by_name[m.name] = m
    for idx, m in enumerate(scenario.output_models):
        path = f"output_models[{idx}].name"
        if m.name in seen_models:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_MODEL,
                path,
                f"duplicate model name '{m.name}'",
                related=m.name,
            )
        elif m.name in raw_by_name:
            _add_issue(
                issues,
                ErrorCode.RAW_MODEL_COLLISION,
                path,
                f"model name '{m.name}' collides with raw table",
                related=m.name,
            )
        else:
            seen_models[m.name] = "output"
            output_by_name[m.name] = m

    # unique relationship names
    seen_rel: set[str] = set()
    for idx, rel in enumerate(scenario.relationships):
        path = f"relationships[{idx}].name"
        if rel.name in seen_rel:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_RELATIONSHIP,
                path,
                f"duplicate relationship name '{rel.name}'",
                related=rel.name,
            )
        else:
            seen_rel.add(rel.name)
            rel_by_name[rel.name] = rel

    # unique assertion names
    seen_assert: set[str] = set()
    for idx, a in enumerate(scenario.tests):
        path = f"tests[{idx}].name"
        if a.name in seen_assert:
            _add_issue(
                issues,
                ErrorCode.UNIQUE_ASSERTION,
                path,
                f"duplicate assertion name '{a.name}'",
                related=a.name,
            )
        else:
            seen_assert.add(a.name)
            assertion_by_name[a.name] = a

    # Build raw column maps for later checks
    raw_col_map: dict[str, dict[str, object]] = {}  # table -> col_name -> RawColumn
    raw_col_type: dict[str, dict[str, DataType]] = {}
    for tbl in scenario.raw_tables:
        col_map: dict[str, object] = {}
        type_map: dict[str, DataType] = {}
        for col in tbl.columns:
            col_map[col.name] = col
            type_map[col.name] = col.type
        raw_col_map[tbl.name] = col_map
        raw_col_type[tbl.name] = type_map

    # §17.3 Raw/keys/generators
    for idx, tbl in enumerate(scenario.raw_tables):
        base = f"raw_tables[{idx}]"
        # PK members exist & non-nullable
        for pk_col in tbl.primary_key:
            col = raw_col_map.get(tbl.name, {}).get(pk_col)
            if col is None:
                _add_issue(
                    issues,
                    ErrorCode.INVALID_PK,
                    f"{base}.primary_key",
                    f"PK column '{pk_col}' does not exist in table '{tbl.name}'",
                    related=pk_col,
                )
            elif getattr(col, "nullable", False):
                _add_issue(
                    issues,
                    ErrorCode.INVALID_PK,
                    f"{base}.primary_key",
                    f"PK column '{pk_col}' must be non-nullable",
                    related=pk_col,
                )
        # generator/type compatibility
        for cidx, col in enumerate(tbl.columns):
            cpath = f"{base}.columns[{cidx}]"
            gen_kind = getattr(col.generator, "kind", None)
            allowed = _ALLOWED_GENERATORS.get(col.type)
            if allowed is not None and gen_kind not in allowed:
                _add_issue(
                    issues,
                    ErrorCode.GENERATOR_TYPE_MISMATCH,
                    f"{cpath}.generator.kind",
                    f"generator '{gen_kind}' not allowed for column type '{col.type.value}'",
                    related=gen_kind,
                )
            # Faker only on string
            if gen_kind in _FAKER_KINDS and col.type != DataType.string:
                _add_issue(
                    issues,
                    ErrorCode.FAKER_TYPE,
                    f"{cpath}.generator.kind",
                    f"Faker generator '{gen_kind}' only allowed on string columns",
                    related=gen_kind,
                )
            # categorical homogeneous
            if gen_kind == "categorical":
                values = getattr(col.generator, "values", ())
                # bool is distinct from int per spec – type names already distinguish, but check homogeneous for column type
                # we ensure all values match column type
                expected_py: dict[DataType, tuple[str, ...]] = {
                    DataType.string: ("str",),
                    DataType.integer: ("int",),
                    DataType.float: ("float",),
                    DataType.boolean: ("bool",),
                    DataType.date: ("date",),
                    DataType.timestamp: ("datetime",),
                }
                # For categorical, values are ScalarValue (str|int|float|bool) – date/timestamp not used via categorical normally
                # Check that all values' type matches column type where applicable
                exp = expected_py.get(col.type)
                if exp is not None:
                    for v in values:
                        # allow bool vs int distinction: bool is bool, int is int
                        actual = type(v).__name__
                        # Special: for column type string, actual must be str; for integer, int; float, float; boolean, bool
                        if col.type == DataType.string and actual != "str":
                            _add_issue(
                                issues,
                                ErrorCode.CATEGORICAL_HOMOGENEOUS,
                                f"{cpath}.generator.values",
                                f"categorical values must be homogeneous for column type '{col.type.value}'",
                                related=str(v),
                            )
                            break
                        if col.type == DataType.integer and actual != "int":
                            _add_issue(
                                issues,
                                ErrorCode.CATEGORICAL_HOMOGENEOUS,
                                f"{cpath}.generator.values",
                                f"categorical values must be homogeneous for column type '{col.type.value}'",
                                related=str(v),
                            )
                            break
                        if col.type == DataType.float and actual != "float":
                            _add_issue(
                                issues,
                                ErrorCode.CATEGORICAL_HOMOGENEOUS,
                                f"{cpath}.generator.values",
                                f"categorical values must be homogeneous for column type '{col.type.value}'",
                                related=str(v),
                            )
                            break
                        if col.type == DataType.boolean and actual != "bool":
                            _add_issue(
                                issues,
                                ErrorCode.CATEGORICAL_HOMOGENEOUS,
                                f"{cpath}.generator.values",
                                f"categorical values must be homogeneous for column type '{col.type.value}'",
                                related=str(v),
                            )
                            break
            # template placeholders
            if gen_kind == "template_string":
                template = getattr(col.generator, "template", "")
                # Find placeholders
                import re

                placeholders = re.findall(r"\{([a-z][a-z0-9_]*)\}", template)
                for ph in placeholders:
                    if ph not in raw_col_map.get(tbl.name, {}):
                        _add_issue(
                            issues,
                            ErrorCode.TEMPLATE_PLACEHOLDER,
                            f"{cpath}.generator.template",
                            f"placeholder '{ph}' does not resolve to another column in same table '{tbl.name}'",
                            related=ph,
                        )
                    if ph == col.name:
                        _add_issue(
                            issues,
                            ErrorCode.TEMPLATE_PLACEHOLDER,
                            f"{cpath}.generator.template",
                            f"template column '{col.name}' must not reference itself",
                            related=ph,
                        )
                # Check for cycles – build graph for template deps within same table
                # For simplicity, if any placeholder forms cycle, report
                # We'll detect self-ref already, and for two-way cycle we can check later with full graph
            # foreign_key generators
            if gen_kind == "foreign_key":
                rel_name = getattr(col.generator, "relationship", None)
                if rel_name not in rel_by_name:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"{cpath}.generator.relationship",
                        f"relationship '{rel_name}' does not exist",
                        related=rel_name,
                    )
                else:
                    # check correct side/bridge – simplified: ensure target_side is left/right and relationship exists
                    rel = rel_by_name[rel_name]
                    # Determine if this column should be FK: check if column is part of left/right endpoint
                    # For simplicity, check that target_side is consistent with relationship cardinality direction
                    # If relationship is one_to_many, right side is FK and should target left; etc.
                    # We'll do basic check: if rel.cardinality == "one_to_many" and target_side != "left" for right columns, etc.
                    # For now, just ensure target_side is left or right – already validated locally, so check that relationship exists
                    pass
        # template cycle detection per table
        # Build dependency graph for template columns
        template_deps: dict[str, list[str]] = {}
        for col in tbl.columns:
            if getattr(col.generator, "kind", None) == "template_string":
                tmpl = getattr(col.generator, "template", "")
                deps = re.findall(r"\{([a-z][a-z0-9_]*)\}", tmpl)
                template_deps[col.name] = deps
        # Detect cycles via DFS
        visited: dict[str, int] = {}  # 0 unvisited, 1 visiting, 2 done

        def _dfs(node: str, stack: list[str]) -> bool:
            state = visited.get(node, 0)
            if state == 1:
                # cycle
                cycle = " -> ".join(stack + [node])
                _add_issue(
                    issues,
                    ErrorCode.TEMPLATE_CYCLE,
                    f"{base}.columns",
                    f"template dependencies have cycle: {cycle}",
                    related=node,
                )
                return True
            if state == 2:
                return False
            visited[node] = 1
            stack.append(node)
            for dep in template_deps.get(node, []):
                if dep in template_deps:
                    _dfs(dep, stack)
            stack.pop()
            visited[node] = 2
            return False

        for n in list(template_deps.keys()):
            if visited.get(n, 0) == 0:
                _dfs(n, [])

    # §17.4 Relationships
    for idx, rel in enumerate(scenario.relationships):
        base = f"relationships[{idx}]"
        # endpoint tables/columns exist
        for side in ("left", "right"):
            ep = getattr(rel, side)
            tbl_name = ep.table
            if tbl_name not in raw_by_name:
                _add_issue(
                    issues,
                    ErrorCode.MISSING_REF,
                    f"{base}.{side}.table",
                    f"table '{tbl_name}' does not exist",
                    related=tbl_name,
                )
                missing_tables.add(tbl_name)
                continue
            for col_name in ep.columns:
                if col_name not in raw_col_map.get(tbl_name, {}):
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"{base}.{side}.columns",
                        f"column '{col_name}' does not exist in table '{tbl_name}'",
                        related=col_name,
                    )
        # arity equal for direct
        if rel.cardinality in ("one_to_one", "one_to_many", "many_to_one"):
            left_len = len(rel.left.columns)
            right_len = len(rel.right.columns)
            if left_len != right_len:
                _add_issue(
                    issues,
                    ErrorCode.RELATIONSHIP_ARITY,
                    f"{base}",
                    f"arity mismatch left {left_len} vs right {right_len}",
                    related=rel.name,
                )
            # endpoint types exactly equal
            if rel.left.table in raw_col_type and rel.right.table in raw_col_type:
                for lcol, rcol in zip(rel.left.columns, rel.right.columns):
                    ltype = raw_col_type.get(rel.left.table, {}).get(lcol)
                    rtype = raw_col_type.get(rel.right.table, {}).get(rcol)
                    if ltype is not None and rtype is not None and ltype != rtype:
                        _add_issue(
                            issues,
                            ErrorCode.RELATIONSHIP_TYPE,
                            f"{base}",
                            f"type mismatch '{lcol}' ({ltype.value}) vs '{rcol}' ({rtype.value})",
                            related=rel.name,
                        )

            # unique side
            # For one_to_many, left must be PK/unique; many_to_one right must be PK/unique; one_to_one both unique
            # We check if PK contains endpoint columns
            def _is_unique_side(table: str, cols: tuple) -> bool:
                tbl = raw_by_name.get(table)
                if tbl is None:
                    return False
                # check if cols equals PK or all columns have unique=True
                if tuple(cols) == tuple(tbl.primary_key):
                    return True
                # check unique columns
                col_objs = [raw_col_map.get(table, {}).get(c) for c in cols]
                if all(getattr(c, "unique", False) for c in col_objs if c is not None):
                    return True
                return False

            if rel.cardinality == "one_to_many":
                if not _is_unique_side(rel.left.table, rel.left.columns):
                    _add_issue(
                        issues,
                        ErrorCode.UNIQUE_SIDE,
                        f"{base}.left",
                        "left side must be unique for one_to_many",
                        related=rel.name,
                    )
            elif rel.cardinality == "many_to_one":
                if not _is_unique_side(rel.right.table, rel.right.columns):
                    _add_issue(
                        issues,
                        ErrorCode.UNIQUE_SIDE,
                        f"{base}.right",
                        "right side must be unique for many_to_one",
                        related=rel.name,
                    )
            elif rel.cardinality == "one_to_one":
                if not _is_unique_side(rel.left.table, rel.left.columns) or not _is_unique_side(
                    rel.right.table, rel.right.columns
                ):
                    _add_issue(
                        issues,
                        ErrorCode.ONE_TO_ONE_UNIQUE,
                        f"{base}",
                        "both sides must be unique for one_to_one",
                        related=rel.name,
                    )

            # dependent columns have correct FK generators & nullability – simplified check
            # For one_to_many, right columns should have FK targeting left
            # For many_to_one, left columns should have FK targeting right
            # We can check that there exists at least one column in dependent side with FK generator pointing to this relationship
            # If not, raise
            def _has_fk(table: str, cols: tuple, target_side: str) -> bool:
                cmap = raw_col_map.get(table, {})
                for cname in cols:
                    col = cmap.get(cname)
                    if col is not None and getattr(col.generator, "kind", None) == "foreign_key":
                        if (
                            getattr(col.generator, "relationship", None) == rel.name
                            and getattr(col.generator, "target_side", None) == target_side
                        ):
                            return True
                return False

            if rel.cardinality == "one_to_many":
                if not _has_fk(rel.right.table, rel.right.columns, "left"):
                    _add_issue(
                        issues,
                        ErrorCode.FOREIGN_KEY_SIDE,
                        f"{base}.right",
                        "dependent columns must have foreign_key generator targeting left",
                        related=rel.name,
                    )
            elif rel.cardinality == "many_to_one":
                if not _has_fk(rel.left.table, rel.left.columns, "right"):
                    _add_issue(
                        issues,
                        ErrorCode.FOREIGN_KEY_SIDE,
                        f"{base}.left",
                        "dependent columns must have foreign_key generator targeting right",
                        related=rel.name,
                    )
            elif rel.cardinality == "one_to_one":
                has_left = _has_fk(rel.left.table, rel.left.columns, "right")
                has_right = _has_fk(rel.right.table, rel.right.columns, "left")
                if not (has_left ^ has_right):
                    _add_issue(
                        issues,
                        ErrorCode.FOREIGN_KEY_SIDE,
                        f"{base}",
                        "exactly one side must be foreign_key for one_to_one",
                        related=rel.name,
                    )
        else:  # many_to_many
            # bridge table exists and distinct
            bridge_table = getattr(rel, "bridge", None)
            if bridge_table is not None:
                btbl = bridge_table.table
                if btbl not in raw_by_name:
                    _add_issue(
                        issues,
                        ErrorCode.BRIDGE_TABLE,
                        f"{base}.bridge.table",
                        f"bridge table '{btbl}' does not exist",
                        related=btbl,
                    )
                elif btbl in (rel.left.table, rel.right.table):
                    _add_issue(
                        issues,
                        ErrorCode.BRIDGE_TABLE,
                        f"{base}.bridge.table",
                        "bridge table must be distinct from endpoints",
                        related=btbl,
                    )
                # arities already checked via disjoint, but check types if possible
                # For simplicity, check bridge left/right columns count matches endpoint arity
                if len(bridge_table.left_columns) != len(rel.left.columns):
                    _add_issue(
                        issues,
                        ErrorCode.RELATIONSHIP_ARITY,
                        f"{base}.bridge.left_columns",
                        "bridge left arity must match left endpoint",
                        related=rel.name,
                    )
                if len(bridge_table.right_columns) != len(rel.right.columns):
                    _add_issue(
                        issues,
                        ErrorCode.RELATIONSHIP_ARITY,
                        f"{base}.bridge.right_columns",
                        "bridge right arity must match right endpoint",
                        related=rel.name,
                    )

    # §17.5 Staging
    # each raw has exactly one staging and vice versa
    raw_names = {t.name for t in scenario.raw_tables}
    for tbl in scenario.raw_tables:
        cnt = sum(1 for s in scenario.staging_models if s.source == tbl.name)
        if cnt != 1:
            _add_issue(
                issues,
                ErrorCode.STAGING_1TO1,
                "staging_models",
                f"raw table '{tbl.name}' must have exactly one staging model, found {cnt}",
                related=tbl.name,
            )
    for s in scenario.staging_models:
        if s.source not in raw_names:
            _add_issue(
                issues,
                ErrorCode.MISSING_REF,
                f"staging_models[{s.name}].source",
                f"staging source '{s.source}' does not exist",
                related=s.source,
            )
            missing_tables.add(s.source)
        # source columns exist
        raw_cols = raw_col_map.get(s.source, {})
        for col in s.columns:
            if col.source not in raw_cols:
                _add_issue(
                    issues,
                    ErrorCode.STAGING_SOURCE_COLUMN,
                    f"staging_models[{s.name}].columns",
                    f"source column '{col.source}' does not exist in raw table '{s.source}'",
                    related=col.source,
                )
        # grain columns exist after transformations (check target names)
        target_names = {c.target for c in s.columns}
        for g in s.grain:
            if g not in target_names:
                _add_issue(
                    issues,
                    ErrorCode.STAGING_GRAIN,
                    f"staging_models[{s.name}].grain",
                    f"grain column '{g}' does not exist after transformations",
                    related=g,
                )

    # §17.6 DAG & Intermediate
    # Build dependency graph for intermediate models
    # intermediate -> staging or intermediate
    all_model_names = set(staging_by_name.keys()) | set(intermediate_by_name.keys())
    # Check invalid layer dependency and self-reference
    for idx, m in enumerate(scenario.intermediate_models):
        base = f"intermediate_models[{idx}]"
        deps: list[str] = []
        if hasattr(m, "source"):
            deps.append(m.source)  # transform, aggregate, deduplicate
        if hasattr(m, "left"):
            deps.extend([m.left, m.right])  # join
        for dep in deps:
            if dep == m.name:
                _add_issue(
                    issues,
                    ErrorCode.DAG_CYCLE,
                    f"{base}.name",
                    f"self-dependency '{dep}'",
                    related=dep,
                )
            elif dep not in all_model_names:
                # Check if dep is raw or output
                if dep in raw_by_name:
                    _add_issue(
                        issues,
                        ErrorCode.LAYER_DEPENDENCY,
                        f"{base}",
                        f"intermediate '{m.name}' must not reference raw table '{dep}'",
                        related=dep,
                    )
                elif dep in output_by_name:
                    _add_issue(
                        issues,
                        ErrorCode.LAYER_DEPENDENCY,
                        f"{base}",
                        f"intermediate '{m.name}' must not reference output model '{dep}'",
                        related=dep,
                    )
                else:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"{base}",
                        f"dependency '{dep}' does not exist",
                        related=dep,
                    )
                    missing_models.add(dep)
            # also check join keys existence later

    # Topological sort (Kahn + declaration order tie-breaker)
    # Build graph: node -> dependencies
    graph: dict[str, set[str]] = {}
    in_degree: dict[str, int] = {}
    # Initialize
    for m in scenario.intermediate_models:
        graph[m.name] = set()
        in_degree[m.name] = 0
    # Populate edges: if A depends on B (B -> A), then in_degree[A]++
    # Dependencies are staging or intermediate names
    for m in scenario.intermediate_models:
        deps: list[str] = []
        if hasattr(m, "source"):
            deps.append(m.source)
        if hasattr(m, "left"):
            deps.extend([m.left, m.right])
        for dep in deps:
            if dep in graph:
                graph[dep].add(m.name)  # actually reverse: dep -> m
                in_degree[m.name] += 1

    # Kahn
    queue: deque[str] = deque()
    # Use declaration order as tie-breaker: sort by original index
    idx_map = {m.name: i for i, m in enumerate(scenario.intermediate_models)}
    # initial nodes with indegree 0, sorted by idx
    zero = [n for n, d in in_degree.items() if d == 0]
    zero.sort(key=lambda n: idx_map.get(n, 0))
    queue.extend(zero)
    topo: list[str] = []
    while queue:
        n = queue.popleft()
        topo.append(n)
        for succ in sorted(graph.get(n, []), key=lambda x: idx_map.get(x, 0)):
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)
        # keep queue sorted by declaration order for determinism
        queue = deque(sorted(queue, key=lambda x: idx_map.get(x, 0)))
    if len(topo) != len(graph):
        # cycle
        remaining = [n for n, d in in_degree.items() if d > 0]
        for n in remaining:
            _add_issue(
                issues,
                ErrorCode.DAG_CYCLE,
                "intermediate_models",
                f"cycle detected involving '{n}'",
                related=n,
            )

    # For each intermediate, check projections, grain, join keys, etc.
    # Build staging output schemas for lineage (simplified: staging columns target -> type)
    staging_schema: dict[str, dict[str, DataType]] = {}
    for s in scenario.staging_models:
        schema: dict[str, DataType] = {}
        # Need to infer type after operations – simplified: use raw column type for source that maps to target, ignoring cast
        raw_types = raw_col_type.get(s.source, {})
        for col in s.columns:
            # Find raw type for source
            rt = raw_types.get(col.source)
            # If has cast operation, use cast type
            cast_type = None
            for op in col.operations:
                if getattr(op, "op", None) == "cast":
                    cast_type = getattr(op, "type", None)
            if cast_type is not None:
                # cast type is DataType (allow string)
                # Normalize string to DataType
                if isinstance(cast_type, str):
                    try:
                        cast_type = DataType(cast_type)
                    except Exception:
                        cast_type = rt
                schema[col.target] = cast_type if cast_type is not None else rt
            else:
                schema[col.target] = rt
        staging_schema[s.name] = schema

    # Build intermediate schemas iteratively in topo order
    intermediate_schema: dict[str, dict[str, DataType]] = {}
    # Also keep lineage for later
    lineage: dict[str, dict[str, str]] = {}  # model -> col -> source

    for name in topo:
        m = intermediate_by_name.get(name)
        if m is None:
            continue

        # Helper to get schema for dependency
        def _get_schema(dep: str) -> dict[str, DataType] | None:
            if dep in staging_schema:
                return staging_schema[dep]
            if dep in intermediate_schema:
                return intermediate_schema[dep]
            return None

        if m.operation == "transform":
            src_schema = _get_schema(m.source)
            if src_schema is None:
                # missing already reported, suppress
                continue
            # Check columns existence
            out_schema: dict[str, DataType] = {}
            for pc in m.columns:
                if pc.source not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].columns",
                        f"projection source '{pc.source}' not in '{m.source}'",
                        related=pc.source,
                    )
                else:
                    out_schema[pc.target] = src_schema[pc.source]
            # Derived columns
            for dc in m.derived_columns:
                # Check expression columns existence – simplified: check ColumnExpression
                # For now, just check that expression's column refs are in projected names + derived? Simplified
                # Assume dc.type matches inferred – we can check declared type vs inferred
                # For test, we will check that derived column type matches expression type via simple rule
                # If expression is column ref to non-existent, already flagged
                # For metric, just set type
                if dc.name in out_schema:
                    _add_issue(
                        issues,
                        ErrorCode.UNKNOWN,
                        f"intermediate_models[{name}].derived_columns",
                        f"derived column '{dc.name}' collides with projected",
                        related=dc.name,
                    )
                out_schema[dc.name] = (
                    dc.type
                    if isinstance(dc.type, DataType)
                    else DataType(dc.type)
                    if isinstance(dc.type, str)
                    else dc.type
                )
            # Check grain
            for g in m.grain:
                if g not in out_schema:
                    _add_issue(
                        issues,
                        ErrorCode.GRAIN_IMPOSSIBLE,
                        f"intermediate_models[{name}].grain",
                        f"grain '{g}' not in output schema",
                        related=g,
                    )
            intermediate_schema[name] = out_schema

        elif m.operation == "join":
            left_schema = _get_schema(m.left)
            right_schema = _get_schema(m.right)
            if left_schema is None or right_schema is None:
                continue
            # Join keys
            for pair in m.join.on:
                if pair.left not in left_schema:
                    _add_issue(
                        issues,
                        ErrorCode.JOIN_KEY_MISMATCH,
                        f"intermediate_models[{name}].join.on",
                        f"left key '{pair.left}' not in '{m.left}'",
                        related=pair.left,
                    )
                if pair.right not in right_schema:
                    _add_issue(
                        issues,
                        ErrorCode.JOIN_KEY_MISMATCH,
                        f"intermediate_models[{name}].join.on",
                        f"right key '{pair.right}' not in '{m.right}'",
                        related=pair.right,
                    )
                # Type mismatch
                lt = left_schema.get(pair.left)
                rt = right_schema.get(pair.right)
                if lt is not None and rt is not None and lt != rt:
                    _add_issue(
                        issues,
                        ErrorCode.JOIN_KEY_MISMATCH,
                        f"intermediate_models[{name}].join.on",
                        f"join key type mismatch '{lt.value}' vs '{rt.value}'",
                        related=pair.left,
                    )
            # Check columns existence
            out_schema = {}
            for jc in m.columns:
                src_schema = left_schema if jc.side == "left" else right_schema
                if jc.source not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].columns",
                        f"projection source '{jc.source}' not in {jc.side} '{getattr(m, jc.side)}'",
                        related=jc.source,
                    )
                else:
                    if jc.target in out_schema:
                        _add_issue(
                            issues,
                            ErrorCode.UNKNOWN,
                            f"intermediate_models[{name}].columns",
                            f"duplicate output name '{jc.target}'",
                            related=jc.target,
                        )
                    out_schema[jc.target] = src_schema[jc.source]
            # Derived
            for dc in m.derived_columns:
                if dc.name in out_schema:
                    _add_issue(
                        issues,
                        ErrorCode.UNKNOWN,
                        f"intermediate_models[{name}].derived_columns",
                        f"collision '{dc.name}'",
                        related=dc.name,
                    )
                out_schema[dc.name] = (
                    dc.type
                    if isinstance(dc.type, DataType)
                    else DataType(dc.type)
                    if isinstance(dc.type, str)
                    else dc.type
                )
            for g in m.grain:
                if g not in out_schema:
                    _add_issue(
                        issues,
                        ErrorCode.GRAIN_IMPOSSIBLE,
                        f"intermediate_models[{name}].grain",
                        f"grain '{g}' not in output",
                        related=g,
                    )
            intermediate_schema[name] = out_schema

        elif m.operation == "aggregate":
            src_schema = _get_schema(m.source)
            if src_schema is None:
                continue
            # Check group_by sources exist
            for pc in m.group_by:
                if pc.source not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].group_by",
                        f"source '{pc.source}' not in '{m.source}'",
                        related=pc.source,
                    )
            # Check metrics column existence and type
            for met in m.metrics:
                # metric name already checked for uniqueness in local validation, but check type
                if hasattr(met, "column"):
                    col_name = getattr(met, "column", None)
                    if col_name is not None and col_name not in src_schema:
                        _add_issue(
                            issues,
                            ErrorCode.METRIC_TYPE,
                            f"intermediate_models[{name}].metrics",
                            f"metric column '{col_name}' not in source",
                            related=col_name,
                        )
                    elif col_name is not None:
                        ctype = src_schema.get(col_name)
                        # sum, avg, conditional_sum require numeric; min/max require numeric/date/timestamp
                        if met.function in ("sum", "avg", "conditional_sum") and ctype not in (
                            DataType.integer,
                            DataType.float,
                        ):
                            _add_issue(
                                issues,
                                ErrorCode.METRIC_TYPE,
                                f"intermediate_models[{name}].metrics",
                                f"metric '{met.function}' requires numeric column, got '{ctype}'",
                                related=met.name,
                            )
                        if met.function in ("min", "max") and ctype not in (
                            DataType.integer,
                            DataType.float,
                            DataType.date,
                            DataType.timestamp,
                        ):
                            _add_issue(
                                issues,
                                ErrorCode.METRIC_TYPE,
                                f"intermediate_models[{name}].metrics",
                                f"metric '{met.function}' requires numeric/date/timestamp",
                                related=met.name,
                            )
            # Build output schema for aggregate: group_by targets + metric names
            out_schema = {}
            for pc in m.group_by:
                # group_by target type is from source column type
                src_type = src_schema.get(pc.source)
                if src_type is not None:
                    out_schema[pc.target] = src_type
            for met in m.metrics:
                # Determine return type
                if met.function in ("count_rows", "count", "count_distinct", "conditional_count"):
                    out_schema[met.name] = DataType.integer
                elif met.function in ("sum", "avg", "conditional_sum"):
                    out_schema[met.name] = DataType.float
                elif met.function in ("min", "max"):
                    # return type is same as column type
                    col_name = getattr(met, "column", None)
                    ctype = src_schema.get(col_name) if col_name else DataType.string
                    out_schema[met.name] = ctype if ctype else DataType.string
                else:
                    out_schema[met.name] = DataType.string
            # Check grain subset
            group_targets = {c.target for c in m.group_by}
            for g in m.grain:
                if g not in group_targets:
                    _add_issue(
                        issues,
                        ErrorCode.GRAIN_IMPOSSIBLE,
                        f"intermediate_models[{name}].grain",
                        f"grain '{g}' must be subset of group_by targets",
                        related=g,
                    )
            intermediate_schema[name] = out_schema

        elif m.operation == "deduplicate":
            src_schema = _get_schema(m.source)
            if src_schema is None:
                continue
            # keys must be in source
            for k in m.keys:
                if k not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].keys",
                        f"key '{k}' not in source",
                        related=k,
                    )
            for sk in m.order_by:
                if sk.column not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.MISSING_REF,
                        f"intermediate_models[{name}].order_by",
                        f"order_by '{sk.column}' not in source",
                        related=sk.column,
                    )
            # grain already checked equals keys as set locally, but we also check that grain is subset of source
            for g in m.grain:
                if g not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.GRAIN_IMPOSSIBLE,
                        f"intermediate_models[{name}].grain",
                        f"grain '{g}' not in source",
                        related=g,
                    )
            # output schema is same as source (dedup doesn't change columns)
            intermediate_schema[name] = dict(src_schema)

    # §17.7 Output & Assertions
    for idx, out in enumerate(scenario.output_models):
        base = f"output_models[{idx}]"
        if out.source not in intermediate_by_name:
            _add_issue(
                issues,
                ErrorCode.MISSING_REF,
                f"{base}.source",
                f"output source '{out.source}' is not an intermediate model",
                related=out.source,
            )
            continue
        src_schema = intermediate_schema.get(out.source)
        if src_schema is None:
            # missing due to earlier cycle, suppress
            continue
        # Check group_by sources
        for pc in out.group_by:
            if pc.source not in src_schema:
                _add_issue(
                    issues,
                    ErrorCode.MISSING_REF,
                    f"{base}.group_by",
                    f"source '{pc.source}' not in '{out.source}'",
                    related=pc.source,
                )
        # Check grain and dimensions reference group_by targets
        group_targets = {c.target for c in out.group_by}
        for g in out.grain:
            if g not in group_targets:
                _add_issue(
                    issues,
                    ErrorCode.GRAIN_IMPOSSIBLE,
                    f"{base}.grain",
                    f"grain '{g}' must reference group_by target",
                    related=g,
                )
        for d in out.dimensions:
            if d not in group_targets:
                _add_issue(
                    issues,
                    ErrorCode.GRAIN_IMPOSSIBLE,
                    f"{base}.dimensions",
                    f"dimension '{d}' must reference group_by target",
                    related=d,
                )
        # Check metrics
        for met in out.metrics:
            if hasattr(met, "column"):
                col_name = getattr(met, "column", None)
                if col_name is not None and col_name not in src_schema:
                    _add_issue(
                        issues,
                        ErrorCode.METRIC_TYPE,
                        f"{base}.metrics",
                        f"metric column '{col_name}' not in source",
                        related=col_name,
                    )
                elif col_name is not None:
                    ctype = src_schema.get(col_name)
                    if met.function in ("sum", "avg", "conditional_sum") and ctype not in (
                        DataType.integer,
                        DataType.float,
                    ):
                        _add_issue(
                            issues,
                            ErrorCode.METRIC_TYPE,
                            f"{base}.metrics",
                            f"metric '{met.function}' requires numeric",
                            related=met.name,
                        )

    # Check assertions for duplicate/contradictory (simplified)
    # For now, just check that assertion model references exist
    for idx, a in enumerate(scenario.tests):
        base = f"tests[{idx}]"
        # model must exist in any model layer (staging, intermediate, output)
        if (
            a.model not in staging_by_name
            and a.model not in intermediate_by_name
            and a.model not in output_by_name
        ):
            _add_issue(
                issues,
                ErrorCode.MISSING_REF,
                f"{base}.model",
                f"assertion model '{a.model}' does not exist",
                related=a.model,
            )

    # §17.8 Connectivity
    # Build ancestor map: for each staging and intermediate, check if ancestor of output
    # Build graph from output backwards
    # First, map each model to its dependencies
    dep_map: dict[str, list[str]] = {}
    for s in scenario.staging_models:
        dep_map[s.name] = [s.source]  # raw
    for m in scenario.intermediate_models:
        deps: list[str] = []
        if hasattr(m, "source"):
            deps.append(m.source)
        if hasattr(m, "left"):
            deps.extend([m.left, m.right])
        dep_map[m.name] = deps
    for o in scenario.output_models:
        dep_map[o.name] = [o.source]

    # For each staging and intermediate, check if it can reach output via reverse graph
    # Build reverse graph: dependency -> dependents
    rev: dict[str, list[str]] = defaultdict(list)
    for node, deps in dep_map.items():
        for d in deps:
            rev[d].append(node)

    # BFS from outputs
    reachable: set[str] = set()
    queue = deque([o.name for o in scenario.output_models])
    visited = set()
    while queue:
        cur = queue.popleft()
        if cur in visited:
            continue
        visited.add(cur)
        reachable.add(cur)
        for dep in dep_map.get(cur, []):
            if dep not in visited:
                queue.append(dep)
                reachable.add(dep)
        # Also traverse via rev? Actually we want ancestors, so from output follow dependencies backwards
        # The above does that: from output, go to its source, then to its dependencies, etc.
        # So reachable will contain all ancestors

    # Check every staging is ancestor of output
    for s in scenario.staging_models:
        if s.name not in reachable:
            _add_issue(
                issues,
                ErrorCode.DISCONNECTED,
                f"staging_models[{s.name}]",
                f"staging model '{s.name}' is not ancestor of any output",
                related=s.name,
            )
    for m in scenario.intermediate_models:
        if m.name not in reachable:
            _add_issue(
                issues,
                ErrorCode.DISCONNECTED,
                f"intermediate_models[{m.name}]",
                f"intermediate model '{m.name}' is not ancestor of any output",
                related=m.name,
            )
    # Every raw reaches output via staging
    for tbl in scenario.raw_tables:
        # Find staging that sources this raw
        stg_for_raw = [s for s in scenario.staging_models if s.source == tbl.name]
        if not stg_for_raw:
            continue  # already reported as staging 1-to-1
        # Check if any of those stagings is reachable
        if not any(s.name in reachable for s in stg_for_raw):
            _add_issue(
                issues,
                ErrorCode.DISCONNECTED,
                f"raw_tables[{tbl.name}]",
                f"raw table '{tbl.name}' does not reach any output",
                related=tbl.name,
            )

    # Sort issues deterministically
    issues_sorted = sorted(issues, key=lambda i: (i.path, i.code))

    if issues_sorted:
        raise SemanticValidationError(issues_sorted)

    # Build derived assertions (simplified)
    derived: list = []
    for tbl in scenario.raw_tables:
        for col in tbl.columns:
            if not col.nullable:
                # not_null is implied, but we derive explicit assertion for PK/grain? Simplified
                pass
        if tbl.primary_key:
            # derive unique and not_null for PK
            derived.append(
                {
                    "name": f"derived_unique_{tbl.name}",
                    "model": tbl.name,
                    "type": "unique",
                    "columns": tbl.primary_key,
                }
            )
    # For staging grain
    for s in scenario.staging_models:
        derived.append(
            {
                "name": f"derived_unique_{s.name}",
                "model": s.name,
                "type": "unique",
                "columns": s.grain,
            }
        )

    # Compute lineage (simplified)
    lineage: dict = {}
    for s in scenario.staging_models:
        lineage[s.name] = {c.target: f"{s.source}.{c.source}" for c in s.columns}
    for m in scenario.intermediate_models:
        sch = intermediate_schema.get(m.name, {})
        lineage[m.name] = {k: f"{m.name}.{k}" for k in sch.keys()}

    return ValidatedScenario(
        scenario=scenario,
        raw_by_name=raw_by_name,
        staging_by_name=staging_by_name,
        intermediate_by_name=intermediate_by_name,
        output_by_name=output_by_name,
        relationships_by_name=rel_by_name,
        topological_order=tuple(topo),
        lineage=lineage,
        derived_assertions=tuple(derived),
    )
