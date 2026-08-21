# Scenario Language and Pydantic Contract Specification

Status: draft.

## 1. Purpose

This document defines version 1 of the declarative scenario language used to describe a healthy synthetic data pipeline. It is the normative specification for:

- the Pydantic model hierarchy;
- the source-file topology of the scenario package;
- JSON field names, types, defaults, and discriminators;
- structural and local validation performed during Pydantic parsing;
- cross-object invariants performed by a separate semantic validator;
- canonical serialization and generated JSON Schema; and
- tests required for the contract implementation.

A scenario is a small declarative program. Pydantic defines its grammar and local static rules; the semantic validator resolves its names, types, lineage, DAG, keys, grain, and assertions. A scenario compiler may consume a scenario only after both validation stages succeed.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 2. Scope and boundaries

Version 1 describes a four-layer pipeline:

```text
raw -> staging -> intermediate -> output
```

Every scenario MUST contain:

- 3–4 raw tables;
- exactly one staging model for each raw table;
- 2–3 intermediate models; and
- 1–2 output models.

The language covers raw column generation, raw relationships, source-oriented staging transformations, relational and business transformations in the intermediate layer, output aggregations, model grain, and healthy assertions.

This document does not define:

- how an LLM or human chooses a useful domain or composes a diverse scenario;
- the runtime algorithm, probability distribution, or random-stream allocation of a mini-generator;
- SQL, YAML, Parquet, DuckDB, or dbt rendering details;
- generated project layout;
- fault definitions or fault injection;
- diagnostic tools, oracle behavior, trajectories, training, or evaluation; or
- arbitrary user-provided Python, SQL, Jinja, regular expressions, or templating code.

Those responsibilities belong to `SCENARIO_AUTHORING.md`, `GENERATOR_SPEC.md`, and the downstream specifications identified in `PIPELINE_SPEC.md`.

## 3. Contract principles

### 3.1. Pydantic is the executable source of truth

The implementation MUST use Pydantic 2.x models as the executable scenario contract. A JSON Schema MAY be published for editors, LLM tooling, or authoring workflows, but it MUST be generated from the root Pydantic model. A separately maintained handwritten JSON Schema is prohibited.

The public root model MUST be named `Scenario`. The same validated scenario is consumed by the raw-data generator, dbt-project generator, manifest builder, and later infrastructure.

### 3.2. Strict, closed, immutable input

Every contract model MUST inherit from a common `ContractModel` with behavior equivalent to:

```python
ConfigDict(
    extra="forbid",
    strict=True,
    frozen=True,
    validate_default=True,
    allow_inf_nan=False,
)
```

Consequently:

- unknown JSON fields MUST fail validation;
- scalar coercion such as `"10"` to `10` MUST NOT occur;
- NaN and positive or negative infinity MUST be rejected;
- defaults MUST themselves be validated;
- parsed contract objects MUST not be mutated in place; and
- validators MUST reject bad input rather than silently repair, trim, normalize, sort, or deduplicate it.

The canonical entry point MUST validate JSON directly with strict behavior. Internal code MUST NOT use `model_construct()` or another validation-bypassing path for untrusted scenario content.

Tuple-typed fields SHOULD be used for ordered JSON arrays so the parsed representation is not mutated accidentally. JSON serialization still emits arrays.

### 3.3. Closed vocabulary and explicit dispatch

Every sum type MUST be an `Annotated` discriminated union. The discriminator names are fixed:

| Union | Discriminator |
| --- | --- |
| mini-generators | `kind` |
| relationships | `cardinality` |
| staging column operations | `op` |
| staging row operations | `op` |
| expressions | `kind` |
| conditions | `kind` |
| intermediate models | `operation` |
| metrics | `function` |
| healthy assertions | `type` |

Dispatch by class name, trial parsing of an untagged union, a registry populated by imports, or a free-form operation name is prohibited in version 1.

### 3.4. No arbitrary executable fragments

No model may contain fields named or interpreted as `sql`, `jinja`, `python`, `code`, `expression_sql`, or an equivalent executable escape hatch. Expressions and conditions MUST be composed only from the structured nodes defined here.

### 3.5. Scenario identity is distinct from data variation

`data_seed` MUST NOT be a field of `Scenario`. The identity relation is:

```text
scenario.json + data_seed + compiler/runtime versions = pipeline instance
```

Changing only `data_seed` creates a new instance of the same scenario, not a new scenario.

## 4. Implementation topology

### 4.1. Model dependency topology

The model graph MUST remain layered and acyclic:

```text
ContractModel and constrained scalar types
        |
        +-- expressions and conditions
        +-- mini-generators
        +-- raw tables
        +-- relationships
        +-- staging models
        +-- joins and intermediate models
        +-- metrics and output models
        +-- healthy assertions
                    |
                    v
                 Scenario
                    |
                    v
          semantic validation result
```

Leaf modules MUST NOT import the root `Scenario` model. The semantic validator may import all model modules; contract modules MUST NOT import the semantic validator or compiler.

### 4.2. Required source-file topology

The implementation SHOULD use the following package layout:

```text
src/data_pipeline_diagnostics/scenario/
├── __init__.py
├── base.py
├── types.py
├── expressions.py
├── generators.py
├── raw.py
├── relationships.py
├── staging.py
├── intermediate.py
├── output.py
├── assertions.py
├── models.py
├── semantic.py
├── errors.py
└── json_schema.py
```

Responsibilities are fixed as follows:

| File | Responsibility |
| --- | --- |
| `base.py` | `ContractModel` only. |
| `types.py` | Constrained scalar aliases, string enums, references, row counts, sort keys, and other dependency-free shared values. |
| `expressions.py` | Structured scalar expressions and boolean conditions. |
| `generators.py` | Concrete mini-generator models and the `GeneratorSpec` discriminated union. |
| `raw.py` | `RawColumn` and `RawTable`. |
| `relationships.py` | Relationship endpoints, bridge references, concrete relationship variants, and the relationship union. |
| `staging.py` | Staging column mappings, column operations, row operations, and `StagingModel`. |
| `intermediate.py` | Projection types, join types, concrete intermediate-model variants, and their union. |
| `output.py` | Metrics and `OutputModel`. |
| `assertions.py` | Explicit healthy-assertion models. It MUST NOT contain pytest tests. |
| `models.py` | The root `Scenario` composition only; it MUST NOT accumulate graph validation or compiler behavior. |
| `semantic.py` | Cross-object validation, symbol tables, schema/lineage resolution, and the validated-result wrapper. |
| `errors.py` | Stable project-level parse and semantic issue types. |
| `json_schema.py` | Deterministic export of JSON Schema from `Scenario`. |
| `__init__.py` | Deliberate public exports only: `Scenario`, parsing helpers, semantic validation entry point, result and error types. |

The exact split MAY change if implementation pressure justifies it, but the dependency direction and responsibility boundaries MUST remain intact. In particular, a single monolithic `models.py` containing all models and global validators is non-conforming.

### 4.3. Required test-file topology

Contract tests SHOULD use:

```text
tests/scenario/
├── test_base_contract.py
├── test_generators.py
├── test_raw.py
├── test_relationships.py
├── test_expressions.py
├── test_staging.py
├── test_intermediate.py
├── test_output.py
├── test_assertions.py
├── test_scenario.py
├── test_semantic.py
├── test_serialization.py
└── fixtures/
    ├── valid/
    └── invalid/
```

Tests for local Pydantic validators MUST be separate from semantic-validator tests even when both reject similar malformed scenarios.

## 5. Shared scalar types and references

### 5.1. Identifiers

`Identifier` MUST be a strict string matching:

```text
^[a-z][a-z0-9_]*$
```

Its length MUST be between 1 and 63 characters. It is used for table, model, column, relationship, metric, and assertion names. Names are already normalized identifiers; validators MUST NOT lowercase or otherwise rewrite them.

`ScenarioId` MUST use the same character vocabulary, with a maximum length of 100. `DomainName` MUST be an `Identifier`. The set of useful domains is authoring policy and MUST NOT be a Pydantic enum.

Optional human-readable `description` fields MUST be strict strings between 1 and 500 characters when present. Descriptions are metadata and MUST NOT affect compilation.

### 5.2. Scalar data types

`DataType` MUST be a string enum with exactly these version-1 values:

```text
string
integer
float
boolean
date
timestamp
```

Decimal precision/scale, binary values, arrays, structs, JSON values, and timezone-specific warehouse types are out of scope for version 1.

### 5.3. JSON scalar values

`ScalarValue` is the strict union `str | int | float | bool`. `None` is not a scalar value in the DSL; null production is controlled by `RawColumn.null_probability`. Floats MUST be finite.

The semantic validator MUST check scalar values against their contextual `DataType`. Because `bool` is a subclass of `int` in Python, boolean values MUST NOT be accepted as integer or float literals.

### 5.4. Probability and bounded collections

`Probability` MUST be a finite float in the inclusive range `[0.0, 1.0]`. Integers MUST NOT be coerced to probabilities in strict mode.

Every field described as non-empty below MUST have a Pydantic length constraint, not only a semantic check.

### 5.5. References

Cross-object references MUST be stored by stable names, never by array index or object identity.

`RelationshipEndpoint` has:

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `table` | `Identifier` | yes | Raw-table name. |
| `columns` | non-empty tuple of `Identifier` | yes | No duplicates; order defines composite-key component order. |

`SortKey` has:

| Field | Type | Required | Default |
| --- | --- | --- | --- |
| `column` | `Identifier` | yes | — |
| `direction` | `asc | desc` | no | `asc` |

## 6. Root `Scenario` model

`Scenario` is the sole public root model.

| Field | Type | Required | Default and constraints |
| --- | --- | --- | --- |
| `schema_version` | literal string `"1.0"` | yes | No numeric versions and no coercion. |
| `scenario_id` | `ScenarioId` | yes | — |
| `domain` | `DomainName` | yes | Metadata only; it MUST NOT select hidden behavior. |
| `description` | optional description | no | `None`. |
| `raw_tables` | tuple of `RawTable` | yes | 3–4 items. |
| `relationships` | non-empty tuple of `RelationshipSpec` | yes | Semantic validation determines whether the declarations connect the complete pipeline. |
| `staging_models` | tuple of `StagingModel` | yes | 3–4 items. |
| `intermediate_models` | tuple of `IntermediateModel` | yes | 2–3 items. |
| `output_models` | tuple of `OutputModel` | yes | 1–2 items. |
| `tests` | tuple of `HealthyAssertion` | no | Empty tuple. Automatically derived tests are not repeated here. |

Pydantic MUST enforce the version literal and collection bounds. It MUST NOT resolve names, determine connectivity, or validate the DAG while constructing `Scenario`.

The root model MUST NOT contain compiler settings, output paths, environment names, DuckDB paths, dbt profiles, seeds, fault metadata, expected fault labels, or diagnostic configuration.

## 7. Raw-data models

### 7.1. `RowCount`

`RowCount` declares the allowed instance size of one raw table:

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `min` | strict positive integer | yes | At least 1. |
| `max` | strict positive integer | yes | `max >= min`. |

An exact row count is represented by equal `min` and `max`. How `data_seed` chooses a count within the interval belongs to `GENERATOR_SPEC.md`.

### 7.2. `RawColumn`

