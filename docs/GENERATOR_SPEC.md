# Pipeline Generator Specification

Status: draft.

## 1. Purpose

This document defines the version-1 compiler and materialization contract for a validated
scenario. It specifies:

- the compiler-facing API and component boundaries;
- deterministic raw-data generation;
- the fixed Parquet, DuckDB, and dbt physical boundary;
- SQL rendering for every version-1 scenario construct;
- lowering of explicit and derived healthy assertions to dbt tests;
- clean-control execution, failure handling, and cache reuse; and
- the instance record needed to reproduce and inspect a materialized pipeline.

The result of this subsystem is a successful, immutable clean baseline for one exact pipeline
instance. The downstream fault subsystem may copy or reconstruct that baseline, but it may not
mutate the cached baseline itself.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 2. Scope and authority

`SCENARIO_SPEC.md` owns the scenario language, static validity, canonical scenario
serialization, and the `ValidatedScenario` result. `PIPELINE_SPEC.md` owns the cross-subsystem
lifecycle and clean-control requirement. This document owns execution semantics after semantic
validation succeeds.

The generator MUST implement every version-1 generator, staging operation, expression,
condition, intermediate operation, metric, output model, and healthy assertion admitted by the
scenario contract. It MUST NOT infer behavior from `domain`, descriptions, identifier spelling,
or business vocabulary.

Descriptions are metadata and MUST NOT affect raw values, generated SQL, model configuration,
or assertions. No scenario value may be interpreted as Python, arbitrary SQL, Jinja, a regular
expression, a shell command, or a provider name outside the closed scenario vocabulary.

Descriptions and `domain` remain part of canonical scenario content and therefore of cache
identity, even though they do not change executable artifacts.

This document targets one local runtime only:

```text
generated Parquet
    -> local DuckDB database
        -> generated dbt project using dbt-duckdb
            -> materialized staging/intermediate/output tables
                -> dbt healthy tests
```

Multiple SQL dialects, remote warehouses, services, plugin registries, and configurable
execution backends are outside version 1.

## 3. Input boundary

### 3.1. Core compiler input

Every core compiler operation MUST require a `ValidatedScenario`. A bare `Scenario`, unparsed
JSON object, or path MUST NOT satisfy that type boundary.

This requirement does not imply that callers must construct the wrapper themselves. A public
convenience facade MAY accept UTF-8 scenario JSON or a path, but it MUST perform this exact chain
before invoking compiler code:

```text
parse_scenario_json
    -> validate_semantics
        -> compile/materialize ValidatedScenario
```

The original `scenario.json` is sufficient to reconstruct all information, but accepting only
`ValidatedScenario` inside the compiler prevents callers from accidentally skipping global
semantic validation.

### 3.2. Use of validated facts

The authored scenario remains the source of executable declarations. The additional immutable
facts on `ValidatedScenario` are resolved views of that source, not a second authored contract.
The compiler uses them as follows:

| Fact | Compiler use |
| --- | --- |
| `raw_by_name`, layer symbol maps | Exact lookup without name-based inference. |
| resolved layer schemas | Type-aware SQL/literal rendering and artifact metadata. |
| `topological_order` | Deterministic intermediate-model rendering order. dbt `ref` still expresses the executable graph. |
| resolved grains and keys | Structural test generation and manifest metadata. |
| lineage | Traceability/crosswalk metadata; not a source of new transformations. |
| `derived_assertions` | One input to healthy-test rendering. |
| `scenario` | Raw generators, relationships, transformations, metrics, outputs, and explicit tests. |

The compiler MUST NOT require a new public resolved-relationship structure, raw dependency
order, or unified explicit-plus-derived assertion list to be added to `ValidatedScenario`.
Those views MAY be computed privately by the component that needs them.

### 3.3. External inputs

`data_seed`, output/cache location, and runtime configuration are supplied outside the scenario.
Version 1 accepts `data_seed` as a non-negative integer not greater than `2^63 - 1`.

`data_seed` controls clean raw-data variation only. It is distinct from every future
fault-injection seed and MUST NOT control fault selection, oracle decisions, diagnostic-tool
order, or hidden metadata.

## 4. Component architecture

The subsystem has four logical parts:

```text
                         ValidatedScenario
                         /               \
                        v                 v
              raw-plan builder       dbt renderer
                        |                 |
                        v                 v
                 immutable RawPlan   generated dbt files
                        |                 |
                        v                 |
             Parquet + DuckDB raw         |
                         \               /
                          v             v
                         clean dbt build
                                |
                                v
                    instance record + cache
```

The implementation MAY group these parts into fewer modules. Their responsibility boundaries
are normative.

### 4.1. Raw plan

`RawPlan` is a small, deeply immutable, internal execution plan for the raw generator. It MUST
contain only facts needed to produce raw tables, such as:

- chosen table-generation order;
- row-count specifications;
- leaf and template-column evaluation order;
- composite foreign-key groups and their ordered target-column bindings;
- hard raw constraints; and
- deterministic random-stream names.

Only the raw executor consumes `RawPlan`. It is neither a public artifact nor a cache identity,
and it MUST NOT contain dbt models, SQL, tests, filesystem paths, or downstream fault metadata.

### 4.2. dbt renderer

The dbt renderer consumes the same `ValidatedScenario` directly. It renders sources, models,
tests, macros, and fixed project configuration. It MUST NOT execute `RawPlan` or reconstruct raw
values.

### 4.3. No shared compilation IR

Version 1 MUST NOT introduce a general `CompilationPlan`, optimizer, public `CompiledScenario`,
or shared intermediate representation merely to connect the raw and dbt components. Their tasks
are separate. Their required agreement is limited to the fixed physical contract in section 5,
which is derived from the same validated identifiers and types.

A passive return object that groups generated artifact paths MAY exist, but it MUST NOT become a
second semantic source of truth.

### 4.4. Suggested public shape

The public operations SHOULD be equivalent to:

```python
build_raw_plan(validated: ValidatedScenario) -> RawPlan
render_dbt_project(validated: ValidatedScenario, destination: Path) -> RenderedDbtProject
prepare_clean_instance(
    validated: ValidatedScenario,
    data_seed: int,
    cache_root: Path,
) -> CleanInstance
```