| Field | Type | Required | Default and rules |
| --- | --- | --- | --- |
| `name` | `Identifier` | yes | — |
| `type` | `DataType` | yes | The JSON and Python field name is exactly `type`. |
| `nullable` | strict boolean | no | `false`. |
| `null_probability` | `Probability` | no | `0.0`. MUST be `0.0` when `nullable` is false. |
| `unique` | strict boolean | no | `false`. Applies to non-null generated values. |
| `generator` | `GeneratorSpec` | yes | — |
| `description` | optional description | no | `None`. |

The generator produces the non-null value domain. Null insertion and uniqueness are column constraints coordinated by the raw-data generator.

### 7.3. `RawTable`

| Field | Type | Required | Default and rules |
| --- | --- | --- | --- |
| `name` | `Identifier` | yes | — |
| `rows` | `RowCount` | yes | — |
| `columns` | non-empty tuple of `RawColumn` | yes | Column names MUST be unique within the table. |
| `primary_key` | tuple of `Identifier` | no | Empty tuple; entries MUST be unique. Composite-key order is significant. |
| `description` | optional description | no | `None`. |

Existence, nullability, and type rules for `primary_key` members require access to the table's columns and are semantic checks, not `RawTable` local validators.

## 8. Mini-generator models

### 8.1. General rules

Every mini-generator is a closed Pydantic model with a literal `kind`. A mini-generator configuration is a stable public DSL construct; implementation libraries such as Python `random`, NumPy, or Faker are private execution details.

All randomness MUST ultimately be controlled by named streams derived from `data_seed`, but stream derivation and sampling semantics belong to `GENERATOR_SPEC.md`.

### 8.2. Required version-1 generators

#### `FormattedIdGenerator`

```text
kind: "formatted_id"
prefix: string, default "", maximum length 32
digits: strict integer, 1..18
start: strict non-negative integer, default 1
```

`prefix` MUST contain only printable ASCII characters and MUST NOT contain whitespace. The upper bound implied by `start`, table row counts, and `digits` is a semantic feasibility check.

#### `IntegerRangeGenerator`

```text
kind: "integer_range"
min: strict integer
max: strict integer
```

The local validator MUST require `min < max`.

#### `FloatRangeGenerator`

```text
kind: "float_range"
min: strict finite float
max: strict finite float
decimal_places: strict integer, 0..12, default 2
```

The local validator MUST require `min < max`.

#### `DateRangeGenerator`

```text
kind: "date_range"
min: date
max: date
```

Dates serialize as ISO `YYYY-MM-DD`. The local validator MUST require `min < max`.

#### `TimestampRangeGenerator`

```text
kind: "timestamp_range"
min: datetime
max: datetime
```

Inputs MUST include a UTC offset. Validators MUST reject naive datetimes and offsets that cannot be normalized to UTC. Canonical serialization MUST use UTC. The local validator MUST require `min < max` after normalization.

#### `CategoricalGenerator`

```text
kind: "categorical"
values: non-empty tuple[ScalarValue]
weights: optional tuple[finite float]
```

`values` MUST be unique by both value and JSON scalar type; therefore `true` and `1` are distinct DSL values but cannot be conflated by Python equality. When `weights` is present:

- its length MUST equal the length of `values`;
- every weight MUST be non-negative;
- at least one weight MUST be greater than zero; and
- normalization MUST NOT be performed by Pydantic.

The generator runtime may normalize weights as specified by `GENERATOR_SPEC.md`.

#### `BooleanGenerator`

```text
kind: "boolean"
true_probability: Probability, default 0.5
```

#### `RandomStringGenerator`

```text
kind: "random_string"
min_length: strict positive integer
max_length: strict positive integer
alphabet: non-empty strict string, default lowercase ASCII letters and digits
```

`alphabet` characters MUST be unique, and `min_length <= max_length`.

#### `TemplateStringGenerator`

```text
kind: "template_string"
template: strict string, length 1..256
```

`template` combines literals with one or more placeholders of the exact form `{column_name}`, where `column_name` is an `Identifier` naming another column in the same raw table. For example:

```text
{first_name}.{last_name}@example.test
```

At least one placeholder is required. Nested access, indexing, conversions, format specifiers, function calls, conditionals, loops, escaped executable fragments, and every Jinja-style construct are prohibited. A local validator MUST reject unmatched braces and any placeholder that is not an `Identifier`. Existence, self-reference, type formatting, and cycles among template-generated columns are semantic checks. Runtime interpolation semantics belong to `GENERATOR_SPEC.md`.

#### `ForeignKeyGenerator`

```text
kind: "foreign_key"
relationship: Identifier
target_side: "left" | "right"
```

This generator never samples an unrelated column independently. It tells the relationship-aware raw generator which declared key universe supplies the value. For a composite foreign key, every component column uses the same `relationship` and `target_side`; the runtime MUST sample the composite tuple atomically.

#### Faker-backed leaf generators

Version 1 MUST expose these stable generator kinds:

```text
person_name
email
city
street_address
company_name
phone_number
```

Each corresponding model has:

```text
kind: the literal kind above
locale: strict string, default "en_US"
```

The finite public kinds are the contract. A scenario MUST NOT name an arbitrary Faker provider or pass provider-specific arguments. The implementation MAY use Faker internally, but its version MUST be pinned and recorded in provenance. Faker MUST NOT control relationships, keys, cardinality, null insertion, uniqueness, or row counts.

### 8.3. Generator/type compatibility

Pydantic validates each generator's own fields. The semantic validator MUST enforce this compatibility matrix:

| Column type | Allowed generator kinds |
| --- | --- |
| `string` | `formatted_id`, `categorical` with string values, `random_string`, `template_string`, `foreign_key` targeting a string key, and all Faker-backed leaf kinds |
| `integer` | `integer_range`, `categorical` with integer values, `foreign_key` targeting an integer key |
| `float` | `float_range`, `categorical` with float values, `foreign_key` targeting a float key |
| `boolean` | `boolean`, `categorical` with boolean values, `foreign_key` targeting a boolean key |
| `date` | `date_range`, `foreign_key` targeting a date key |
| `timestamp` | `timestamp_range`, `foreign_key` targeting a timestamp key |

Using floating-point or boolean relationship keys SHOULD be rejected by authoring policy even though the grammar can represent them. Mixed-type categorical values MUST be rejected for a typed column.

## 9. Relationship models

### 9.1. Directional meaning

Every relationship names a `left` and `right` endpoint. Cardinality is read left-to-right:

```text
one_to_many: one left key may match many right rows
many_to_one: many left rows may match one right key
one_to_one: at most one matched row on each side
many_to_many: many rows on both sides, materialized through a declared bridge table
```

Relationship endpoints refer to raw-table columns. Staging and downstream joins use the lineage derived from those columns.

### 9.2. Direct relationships

`OneToOneRelationship`, `OneToManyRelationship`, and `ManyToOneRelationship` each have:

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `name` | `Identifier` | yes | — |
| `cardinality` | respective literal | yes | Union discriminator. |
| `left` | `RelationshipEndpoint` | yes | — |
| `right` | `RelationshipEndpoint` | yes | — |
| `description` | optional description | no | `None`. |

For `one_to_many`, the right-side columns are the dependent foreign key and their `ForeignKeyGenerator.target_side` is `left`. For `many_to_one`, the left-side columns are dependent and target `right`. For `one_to_one`, exactly one endpoint MUST be generated as a foreign key targeting the other endpoint.

### 9.3. Many-to-many relationships

`ManyToManyRelationship` has the direct fields above plus:

```text
cardinality: "many_to_many"
bridge: BridgeReference
```

`BridgeReference` has:

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `table` | `Identifier` | yes | A raw bridge table distinct from the left and right tables. |
| `left_columns` | non-empty tuple of `Identifier` | yes | Bridge columns referencing `left.columns`. |
| `right_columns` | non-empty tuple of `Identifier` | yes | Bridge columns referencing `right.columns`. |

Bridge-column tuples MUST be disjoint. Their arity MUST match their respective endpoint arity. Their column generators MUST target the corresponding endpoint through the same relationship. Direct implicit many-to-many generation without a bridge table is prohibited.

### 9.4. Referential integrity

Relationships describe healthy referential structure. Version 1 does not allow an orphan-rate or referential-integrity violation in a healthy scenario. Optional foreign keys are represented with a nullable dependent column and `null_probability`; every non-null generated foreign key MUST match its target key.

Target participation or coverage distributions MAY be added in a later language version after their execution semantics are specified. An ambiguous `coverage` or `completeness` field MUST NOT be accepted in version 1.

## 10. Structured expressions and conditions

Expressions are used only for derived intermediate columns and conditions. They never contain SQL.

### 10.1. Expression union

`Expression` MUST include:

| Model / `kind` | Fields |
| --- | --- |
| `ColumnExpression` / `column` | `column: Identifier` |
| `LiteralExpression` / `literal` | `value: ScalarValue` |
| `BinaryExpression` / `binary` | `operator: add | subtract | multiply | divide`, `left: Expression`, `right: Expression` |
| `DatePartExpression` / `date_part` | `part: year | quarter | month | day | day_of_week`, `value: Expression` |
| `CoalesceExpression` / `coalesce` | `values: tuple[Expression, ...]` with at least two items |

Recursive depth MUST be limited by the parsing entry point to prevent pathological input. A maximum depth of 16 is RECOMMENDED.

### 10.2. Condition union

`Condition` MUST include:

| Model / `kind` | Fields |
| --- | --- |
| `ComparisonCondition` / `comparison` | `operator: eq | ne | lt | lte | gt | gte`, `left: Expression`, `right: Expression` |
| `InCondition` / `in` | `value: Expression`, non-empty `options: tuple[ScalarValue, ...]`, `negated: bool = false` |
| `NullCondition` / `is_null` | `value: Expression`, `negated: bool = false` |
| `BooleanCondition` / `all` | `conditions: tuple[Condition, ...]`, at least two |
| `AnyCondition` / `any` | `conditions: tuple[Condition, ...]`, at least two |
| `NotCondition` / `not` | `condition: Condition` |

Pydantic validates node shape and collection bounds. The semantic validator resolves columns, infers expression types, checks operator compatibility, and rejects conditions that cannot evaluate to boolean.

## 11. Staging models

### 11.1. Layer responsibility

Staging is source-oriented. A staging model reads exactly one raw table and performs explicit selection/rename, typing, string normalization, null normalization, technical filtering, and technical deduplication. Business joins, aggregations, segmentation, and multi-source calculations MUST NOT appear in staging.

### 11.2. `StagingColumn`

| Field | Type | Required | Default and rules |
| --- | --- | --- | --- |
| `source` | `Identifier` | yes | Source raw-column name. |
| `target` | `Identifier` | yes | Output staging-column name. A different name represents rename. |
| `operations` | tuple of `StagingColumnOperation` | no | Empty tuple; applied in declared order. |
| `description` | optional description | no | `None`. |

There is no separate `rename` or `drop` operation. `target` performs rename, and omission from `StagingModel.columns` performs drop. This prevents two equivalent representations of the same transformation.

### 11.3. Staging column operations

The `StagingColumnOperation` union MUST contain:

| `op` | Additional fields | Purpose |
| --- | --- | --- |
| `cast` | `type: DataType`, optional `format: str` | Explicit type conversion. `format` is allowed only when parsing a string as date or timestamp. |
| `trim` | none | Remove surrounding whitespace. |
| `lower` | none | Lowercase a string. |
| `upper` | none | Uppercase a string. |
| `replace` | `old: str`, `new: str` | Literal string replacement. Empty `old` is invalid. |
| `map_values` | non-empty `mapping: dict[str, str]`, `on_unmapped: keep | null | error = keep` | Normalize string categories. |
| `null_if` | non-empty `values: tuple[ScalarValue, ...]` | Convert listed values to null. |
| `coalesce` | `value: ScalarValue` | Replace null with a literal. |

The operations form a typed pipeline: the output type of one operation is the input type of the next. Compatibility is a semantic check because it depends on the source-column type and preceding operations.

### 11.4. Staging row operations

The `StagingRowOperation` union MUST contain:

#### `FilterRowsOperation`

```text
op: "filter"
condition: Condition
```

#### `DeduplicateRowsOperation`

```text
op: "deduplicate"
keys: non-empty tuple[Identifier]
order_by: non-empty tuple[SortKey]
```

Rows are retained according to the declared sort order. The generator specification defines the exact SQL rendering. Semantic validation MUST establish deterministic tie breaking from declared keys and source lineage.

### 11.5. `StagingModel`

| Field | Type | Required | Default and rules |
| --- | --- | --- | --- |
| `name` | `Identifier` | yes | — |
| `source` | `Identifier` | yes | Raw-table name. |
| `columns` | non-empty tuple of `StagingColumn` | yes | `source` values and `target` values MUST each be unique locally. |
| `row_operations` | tuple of `StagingRowOperation` | no | Empty tuple; applied in order after column operations. |
| `grain` | non-empty tuple of `Identifier` | yes | Expected output uniqueness key; no duplicate members. |
| `description` | optional description | no | `None`. |

## 12. Joins and intermediate models

### 12.1. Shared projection types

`ProjectionColumn` has:

```text
source: Identifier
target: Identifier
```

It explicitly selects and optionally renames a column from a single input model.

`JoinProjectionColumn` has:

```text
side: "left" | "right"
source: Identifier
target: Identifier
```

Join output columns MUST be enumerated through `JoinProjectionColumn`. Wildcards and implicit collision suffixes are prohibited.

`DerivedColumn` has:

```text
name: Identifier
type: DataType
expression: Expression
description: optional description
```

The declared type is checked against the inferred expression type.

### 12.2. `JoinSpec`

Version 1 supports only `inner` and `left` joins. `right`, `full`, `cross`, natural, as-of, and lateral joins are out of scope.

`JoinSpec` has:

| Field | Type | Required | Rules |
| --- | --- | --- | --- |
| `type` | `inner | left` | yes | — |
| `on` | non-empty tuple of `JoinKeyPair` | yes | No duplicate pairs. |

`JoinKeyPair` has `left: Identifier` and `right: Identifier`. Only equality joins over explicit key pairs are supported.

### 12.3. Intermediate-model union

Every intermediate model has a unique `name`, non-empty expected `grain`, and optional `description`. The `operation` discriminator selects one of the following models.

#### `TransformIntermediateModel`

```text
operation: "transform"
name: Identifier
source: Identifier
columns: non-empty tuple[ProjectionColumn]
derived_columns: tuple[DerivedColumn] = ()
filters: tuple[Condition] = ()
grain: non-empty tuple[Identifier]
description: optional description
```

This model performs projection, rename, structured derivation, and filtering over one staging or intermediate input. Output names across projected and derived columns MUST be unique.

Projection occurs first, derived columns are evaluated against projected names, and filters are then evaluated against projected and derived names. The compiler may use nested SELECTs or CTEs to preserve this order.

#### `JoinIntermediateModel`

```text
operation: "join"
name: Identifier
left: Identifier
right: Identifier
join: JoinSpec
columns: non-empty tuple[JoinProjectionColumn]
derived_columns: tuple[DerivedColumn] = ()
filters: tuple[Condition] = ()
grain: non-empty tuple[Identifier]
description: optional description
```

Join key evaluation occurs on the two inputs. Projection occurs next; derived columns and filters operate on the projected output names. The compiler may use nested SELECTs or CTEs to preserve this declared order.

#### `AggregateIntermediateModel`

```text
operation: "aggregate"
name: Identifier
source: Identifier
filters: tuple[Condition] = ()
group_by: non-empty tuple[ProjectionColumn]
metrics: non-empty tuple[MetricSpec]
grain: non-empty tuple[Identifier]
description: optional description
```

Filters are applied before aggregation. Grouping target names and metric names form the output schema and MUST be unique together. `grain` MUST be a non-empty subset of grouping target names.

#### `DeduplicateIntermediateModel`

```text
operation: "deduplicate"
name: Identifier
source: Identifier
keys: non-empty tuple[Identifier]
order_by: non-empty tuple[SortKey]
grain: non-empty tuple[Identifier]
description: optional description
```

This is business-level deduplication. Its output schema is inherited from `source`; its `grain` MUST equal `keys` as a set. Deterministic tie breaking is required.

### 12.4. Intermediate-layer restrictions

An intermediate input MUST be a staging model or another intermediate model. It MUST NOT reference a raw table or output model. An intermediate model MUST NOT self-reference. Validity MUST NOT depend on declaration order; the semantic validator resolves and topologically sorts dependencies.

`union` and window-function models are intentionally excluded from version 1. Adding a new operation requires a new concrete model, semantic rules, renderer support, tests, and a schema-version decision.

## 13. Metric models

Every metric has `name: Identifier` and optional `description`. `MetricSpec` is discriminated by `function` and MUST contain:

| Model / `function` | Additional fields |
| --- | --- |
| `CountRowsMetric` / `count_rows` | none |
| `CountMetric` / `count` | `column: Identifier` |
| `CountDistinctMetric` / `count_distinct` | `column: Identifier` |
| `SumMetric` / `sum` | `column: Identifier` |
| `AverageMetric` / `avg` | `column: Identifier` |
| `MinimumMetric` / `min` | `column: Identifier` |
| `MaximumMetric` / `max` | `column: Identifier` |
| `ConditionalCountMetric` / `conditional_count` | `condition: Condition` |
| `ConditionalSumMetric` / `conditional_sum` | `column: Identifier`, `condition: Condition` |

`sum`, `avg`, and `conditional_sum` require a numeric source column. `min` and `max` accept numeric, date, or timestamp columns. Count functions return `integer`; `sum` and `avg` return `float` in version 1. These are semantic type rules.

Metric names MUST be unique within their containing model and MUST NOT collide with group-by target names.

Compound metrics such as ratios, arbitrary aggregate expressions, percentile functions, and window metrics are out of scope for version 1.

## 14. Output models

An output model is an analytical aggregation over exactly one intermediate model.

| Field | Type | Required | Default and rules |
| --- | --- | --- | --- |
| `name` | `Identifier` | yes | — |
| `source` | `Identifier` | yes | Intermediate-model name. |
| `filters` | tuple of `Condition` | no | Empty tuple; applied before aggregation. |
| `group_by` | non-empty tuple of `ProjectionColumn` | yes | Source and target names are unique within their respective sets. |
| `grain` | non-empty tuple of `Identifier` | yes | Expected row uniqueness key; no duplicates. |
| `dimensions` | tuple of `Identifier` | no | Empty tuple; analytical dimension names in the output. |
| `metrics` | non-empty tuple of `MetricSpec` | yes | — |
| `description` | optional description | no | `None`. |

Semantic validation MUST require:

- every grouping source column to exist in the input;
- every `grain` and `dimensions` member to be a grouping target name;
- every metric input and condition to resolve against the pre-aggregation input;
- all output names to be unique;
- the declared grain to be consistent with grouping and lineage; and
- the output source to be an intermediate model.

`grain` and `group_by` are distinct concepts. `group_by` lists physical aggregation keys; `grain` states the minimal declared business key expected to identify a row. `group_by` may include dimensions functionally dependent on the grain, but healthy execution MUST confirm that the grain is unique.

## 15. Healthy assertion models

### 15.1. Derived and explicit assertions

The dbt-project generator MUST derive healthy tests from structural contracts where possible:

- raw and model primary/grain keys imply composite uniqueness and not-null tests;
- `nullable: false` implies not-null;
- `unique: true` implies uniqueness;
- raw relationships imply relationship tests for non-null dependent keys; and
- every output implies a non-empty-row assertion; an explicit row-count assertion may add stricter bounds but may not weaken non-emptiness.

`Scenario.tests` contains only additional explicit assertions that cannot be derived unambiguously. Repeating an identical derived assertion SHOULD be rejected as redundant.

### 15.2. Assertion union

Every assertion has `name: Identifier`, a target `model: Identifier`, and optional `description`. `HealthyAssertion` MUST include:

| `type` | Additional fields | Local rules |
| --- | --- | --- |
| `not_null` | non-empty `columns` | No duplicate columns. |
| `unique` | non-empty `columns` | Composite order is retained; no duplicates. |
| `accepted_values` | `column`, non-empty `values: tuple[ScalarValue]` | Values unique by value and scalar type. |
| `relationships` | non-empty `columns`, `to_model`, non-empty `to_columns` | Source/target arity equal. |
| `row_count` | optional non-negative integer `min`, optional non-negative integer `max` | At least one bound; `min <= max` when both exist. |
| `column_range` | `column`, optional `min`, optional `max`, `inclusive: bool = true` | At least one bound. |

All assertion references and contextual value types are semantic checks. All healthy assertions are blocking; warning severity is not part of the version-1 contract.

Free-form expression tests are prohibited. A required invariant not expressible by this union must be added as a typed assertion model or derived test, not inserted as SQL text.

## 16. Pydantic local validation requirements

Pydantic validation is responsible for syntax, closed vocabularies, strict types, field-local constraints, and invariants contained entirely within one value object. It MUST be deterministic and free of I/O.

At minimum, local validators and field constraints MUST cover:

| Model area | Required local checks |
| --- | --- |
| Base contract | Extra fields forbidden; strict types; finite floats; defaults validated. |
| Identifiers | Pattern and length. |
| Root scenario | Exact schema-version literal and collection size bounds. |
| Row counts and ranges | Positive/non-negative bounds as specified and correct min/max order. |
| Raw table | Non-empty columns; unique local column names and primary-key entries. |
| Raw column | `null_probability == 0.0` when non-nullable. |
| Categorical generator | Non-empty unique values; weight length and non-negative/non-zero rules. |
| Random strings | Non-empty unique alphabet and length ordering. |
| Template strings | Non-empty restricted placeholders and balanced braces. |
| Timestamps | Timezone-awareness and UTC-normalizable bounds. |
| Relationship value objects | Non-empty unique endpoint columns; bridge arity and disjointness where locally knowable. |
| Expression/condition nodes | Required operands and minimum recursive collection lengths. |
| Staging model | Unique source and target column names; unique grain members. |
| Operation models | Conditional field combinations such as cast format and row-count bounds. |
| Join | Non-empty and unique key pairs. |
| Intermediate variants | Non-empty required lists; unique local output names; grain uniqueness; operation-specific local invariants. |
| Metrics | Function-specific required fields via discriminated variants; unique metric names in a container. |
| Output model | Unique group-by source/target names, dimensions, grain, and output names. |
| Assertions | Function-specific bounds, arity, and uniqueness. |