A file/JSON facade MAY parse and validate before calling `prepare_clean_instance`. The exact
module layout and class names are not public requirements.

## 5. Fixed physical contract

### 5.1. Naming

Scenario identifiers already satisfy a closed identifier grammar and are globally checked for
ambiguous collisions. Version 1 therefore uses fixed identity mappings rather than a
configurable `NamingPolicy`.

| Logical object | Physical representation |
| --- | --- |
| raw table `T` | Parquet file `raw/T.parquet` |
| raw table `T` in DuckDB | quoted relation `"raw"."T"` |
| raw table `T` in dbt | `source('raw', 'T')`, with identifier exactly `T` |
| staging/intermediate/output model `M` | file named `M.sql`, dbt model/ref name `M`, relation `"main"."M"` |
| logical column `C` | physical column named exactly `C` |

All generated SQL identifiers MUST be double-quoted by a single internal quoting helper. All SQL
and YAML literals MUST be escaped by type-aware helpers. Scenario strings MUST never be
concatenated into executable Jinja. Literal rendering MUST also neutralize Jinja delimiter text
that could occur inside an otherwise valid scenario string. The neutralization technique
(string concatenation, comment-splitting, or equivalent) is an implementation choice made in
the generator tasks; the observable contract is: after dbt parsing, every literal originating
from a scenario string MUST evaluate to that exact string value, and no scenario string content
may be interpreted as Jinja, a macro call, or a second parsing pass.

The source name `raw`, DuckDB schemas `raw` and `main`, dbt project name, profile name, and path
rules are fixed constants. Small pure functions for quoting and path construction are permitted;
a policy or provider abstraction is not.

### 5.2. Type mapping

| Scenario type | DuckDB type | Parquet logical representation |
| --- | --- | --- |
| `string` | `VARCHAR` | UTF-8 string |
| `integer` | `BIGINT` | signed 64-bit integer |
| `float` | `DOUBLE` | IEEE-754 double |
| `boolean` | `BOOLEAN` | boolean |
| `date` | `DATE` | date |
| `timestamp` | `TIMESTAMP WITH TIME ZONE` | microsecond timestamp normalized to UTC |

Generated integers MUST fit signed 64-bit storage. Timestamps MUST be normalized to UTC before
serialization. The DuckDB session timezone MUST be UTC.

### 5.3. Layer agreement

The raw generator MUST write exactly the declared raw columns, in declaration order, using the
mapping above. It MUST NOT add an index, lineage, seed, or fault-label column.

The loader MUST create the `raw` schema and load each Parquet file into the exact corresponding
raw relation. The dbt source file MUST refer to those exact relations. Staging and downstream
models MUST use resolved schemas rather than guessing whether a column survived a projection or
rename.

These rules are the complete required coordination between raw generation and dbt rendering.

## 6. Generated instance layout

A completed clean baseline MUST have a layout equivalent to:

```text
<instance>/
├── scenario.json
├── raw/
│   ├── <raw_table>.parquet
│   └── ...
├── pipeline.duckdb
├── dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   ├── models/
│   │   ├── sources.yml
│   │   ├── staging/<staging_model>.sql
│   │   ├── intermediate/<intermediate_model>.sql
│   │   ├── output/<output_model>.sql
│   │   └── assertions.yml
│   ├── macros/
│   │   └── generated_assertions.sql
│   ├── target/
│   │   ├── manifest.json
│   │   └── run_results.json
│   └── logs/dbt.log
├── instance_record.json
└── SUCCESS
```

The implementation MAY split generated YAML or macros into several deterministic files. It MUST
preserve the logical locations and distinctions above. `scenario.json` MUST contain the canonical
scenario bytes used for identity, not the author's whitespace-preserving input file.

Generated dbt text files MUST be UTF-8 with LF newlines and one final newline. The canonical
`scenario.json` snapshot is the exception: it MUST preserve the exact byte representation
returned by `canonical_json`. File enumeration and YAML/SQL resource ordering MUST be
deterministic.

## 7. Instance identity and versions

### 7.1. Scenario identifier versus content identity

`scenario_id` is a human-readable scenario identity and the grouping key used by dataset splits.
It does not prove that the file contents are unchanged.

The canonical scenario hash MUST be the lowercase SHA-256 hexadecimal digest returned by the
scenario package's canonical serialization and hashing functions. Changing scenario content
while retaining `scenario_id` MUST produce a different pipeline-instance identity and MUST NOT
reuse the old baseline.

### 7.2. Compatibility identity

The cache identity MUST include:

- canonical scenario SHA-256;
- `data_seed`;
- generator contract/version;
- raw-generation implementation version;
- SQL/dbt-renderer implementation version;
- Python `major.minor` version (patch version is excluded from the key; the full version is
  recorded in the instance record as provenance for debugging);
- DuckDB version (exact, including patch);
- dbt-core version (exact);
- dbt-duckdb version (exact);
- Faker version (exact, pinned) and locale; and
- the version of a separate Parquet writer if one is used (exact).

An output-affecting change to random sampling, physical naming, data typing, SQL rendering, test
lowering, or generated templates MUST change an implementation version or fingerprint and
therefore invalidate the cache. Absolute paths, hostnames, process IDs, wall-clock timestamps,
and invocation IDs MUST NOT participate in logical identity.

The implementation MAY encode the complete identity as a SHA-256 digest of a canonical JSON
object. A simple filesystem path such as `<cache_root>/v1/<instance_digest>/` is sufficient; a
cache service or database is prohibited.

## 8. Randomness contract

### 8.1. Root seed and named streams

All stochastic choices MUST come from named streams derived from `data_seed`. A single mutable
scenario-wide RNG is prohibited because adding or changing one generator would shift unrelated
tables and columns.

Version 1 derives a stream seed as follows:

```text
payload = canonical JSON of ["dpd-rng-v1", scenario_id, data_seed, stream_name]
subseed = unsigned big-endian integer represented by SHA-256(UTF-8(payload))
```

Here canonical JSON means no insignificant whitespace, UTF-8 encoding, and the same scalar
spelling rules used by the scenario package. All four array elements have fixed JSON types.