Pydantic validators MUST NOT:

- look up another table or model by name;
- build or traverse the DAG;
- inspect the filesystem, database, dbt project, environment, clock, locale, or network;
- infer an undeclared default from `domain` or a naming convention;
- mutate the input to make it valid;
- generate values or SQL; or
- perform compiler or healthy-run checks.

An input may therefore pass Pydantic validation and fail semantic validation. This is intentional and MUST be covered by tests.

## 17. Semantic validation requirements

### 17.1. Interface and result

Semantic validation runs only on a successfully parsed `Scenario`.

The public operation SHOULD have a shape equivalent to:

```python
validate_semantics(scenario: Scenario) -> ValidatedScenario
```

`ValidatedScenario` MUST be a distinct immutable wrapper or result type, not a type alias for `Scenario`. It SHOULD contain or expose:

- the original immutable scenario;
- symbol tables for raw tables and all model layers;
- resolved output schemas and data types;
- column lineage;
- a topological order for intermediate models;
- resolved grains and keys; and
- derived healthy assertions.

The scenario compiler MUST accept `ValidatedScenario`, not bare `Scenario`. This API boundary prevents accidental compilation after structural validation alone.

On failure, semantic validation MUST return or raise structured issues containing at least:

```text
code
JSON-style path
message
related path or referenced name when applicable
```

Issue order MUST be deterministic. The validator SHOULD collect independent issues in one pass rather than fail on the first missing reference, but it MUST suppress cascades whose only cause is an already-reported unresolved symbol.

### 17.2. Symbol and namespace checks

The semantic validator MUST verify:

- unique raw-table names;
- unique model names across staging, intermediate, and output layers;
- no collision between raw-table and model names where it would make generated dbt identifiers ambiguous;
- unique relationship names and explicit assertion names;
- every referenced table, model, relationship, and column exists; and
- references point to the permitted upstream layer.

### 17.3. Raw tables, keys, and generators

The validator MUST verify:

- every primary-key member exists in its raw table;
- primary-key columns are non-nullable;
- declared unique and primary keys are feasible for the row-count range and generator capacity;
- generator kind and configured scalar values are compatible with the column type;
- categorical values are homogeneous for the column type;
- every template placeholder resolves to another column in the same raw table, template dependencies are acyclic, and their evaluation order is deterministic;
- foreign-key generators refer to an existing relationship and occur on the correct dependent or bridge columns;
- every component of a composite foreign key uses the same relationship target and is generated atomically; and
- Faker-backed kinds occur only on string columns.

### 17.4. Relationship checks

The validator MUST verify:

- endpoint tables and columns exist;
- left and right endpoint arity is equal for direct relationships;
- corresponding endpoint types are exactly equal in version 1;
- the unique side required by cardinality is a declared primary or unique key;
- `one_to_one` endpoints are both unique;
- dependent columns have correct foreign-key generators and compatible nullability;
- a many-to-many bridge table exists and is distinct from both endpoints;
- bridge component arities, types, generators, and target sides are correct; and
- one dependent column is not owned by conflicting relationships.

### 17.5. Staging checks

The validator MUST verify:

- every raw table has exactly one staging model and every staging model has a raw source;
- staging source columns exist;
- column-operation chains are type-correct in declared order;
- `cast.format` is present or absent consistently with source/target types;
- row-operation columns and conditions resolve against post-column-operation names;
- `null_if` and `coalesce` values match the current column type;
- deduplication keys and ordering columns exist and provide deterministic ordering;
- staging grain columns exist after transformations; and
- staging operations remain source-oriented and do not encode multi-source business logic.

The validator MUST compute staging output schema and raw-to-staging column lineage.

### 17.6. DAG and intermediate checks

The validator MUST:

- resolve every intermediate dependency independent of declaration order;
- reject cycles and self-dependencies;
- reject raw or output inputs to intermediate models;
- compute a stable topological order using declaration order only as a deterministic tie breaker;
- derive every intermediate output schema and column lineage;
- resolve every projection, expression, condition, key, sort key, and grain column;
- type-check expressions and require declared derived-column types to match inferred types;
- prevent divide operations with statically incompatible operands and require runtime safe-division semantics from the generator specification;
- require join key arity and types to match;
- reject duplicate or contradictory join key pairs;
- require join keys to be supported by traceable raw relationship/key lineage in version 1;
- reject ambiguous join-column references and output-name collisions;
- validate the expected grain against join cardinality and source grains;
- require aggregate grouping/metric inputs to exist and metric functions to accept their types;
- require aggregate grain to be a subset of grouping targets; and
- require deduplication keys to equal the declared grain and have deterministic tie breaking.

### 17.7. Output and assertion checks

The validator MUST:

- require every output source to be an intermediate model;
- resolve grouping columns, filters, dimensions, metric inputs, and conditions;
- type-check metrics and explicit assertion bounds/values;
- require `grain` and `dimensions` to reference output grouping names;
- derive the complete output schema;
- reject duplicate assertions with the same effective meaning;
- reject direct contradictions between explicit and derived assertions; and
- produce all automatically derived healthy assertions.

### 17.8. Connectivity and non-degeneracy

The validator MUST verify that:

- every staging and intermediate model is an ancestor of at least one output;
- every declared raw table reaches at least one output through its staging model;
- every output is reachable from raw inputs through all four layers;
- the model graph has no disconnected component; and
- every significant projected or metric column has traceable raw lineage.

Semantic validation can prove structural reachability, not runtime row counts. Empty outputs, actual key uniqueness, actual relationship integrity, and actual grain uniqueness remain healthy-run acceptance gates.