Each stream uses a fresh isolated PRNG initialized equivalently to
`rng = random.Random(); rng.seed(subseed, version=2)`. The Python version is part of compatibility
identity. Generator code MUST NOT use module-global random state, Python's randomized `hash()`,
the process clock, locale defaults, or an external service.

The canonical scenario hash MUST NOT be an input to stream derivation. It belongs to instance
and cache identity. Excluding it from stream derivation ensures that an unrelated scenario edit
does not perturb otherwise unchanged generator streams.

### 8.2. Required stream namespaces

At minimum, the raw plan MUST allocate independent streams equivalent to:

```text
rows/<table>
values/<table>/<column>
nulls/<table>/<column>
foreign_key/<relationship>/<dependent_table>/<target_side>
nulls/foreign_key/<relationship>/<dependent_table>/<target_side>
```

A composite foreign key uses one value stream and one null stream for the tuple, not one stream
per component. Retries consume only the stream that owns the retried choice. Stream names and the
stream-scheme version MUST be recorded or reconstructible from the instance record.

Changing the root `data_seed` changes the derivation input for every stochastic stream. Adding
or changing one column MUST NOT shift unrelated column, relationship, or table streams when
their names and generator configurations remain unchanged.

### 8.3. Meaning of seed variation

The generator guarantees reproducibility, not training novelty. Two different seeds MAY produce
the same value by chance, and a scenario containing deterministic generators may have few or no
data differences. The generator MUST NOT randomize logs, dbt execution order, diagnostic-tool
calls, or oracle actions merely to manufacture diversity.

The fault catalog decides whether a fault context is seed-sensitive. Dataset construction keeps
multiple seed variants only when their realized diagnostic evidence or justified decisions are
materially different. This empirical selection is outside the compiler.

## 9. Raw planning and execution order

### 9.1. Generation units

The raw planner MUST build dependencies among generation units rather than assuming that JSON
table order is executable order. Units include:

- a table row count;
- an ordinary non-foreign-key column;
- a template column, depending on its placeholders;
- an atomic foreign-key tuple, depending on its target key universe; and
- final table serialization, depending on every column in that table.

Independent units use scenario declaration order as the stable tie breaker. A foreign-key target
key universe MUST exist before the dependent tuple is sampled, but the target table need not be
fully serialized first.

The planner MUST use resolved relationship direction and ordered endpoint columns. It MUST NOT
infer a relationship from matching names.

### 9.2. Cycles and omitted invariants

Version 1 requires every raw key dependency to reach an independently generatable key universe.
Raw dependency cycles are semantic errors owned by `SCENARIO_SPEC.md` §17.3 and rejected by
semantic validation (`E135`) before the compiler ever runs. The raw planner therefore assumes
acyclic input and treats a cycle reaching it as a defensive component-boundary failure: it MUST
fail with the cycle and involved tables, columns, and relationships identified. It MUST NOT
invent values, silently break a relationship, or choose a different seed, and it MUST NOT treat
cycles as a routine input class.

### 9.3. Row counts

For `rows.min == rows.max`, the exact count is used without a random draw. Otherwise the table's
row-count stream samples an integer uniformly from the inclusive interval `[min, max]`.

Hard raw constraints take precedence over proposal distributions. If a sampled combination of
row counts makes a one-to-one relationship or another raw structural constraint impossible, the
generator MUST deterministically condition or resample only the involved row-count streams. It
MUST NOT fail merely because the first proposals are incompatible when the declared ranges
contain a combination known to satisfy those local raw constraints. Retry behavior and limits
are implementation-versioned and MUST NOT depend on wall-clock time.

Failure to find a materializable combination is a clean-instance failure and exposes a contract,
scenario, or generator defect. It is not permission to switch `data_seed`.

### 9.4. Row identity and ordering

Each raw table has an internal zero-based row index used during generation. It MUST NOT be written
as a data column. Rows are serialized in ascending internal row-index order and columns in
scenario declaration order.

Downstream SQL relations are unordered. Logical reproducibility comparisons MUST canonicalize
row order explicitly rather than rely on DuckDB scan order.

## 10. Mini-generator execution semantics

Every generator produces a non-null proposal. Null insertion and hard constraints are applied as
specified in section 11.

### 10.1. Deterministic identifiers

For a zero-based row index `i`, `formatted_id` produces:

```text
prefix + decimal(start + i), left-padded with ASCII "0" to exactly digits characters
```

The numeric portion MUST remain within the declared digit capacity. This generator consumes no
random stream.

### 10.2. Numeric ranges

`integer_range` samples every integer in the inclusive `[min, max]` interval with equal
probability.

`float_range` samples uniformly from the decimal lattice implied by `decimal_places`. Let
`scale = 10 ** decimal_places`; the eligible integer lattice is:

```text
ceil(Decimal(min) * scale) .. floor(Decimal(max) * scale)
```

The selected integer divided by `scale` is stored as `DOUBLE`. Conversions to `Decimal` MUST use
the canonical decimal spelling of the input, not a platform-dependent binary-float expansion.
An empty lattice is an omitted contract invariant and MUST fail explicitly.

### 10.3. Date and timestamp ranges

`date_range` samples uniformly from calendar dates in the inclusive `[min, max]` interval by
sampling an integer day offset.

`timestamp_range` first normalizes both bounds to UTC, then samples uniformly from the inclusive
integer-microsecond interval. It stores the result as a UTC timestamp. It MUST NOT use local time,
daylight-saving rules, or current time.

### 10.4. Categorical and boolean values

Without `weights`, `categorical` samples each declared value uniformly. With `weights`, weights
are normalized by their sum and used in declaration order; a zero-weight value is never selected.
The implementation MUST preserve JSON scalar type, including the distinction between `true` and
`1`.

When a categorical column must be unique, selection is without replacement and remaining
weights are renormalized after each draw. Insufficient positive-weight capacity is an explicit
generation failure.

`boolean` returns `true` when a uniform draw in `[0, 1)` is less than `true_probability`; otherwise
it returns `false`.

### 10.5. Random strings

`random_string` first samples a length uniformly from the inclusive `[min_length, max_length]`
interval, then samples each character uniformly and independently from `alphabet` in declaration
order.

### 10.6. Template strings

`template_string` is evaluated only after all placeholder columns for the row are available.
Literal text is copied verbatim and `{column_name}` is replaced by the referenced value using:

| Value type | Text form |
| --- | --- |
| string | unchanged |
| integer | base-10 decimal |
| float | Python's shortest round-trip finite representation for the pinned Python runtime |
| boolean | lowercase `true` or `false` |
| date | ISO `YYYY-MM-DD` |
| timestamp | UTC ISO-8601 with `+00:00` |

If any referenced value is null, the template result is null. No escaping, secondary parsing,
format mini-language, nested lookup, or Jinja evaluation occurs.

### 10.7. Faker-backed values

Version 1 supports exactly one Faker locale: `de_DE`. Every Faker-backed generator reaching the
compiler MUST declare that locale. The scenario contract consumed by a conforming compiler MUST
therefore restrict the field to `de_DE`; silently substituting `de_DE` for another locale is
prohibited.

The implementation MUST use one isolated Faker instance per column value stream, seeded
deterministically from that column's stream: the `values/<table>/<column>` subseed derived
per section 8.1 seeds a private `random.Random` instance (no shared global RNG, no
`Faker.unique` state shared between columns), and the Faker instance MUST draw only from it.
The exact wiring (for example `Faker.seed_instance`) is an implementation choice made in the
generator tasks; the observable contract is fixed-seed repeatability and cross-column
independence. Faker dispatch is only as follows:

| Generator kind | Faker provider call |
| --- | --- |
| `person_name` | `name()` |
| `email` | `email()` |
| `city` | `city()` |
| `street_address` | `street_address()` |
| `company_name` | `company()` |
| `phone_number` | `phone_number()` |

The Faker package version MUST be pinned and recorded. Faker MUST NOT choose row counts, nulls,
keys, relationships, cardinalities, or uniqueness behavior. Its global RNG and `.unique` state
MUST NOT be shared between columns.

### 10.8. Foreign keys

`foreign_key` has no independent scalar execution. The relationship coordinator evaluates all
components together according to section 11.3.

## 11. Nulls, keys, uniqueness, and relationships

### 11.1. Hard constraints versus distributions

The following are hard raw-data constraints:

- every primary-key tuple is non-null and unique;
- every `nullable: false` column is non-null;
- every non-null value in a `unique: true` column is unique;
- every composite foreign key is either wholly null or wholly non-null; and
- every non-null foreign-key tuple exists in its declared target key universe.

Generator probabilities are proposal distributions, not exact realized-frequency promises.
Deterministic retry, sampling without replacement, or conditioning MAY change a small sample's
observed proportions in order to satisfy hard constraints. Retrying MUST remain local to named
streams and MUST have a deterministic, implementation-versioned limit.

An exhausted finite domain or retry budget MUST produce a structured generation failure. The
generator MUST NOT truncate the table or emit invalid rows.

### 11.2. Null insertion

For an unconstrained nullable scalar column, the column's null stream makes one Bernoulli draw per
row using `null_probability`. Probability `0.0` never inserts null and `1.0` always inserts null.

For a composite foreign key, all component columns MUST have the same `nullable` value and the
same `null_probability`: one tuple-level null draw controls all components. Agreement is a
semantic invariant owned by `SCENARIO_SPEC.md` §17.3 and enforced by semantic validation
(`E111`); a mismatch never reaches a conforming compiler. The raw planner keeps a defensive
assertion for this invariant at its input boundary and MUST fail explicitly rather than use
one component's probability silently.

Null proposals that would violate a hard constraint, including a non-null template target, MAY
be deterministically retried or conditioned. The realized null fraction is not an assertion of
exact probability.

### 11.3. Relationship sampling

A target key universe is the target endpoint's distinct, wholly non-null tuples in target
row-index order. Components retain the endpoint's declared order.

Relationship sampling is:

| Cardinality | Dependent generation |
| --- | --- |
| `one_to_many` | Right endpoint samples left target tuples with replacement. |
| `many_to_one` | Left endpoint samples right target tuples with replacement. |
| `one_to_one` | The resolved dependent endpoint samples target tuples without replacement. |
| `many_to_many` | Each bridge endpoint tuple samples its corresponding target universe with replacement. The two sides are sampled independently. |

For a composite endpoint, tuple selection and null insertion are atomic. A many-to-many bridge
pair may repeat unless another declared raw constraint forbids it. Version 1 does not synthesize
coverage or participation guarantees that are absent from the scenario language.

If a dependent tuple is itself constrained to be unique by a primary key or column uniqueness,
sampling without replacement overrides the default with-replacement rule.

If a required target universe is empty or too small for one-to-one sampling, generation fails.
The implementation MUST NOT create an orphan, partially null tuple, or undeclared synthetic key.

### 11.4. Assertions are not generation instructions

Explicit healthy assertions and derived dbt tests are runtime postconditions. The raw generator
MUST NOT reverse-engineer `accepted_values`, `column_range`, `row_count`, staging filters,
`map_values(on_unmapped="error")`, or cast formats into a general constraint-solving problem.

The generator directly satisfies raw structural constraints because they define the raw layer.
It then executes the declared pipeline and tests its postconditions. A clean-control failure
exposes an incompatible scenario, a missing semantic invariant, or an implementation defect; it
does not justify assertion weakening or automatic scenario repair.

## 12. Parquet creation and DuckDB loading

The raw executor MUST produce one Parquet file per raw table with the exact schema, column order,
row count, and row values generated above. Compression and row-group settings are fixed by the
raw-generator version and recorded when they affect byte-level artifact.

The loader MUST:

1. create a fresh local `pipeline.duckdb` inside the temporary instance workspace;
2. set the session timezone to UTC;
3. create schema `raw`;
4. create one typed raw table from each corresponding Parquet file;
5. verify exact column names, order, DuckDB types, and row counts; and
6. close/checkpoint the database before hashing or publishing the baseline.

Load verification is an integration check, not a third scenario-validation stage. A mismatch
fails instance preparation.

## 13. Generated dbt project

### 13.1. Fixed configuration

The generated project MUST:

- use fixed project name `dpd_pipeline` and fixed profile name `dpd_pipeline` with target
  `clean`;
- reference `../pipeline.duckdb` through a relative profile path;
- use `dbt-duckdb` with one thread;
- materialize every staging, intermediate, and output model as a table;
- place all generated model relations in schema `main` with exact model identifiers;
- define raw tables under one source named `raw` and schema `raw`;
- quote identifiers; and
- contain no external dbt packages or network dependency.

The generated profile contains only the relative local DuckDB path and fixed adapter settings;
it MUST NOT contain credentials or secrets.

One thread is part of the deterministic execution profile. It avoids concurrency-dependent log
ordering and makes diagnostic evidence easier to reproduce.

### 13.2. References and order

Staging models use `source('raw', raw_table_name)`. All later models use `ref(model_name)`.
Intermediate files are rendered in `ValidatedScenario.topological_order`; staging and output
files use scenario declaration order. dbt references, not filename order, determine execution.

The renderer MUST fail on an unknown discriminated variant. It MUST NOT emit a passthrough model
or silently omit a construct.

## 14. SQL rendering rules

### 14.1. General rules

Generated SQL is mechanical DuckDB SQL. The renderer MUST use structured dispatch over the
scenario's discriminator fields and exhaustive tests over every closed union.

CTEs express semantic phase boundaries. They are not required for every scalar operation. A CTE
is required where a later phase must see a namespace or row set created by an earlier phase, for
example projection before derivation, derivation before filtering, and filtering before
aggregation. Empty identity phases MAY be omitted deterministically.

Where an error-producing cast or `map_values(on_unmapped="error")` could otherwise be skipped or
reordered by the DuckDB optimizer, the renderer MUST use an effective materialization barrier so
the declared staging operation order remains observable.

SQL literals are rendered by type:

- strings use single quotes with embedded quotes doubled;
- integers use base-10 decimal;
- floats use a finite round-trip representation;
- booleans use `TRUE` or `FALSE`;
- dates use typed DuckDB date literals; and
- timestamps use typed UTC timestamp literals.

### 14.2. Staging column operations

Each staging column begins as its quoted raw source column. Its operations wrap that expression
in declaration order. The target alias is applied only after the complete chain.

| Operation | Required semantics |
| --- | --- |
| `trim` | Remove leading and trailing whitespace with DuckDB `trim`. |
| `lower` / `upper` | DuckDB Unicode-aware lower/upper conversion. |
| `replace` | Replace every non-overlapping literal occurrence of `old` with `new`. |
| `map_values` | Exact string-key lookup. Null input remains null. Mapped input returns its mapped value. |
| `null_if` | Return null when the value equals any declared typed literal; otherwise retain it. |
| `coalesce` | Replace null with the declared typed literal. |
| `cast` | Strict conversion as defined below; invalid input raises a model error. |

`map_values` handles a non-null unmapped input as follows:

- `keep`: retain the input;
- `null`: return null; and
- `error`: evaluate DuckDB `error(<message>)` with `<message>` exactly
  `dpd_unmapped_value(<model>, <column>)`, where `<model>` and `<column>` are the staging
  model and column names. The message MUST NOT include the offending value, so failure
  identity stays independent of realized data.

The renderer MUST use strict `CAST`, not `TRY_CAST`. A cast from string to date or timestamp with
`format` uses DuckDB `strptime`; the date result is cast to `DATE`, and the timestamp result is
cast to `TIMESTAMP WITH TIME ZONE` under the UTC session. Without `format`, DuckDB's strict cast
is authoritative. Other casts use the physical type mapping in section 5.2.

### 14.3. Staging row operations

Column selection, rename, and column operations form the first staging CTE. Row operations then
apply in declared order, with one row-set boundary per operation:

- `filter` retains only rows for which its condition evaluates to SQL `TRUE`; and
- `deduplicate` computes `row_number()` partitioned by `keys`, ordered by every declared
  `order_by` key and direction, and retains row number 1.

Every deduplication sort term uses explicit `NULLS LAST`. The compiler MUST NOT append an implicit
tie breaker; semantic validation is responsible for admitting only a sufficient declared order.
The helper row-number column MUST not appear in model output.

Staging output columns follow `StagingModel.columns` declaration order.

### 14.4. Expressions

| Expression | DuckDB semantics |
| --- | --- |
| `column` | Quoted column in the current namespace. |
| `literal` | Typed literal rendering from section 14.1. |
| `binary:add` | Parenthesized `left + right`. |
| `binary:subtract` | Parenthesized `left - right`. |
| `binary:multiply` | Parenthesized `left * right`. |
| `binary:divide` | Safe division described below. |
| `date_part` | DuckDB `extract`; `day_of_week` is ISO weekday 1=Monday through 7=Sunday. |
| `coalesce` | DuckDB `coalesce` over values in declaration order. |

Safe division returns null when the denominator is null or equal to zero; otherwise it performs
division and returns `DOUBLE`:

```sql
case
  when <right> is null or <right> = 0 then null
  else cast(<left> as double) / cast(<right> as double)
end
```

Every nested expression is parenthesized sufficiently to make precedence independent of
formatting.

### 14.5. Conditions

Comparisons use `=`, `<>`, `<`, `<=`, `>`, and `>=`. They use ordinary SQL three-valued null
semantics; equality is not rewritten as `IS NOT DISTINCT FROM`.

`in` renders a typed `IN` predicate and applies logical `NOT` when `negated` is true. `is_null`
renders `IS NULL` or `IS NOT NULL`. `all`, `any`, and `not` render fully parenthesized `AND`, `OR`,
and `NOT` expressions. A filter retains SQL `TRUE` only.

### 14.6. Transform models

A transform uses these logical phases:

```text
source
    -> projected columns and renames
        -> all derived columns
            -> filters
```

All derived expressions in one model MUST be evaluated against the projected namespace only.
They MUST be rendered in one derived CTE and MUST NOT refer to aliases created by another derived
column in that same model. Filters are evaluated against projected plus derived columns. Multiple
filters are applied as a conjunction; stable CTE boundaries MAY preserve declaration order.
Output columns consist of projected targets in declaration order followed by derived columns in
declaration order.

### 14.7. Join models

A join uses these logical phases:

```text
left/right refs
    -> INNER or LEFT equality join on all ordered key pairs
        -> explicit side-qualified projection and rename
            -> all derived columns
                -> filters
```

Join-key equality uses ordinary SQL null semantics. No implicit column, wildcard, suffix, natural
join, or additional predicate may be emitted. Derived columns and filters follow the same
namespace and output-order rules as transform models.