## 18. Parsing, serialization, and JSON Schema

### 18.1. Parsing

The package MUST expose one documented JSON parsing helper. It MUST:

1. accept UTF-8 JSON bytes or text;
2. reject duplicate JSON object keys before or during Pydantic parsing;
3. enforce a configured maximum document size and expression depth;
4. call the strict Pydantic JSON validation path; and
5. wrap Pydantic failures in a project-level parse error without discarding the original error locations.

Duplicate object keys are invalid even though many JSON parsers otherwise retain only the last value.

### 18.2. Canonical serialization

Canonical scenario serialization MUST:

- emit UTF-8 JSON;
- serialize dates and timestamps in the canonical forms defined above;
- include declared defaults so equivalent scenarios do not depend on parser defaults;
- omit optional fields whose value is `None`;
- preserve array order;
- sort object keys lexicographically;
- use stable compact separators for content hashing; and
- never include computed semantic indexes or compiler/runtime state.

Pretty-printed authoring JSON MAY differ in whitespace only. Scenario content identity in the healthy manifest MUST be computed from the canonical representation, not the original file bytes.

### 18.3. JSON Schema export

`json_schema.py` MUST generate JSON Schema from `Scenario.model_json_schema()` or the equivalent Pydantic API. The generated schema MUST:

- expose all discriminators and closed variants;
- reject additional properties consistently with runtime models;
- include field descriptions and constraints useful to authoring tools; and
- identify the scenario-language version.

The generated schema is a derived build artifact. Tests MUST fail if a checked-in or published schema is stale relative to the Pydantic models, but normal test execution MUST NOT rewrite repository files.

## 19. Contract test requirements

### 19.1. Positive coverage

Tests MUST include:

- one minimal valid JSON example for every discriminated-union variant;
- a complete valid scenario for each intermediate-model operation;
- direct relationships for all three direct cardinalities and an explicit-bridge many-to-many relationship;
- composite primary and foreign keys;
- a staging operation chain whose type changes;
- valid same-row template dependencies;
- nested expressions and conditions;
- every metric and assertion variant;
- exact and ranged row counts; and
- parse -> canonical serialize -> parse equality.

At least one complete fixture MUST use all four layers and the maximum supported counts: four raw/staging models, three intermediate models, and two output models.

### 19.2. Negative local-validation coverage

Every custom local invariant MUST have a focused failing test. At minimum, tests MUST cover:

- missing and unknown fields;
- wrong scalar types and prohibited coercion;
- unknown or missing discriminators;
- invalid identifiers;
- NaN and infinity;
- empty constrained collections;
- duplicate local names and values;
- invalid range ordering;
- invalid categorical weights;
- invalid template syntax and placeholders;
- invalid timestamp timezone handling;
- invalid nullability/probability combinations;
- invalid operation-specific field combinations;
- invalid assertion bounds; and
- root collection counts outside the 3–4 / 2–3 / 1–2 limits.

### 19.3. Validation-boundary coverage

Tests MUST demonstrate that structurally valid scenarios with each of the following defects successfully parse with Pydantic and then fail semantic validation:

- missing table, model, relationship, or column reference;
- incompatible generator and column type;
- missing, self-referential, or cyclic template dependencies;
- invalid primary key;
- relationship arity or type mismatch;
- wrong foreign-key side;
- invalid staging operation chain;
- cyclic intermediate DAG;
- invalid layer dependency;
- join key mismatch;
- impossible or undeclared grain;
- invalid metric input type;
- disconnected model; and
- contradictory healthy assertion.

This boundary test is mandatory: moving all cross-object checks into a root Pydantic validator would violate the architecture even if malformed scenarios were rejected.

### 19.4. Error and schema tests

Tests MUST verify deterministic semantic issue ordering, stable project issue codes, useful JSON paths, duplicate-key rejection, canonical content identity, and generated-schema freshness.

Tests SHOULD assert selected JSON Schema properties and discriminators rather than snapshot the entire schema blindly. If a complete schema snapshot is kept, updates MUST be reviewed as public contract changes.

## 20. Evolution rules

The scenario language is a public internal contract. A change is breaking if it removes or renames a field or variant, changes a default or field meaning, narrows previously valid values, changes canonical serialization, or changes semantic validity for an existing scenario.

Breaking changes MUST:

1. introduce a new `schema_version` literal;
2. provide an explicit migration function or a deliberate rejection path;
3. update this specification, Pydantic models, semantic validator, JSON Schema, compiler, fixtures, and tests together; and
4. preserve the ability to identify the version from the JSON document before full parsing.

New generator, operation, expression, metric, or assertion variants MUST NOT be added speculatively. Each addition requires deterministic compiler semantics and focused tests before it becomes valid input.

## 21. Implementation acceptance criteria

The scenario-contract implementation is complete only when:

- the required model hierarchy and strict base behavior are implemented;
- every union is explicitly discriminated and exported through the root model;
- local validators enforce all requirements in section 16 without I/O or repair;
- the separate semantic validator enforces section 17 and returns `ValidatedScenario`;
- the compiler-facing API cannot accidentally accept a bare `Scenario`;
- canonical serialization and generated JSON Schema are deterministic;
- all contract, boundary, semantic, serialization, and schema-freshness tests pass;
- lint and type checks configured by the project pass; and
- at least one complete scenario can proceed from JSON parsing through semantic validation to the compiler boundary.

Passing Pydantic validation alone MUST never be described as a valid or healthy scenario. A scenario becomes structurally valid after Pydantic parsing, semantically valid after the separate validation pass, and healthy only after all execution gates in `PIPELINE_SPEC.md` succeed.