### 14.8. Aggregate and output models

Aggregate intermediate models and outputs first apply every filter to the pre-aggregation input,
then group by every declared source expression, alias group keys to their target names, and render
metrics in declaration order. Multiple filters form a conjunction and retain SQL `TRUE` only.
Output columns consist of group-by targets in declaration order followed by metrics in
declaration order.

| Metric | Required SQL meaning |
| --- | --- |
| `count_rows` | `COUNT(*)`, returned as `BIGINT`. |
| `count` | Count non-null source-column values. |
| `count_distinct` | Count distinct non-null source-column values. |
| `sum` | Sum source values and cast the result to `DOUBLE`. |
| `avg` | Average source values and cast the result to `DOUBLE`. |
| `min` / `max` | DuckDB minimum/maximum preserving the validated source type. |
| `conditional_count` | Count rows whose condition is SQL `TRUE`; zero when none qualify. |
| `conditional_sum` | Sum non-null source values for rows whose condition is SQL `TRUE`, cast to `DOUBLE`; null when no qualifying non-null value exists. |

Standard DuckDB grouping semantics apply to null grouping values. An empty filtered input produces
no grouped rows and is detected by the derived non-empty output assertion.

`group_by` alone defines the physical grouping columns. `grain` causes metadata and healthy
tests; it does not add or remove SQL grouping keys. Output `dimensions` are analytical metadata
over names already present in `group_by` and do not independently change SQL.

### 14.9. Intermediate deduplication

An intermediate `deduplicate` model refs its source, ranks rows exactly as in section 14.3 using
the declared `keys` and `order_by`, and returns all source columns except the helper rank. It MUST
NOT add an implicit sort key or change output column order.

## 15. Healthy assertions and dbt tests

### 15.1. Logical assertion set

The generated project MUST test both:

- every explicit assertion in `ValidatedScenario.scenario.tests`; and
- every structural assertion required by `SCENARIO_SPEC.md`.

At minimum the structural set is:

- composite uniqueness and per-column not-null for every raw primary key;
- not-null for every raw column with `nullable: false`;
- uniqueness for every raw column with `unique: true`;
- relationship integrity for every direct dependent tuple and both sides of every bridge;
- composite uniqueness and per-column not-null for every staging, intermediate, and output
  grain; and
- row count `min = 1` for every output.

The dbt renderer MAY consume `ValidatedScenario.derived_assertions` and supplement it from
resolved structural facts. The behavior above is normative regardless of which internal object
currently supplies an assertion. A persistent unified assertion list is not required.

Before rendering, assertions MUST be deduplicated by effective semantic identity while retaining
their origin (`explicit` or `derived`) and logical name for traceability. Deduplication MUST NOT
merge assertions that have different bounds, values, column order, or relationship targets.

### 15.2. Generic-test lowering

Assertions are logical requirements. Generic tests are their reusable dbt implementation; using
a generic test does not replace, weaken, or remove the assertion.

| Logical assertion | dbt lowering |
| --- | --- |
| one-column `not_null` | dbt built-in `not_null` |
| multi-column `not_null` | one built-in `not_null` test per column |
| one-column `unique` | dbt built-in `unique` |
| composite `unique` | local `composite_unique` generic test |
| `accepted_values` | dbt built-in `accepted_values`, with type-aware quoting |
| one-column `relationships` | dbt built-in `relationships` |
| composite `relationships` | local `composite_relationships` generic test |
| `row_count` | local `row_count_between` generic test |
| `column_range` | local `column_range` generic test |

The project MUST vendor its small custom macros locally and MUST NOT depend on `dbt-utils` or
another downloaded package. Per-assertion singular SQL files are unnecessary.

All healthy tests have error severity. Warning-only clean assertions are prohibited.

### 15.3. Exact assertion semantics

- `not_null` fails once for every null value in any named column.
- `unique` applies to wholly non-null keys. For a composite key, rows with any null key component
  are excluded; separate structural not-null tests enforce non-null grains and primary keys.
- `accepted_values` ignores nulls and fails non-null values outside the typed declared set.
- `relationships` ignores wholly null dependent tuples, treats a partially null composite tuple
  as failure, and fails every non-null tuple absent from the target projection.
- `row_count` compares the model's total row count with each present inclusive bound.
- inclusive `column_range` fails values below `min` or above `max`; exclusive range fails values
  less than or equal to `min` or greater than or equal to `max`. Nulls are ignored.

A logical multi-column assertion may lower to several dbt nodes. Physical test names MUST be
derived deterministically from the logical assertion name and component role. Length handling
MUST use a stable suffix/hash helper, not a configurable naming policy.

### 15.4. Test targets and traceability

Assertions on raw tables attach to dbt sources. Assertions on staging, intermediate, or output
models attach to their dbt models. Relationship targets use `source()` for raw tables and `ref()`
for generated models.

After dbt parsing/building, the instance record SHOULD map each logical assertion to the one or
more dbt `unique_id` values that implement it. dbt's own `target/manifest.json` remains the
authority for the dbt graph.

### 15.5. No static assertion proving

Derived assertions are derived in the sense that the need for the check follows from declared
structure. This does not mean the compiler has statically proved every realized row will satisfy
it. Explicit assertions are authored runtime expectations.

The compiler MUST render and run these checks. It MUST NOT implement symbolic execution of
generators, staging operations, filters, or arbitrary assertions to prove them in advance. In
particular, it need not statically prove that every generated value is mapped by
`map_values(on_unmapped="error")`, that every generated string satisfies a cast format, that a
filter retains a row, or that an explicit range/value assertion follows from upstream domains.

## 16. Clean-control execution

Instance preparation MUST run in a fresh temporary workspace. After raw loading and dbt project
rendering, it invokes the equivalent of:

```text
dbt build --profiles-dir . --target clean --threads 1
```

The command runs from the generated `dbt/` directory under a fixed UTC environment. Partial
state from another instance MUST NOT be reused. All models and healthy tests participate in the
build.

A clean control succeeds only when:

- Parquet creation and raw loading complete;
- every dbt model builds successfully;
- every explicit and structural healthy test passes;
- `manifest.json` and `run_results.json` are readable and correspond to this project; and
- the generated artifacts pass the integrity checks required for publication.

dbt invocation timestamps, durations, invocation IDs, and nondeterministic log formatting are
observability metadata. They need not be byte-identical and MUST be excluded from logical
identity. Model results, raw data, rendered project files, graph, row counts, and pass/fail outcome
MUST be logically reproducible for an identical instance identity.

## 17. Failure semantics

Any failure before `SUCCESS` prevents cache publication and downstream fault injection. The
system MUST NOT:

- choose another `data_seed`;
- silently omit the scenario or failed assertion;
- weaken a test or change error severity;
- coerce invalid values with `TRY_CAST`;
- create orphan keys or truncate rows;
- continue emitting trajectories merely to reach a target count; or
- mark a partial directory as a clean baseline.

The failed workspace SHOULD be preserved or copied to a failure-artifact location. A structured
`failure_record.json` MUST include at least:

- the complete attempted identity;
- failure stage (`raw_plan`, `raw_generate`, `raw_load`, `dbt_render`, `dbt_build`,
  `integrity`, or `cache_publish`);
- stable error category and message;
- command and exit status when a subprocess was involved; and
- relative paths to available logs, `manifest.json`, and `run_results.json`.

`failure_record.json` MUST have exactly these logical fields (all required unless marked
optional):

```json
{"identity": {"scenario_sha256": "...", "scenario_id": "...", "data_seed": 0,
              "generator_contract": "...", "raw_generator": "...", "dbt_renderer": "..."},
 "stage": "raw_plan | raw_generate | raw_load | dbt_render | dbt_build | integrity | cache_publish",
 "category": "...", "message": "...",
 "command": [], "exit_status": null,
 "paths": {"log": null, "manifest": null, "run_results": null}}
```

`command` is the subprocess argv (empty when no subprocess was involved); `exit_status` is
null in the same case; `paths` entries are instance-relative paths or null when the file is
unavailable. It MUST NOT contain a hidden fault label because clean preparation has no fault.
Large logs and tracebacks belong in separate files rather than inline JSON.

A failure for a `ValidatedScenario` is evidence of a compiler/generator/runtime defect, an
omitted semantic invariant, or data-incompatible scenario content (for example an explicit
assertion bound outside the generator domain, an unmapped `map_values(on_unmapped="error")`
input, a cast-format mismatch, or a filter retaining no rows; see section 11.4). The project
fixes that cause — code or runtime fix with retry of the same identity, or scenario edit with
revalidation — and does not turn compilation into a third routine corpus filter.

## 18. Instance record

### 18.1. Role

`instance_record.json` is the reproducibility passport for one clean baseline. It answers which
scenario content, seed, code/runtime versions, physical artifacts, raw row counts, dbt resources,
and tests produced the successful instance.

It is distinct from dbt's generated `target/manifest.json`:

- dbt's manifest describes dbt resources and dependency graph;
- the instance record describes the whole raw-plus-dbt pipeline instance and points to the dbt
  artifacts.

No third compiler manifest is required. `RawPlan` remains an internal in-memory object.

### 18.2. Required content

The record MUST be versioned and contain at least these logical sections:

```json
{
  "record_version": "1.0",
  "status": "success",
  "identity": {
    "instance_digest": "...",
    "scenario_id": "...",
    "scenario_schema_version": "1.0",
    "scenario_sha256": "...",
    "data_seed": 0
  },
  "versions": {
    "generator_contract": "...",
    "raw_generator": "...",
    "dbt_renderer": "...",
    "python": "3.14",
    "python_full": "3.14.7",
    "duckdb": "...",
    "dbt_core": "...",
    "dbt_duckdb": "...",
    "faker": "...",
    "faker_locale": "de_DE"
  },
  "randomness": {
    "stream_scheme": "dpd-rng-v1",
    "streams": []
  },
  "raw_tables": [],
  "dbt": {},
  "artifacts": []
}
```

Each `raw_tables[]` entry MUST have exactly these logical fields (all required):

```json
{"name": "...", "row_count": 0, "duckdb_relation": "\"raw\".\"T\"",
 "parquet_path": "raw/T.parquet",
 "columns": [{"name": "C", "duckdb_type": "VARCHAR"}],
 "sha256": "..."}
```

`sha256` is the lowercase hex digest of the Parquet file bytes. The `dbt` section MUST have
exactly these fields: `command` (the rendered `dbt build` argv), `exit_status`,
`project_dir`, `log_path`, `manifest_path`, and `run_results_path` (all paths relative to
the instance root). Each `artifacts[]` entry MUST have `path` (relative), `kind` (one of
`scenario`, `parquet`, `duckdb`, `dbt_project`, `dbt_target`, `log`, `record`, `marker`),
`size_bytes`, and `sha256` of the file bytes (`SUCCESS` marker: digest of its content).
Extra informational fields are prohibited in these three sections; crosswalks and volatile
observability belong in the `SHOULD`/`MAY` sections below. The randomness section MUST list
every allocated stream name; numeric sub-seeds need not be repeated because they are
deterministically reconstructible. In `versions`, `python` is the `major.minor` cache-key
component from section 7.2 while `python_full` is provenance only and MUST NOT affect
`instance_digest` or logical-equivalence checks.

The record SHOULD additionally include:

- logical model name to dbt `unique_id` and physical relation mappings;
- logical assertion name/origin to dbt `unique_id` mappings;
- realized row counts for materialized dbt models; and
- deterministic logical table checksums used by reproducibility tests.

These crosswalks aid diagnostics but do not replace `ValidatedScenario` or dbt's manifest.

Absolute paths and timestamps MAY be recorded in an explicitly volatile observability section,
but they MUST NOT affect `instance_digest` or logical-equivalence checks. The record MUST NOT
inline raw rows, full logs, secrets, fault configuration, hidden labels, or oracle-only metadata.

### 18.3. Publication order

`instance_record.json` is written only after the successful clean build and artifact inventory.
The `SUCCESS` marker is written last. A cache entry without both a matching successful record and
the final marker is incomplete.

## 19. Cache and clean-baseline handoff

Before building, the preparer checks the exact cache identity. It may reuse an entry only when:

- the identity components exactly match;
- `SUCCESS` exists;
- `instance_record.json` has status `success` and matches the path/key;
- every required artifact exists; and
- recorded integrity checks pass.

A miss builds in a sibling temporary directory. Publication MUST be atomic or race-safe so two
workers cannot expose a partial entry. A failed or mismatched entry is never repaired in place or
reused as success.

The cached baseline is immutable. The fault subsystem receives an isolated copy, copy-on-write
clone, or deterministic reconstruction. Fault injection, dbt reruns under a fault, and diagnostic
logs MUST occur outside the cached clean directory.

## 20. Deliberate non-goals

The implementation MUST remain proportionate to a finite, one-time dataset-generation project.
The following are restated here only as scope reminders; each points to the normative section
that already prohibits or bounds it, and no item below adds or removes a requirement:

- a general compilation IR, optimizer, or multi-stage `CompilationPlan` (see section 4.3);
- a configurable naming-policy hierarchy (see section 5.1);
- a multi-dialect SQL abstraction or external warehouse adapters (see section 2);
- arbitrary generator/provider plugins (see sections 10.7–10.8);
- a universal assertion-driven raw-data constraint solver or symbolic reachability analysis
  for mappings, casts, filters, or assertions (see section 15.5);
- a general solver for cyclic foreign-key universes — cycles are semantic errors (see
  sections 9.2 and 15.5);
- a separate compiler manifest in addition to `instance_record.json` and dbt's manifest
  (see section 18.1);
- a service-backed cache or distributed scheduler (see sections 7.2 and 19); or
- proof that every new `data_seed` creates novel diagnostic evidence (see section 8.3).

Cheap internal assertions and clear failures at component boundaries are permitted. They MUST
not become a hidden second validity language or silently narrow the accepted corpus.

## 21. Conformance tests

The implementation is conforming only when automated tests cover sections 21.1–21.4.
Purpose-built fixtures are the conformance gate: corpus execution supplements those
fixtures and verifies that the actual dataset source is materializable; it does not replace
feature-level tests.

### 21.0. Minimum gate

The minimum gate that unblocks downstream work (see section 22) is:

- one positive fixture per closed-union variant (generator kind, staging operation,
  expression, condition, intermediate operation, metric, assertion type);
- one deliberately failing clean control per custom generic-test family
  (`composite_unique`, `composite_relationships`, `row_count_between`, `column_range`);
- fixed-seed repeatability, named-stream isolation, and one controlled stochastic fixture in
  which changing `data_seed` changes generated data; and
- one end-to-end clean control: parse -> semantic validate -> raw plan -> Parquet ->
  DuckDB -> dbt build -> successful record, plus a verified cache hit on repeat.

The remaining items in sections 21.1–21.4 are the full conformance target and SHOULD be
completed in the same proportions as the generator is extended; they MUST be complete before
the one-time dataset generation run finishes.

### 21.1. Raw generation

- every mini-generator kind, edge bound, and physical type;
- exact and ranged row counts;
- fixed-seed repeatability and named-stream isolation;
- a controlled stochastic fixture in which changing `data_seed` changes generated data;
- no requirement that every scenario or every seed pair differ;
- null probabilities `0.0` and `1.0` plus ordinary nullable columns;
- primary, single unique, composite key, and finite-capacity behavior;
- all direct cardinalities, many-to-many bridges, composite tuples, optional foreign keys, and
  one-to-one sampling without replacement;
- template dependency order and null propagation; and
- Faker dispatch using pinned `de_DE` instances.

### 21.2. SQL and dbt rendering

- snapshots or equivalent structural checks for every staging column and row operation;
- every expression and condition variant, including safe division and ISO day of week;
- transform and join namespace boundaries;
- every intermediate operation, output aggregation, and metric;
- deterministic deduplication and explicit null ordering;
- exact raw-source/model/column physical naming; and
- stable rendering independent of map iteration and process hash randomization.

### 21.3. Assertions

- every explicit assertion variant;
- every structural assertion listed in section 15.1, including output-grain uniqueness and
  not-null tests;
- standard versus custom generic-test selection;
- composite uniqueness and relationship null semantics;
- inclusive/exclusive and one-sided bounds;
- stable logical-to-dbt test mapping; and
- a deliberately failing clean control for each custom test family.

### 21.4. End-to-end behavior

- parse -> semantic validate -> raw plan -> Parquet -> DuckDB -> dbt build -> successful record;
- repeated preparation of one identity returns a verified cache hit;
- identical identities produce logically identical artifacts and data;
- changes to canonical scenario content, `data_seed`, generator versions, or compatibility
  versions produce cache misses;
- a failed build never produces `SUCCESS`;
- a cached baseline remains unchanged after a fault-workspace copy is modified; and
- before the one-time dataset generation run, every accepted corpus scenario completes at
  least one clean seed (dataset-build gate; not part of the section 22 readiness gate).

Purpose-built fixtures MUST cover the complete language. Corpus execution supplements those
fixtures and verifies that the actual dataset source is materializable; it does not replace
feature-level tests.

Tests MUST run without network access and without a long cloud job.

## 22. Readiness criteria

The pipeline generator is ready for downstream use only when:

- the core API rejects a bare `Scenario` and accepts `ValidatedScenario`;
- all version-1 constructs have deterministic raw or SQL semantics;
- the physical naming/type contract is exercised end to end;
- explicit and structural assertions are blocking and traceable;
- the instance identity, record, cache verification, and immutable handoff are implemented;
- the Faker dependency is pinned, the supported locale is `de_DE`, and the scenario contract
  cannot validate another locale for this compiler contract; and
- the minimum conformance gate from section 21.0 and the project's configured quality gates
  pass.

Readiness does not require the full corpus clean-control run: per `PIPELINE_SPEC.md` §5 the
compiler lifecycle and the corpus lifecycle are separate. The full-corpus clean-control run
is the dataset-build gate from section 21.4 and MUST complete before the one-time dataset
generation run, but it MUST NOT block downstream fault-subsystem development against
fixture-built baselines.

Any disagreement among this document, `PIPELINE_SPEC.md`, `SCENARIO_SPEC.md`, the implemented
validator, and the compiler MUST be resolved explicitly. The generator MUST not compensate for a
contract mismatch with silent coercion or hidden support rules.
