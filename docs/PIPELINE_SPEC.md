# Healthy Synthetic Data Pipeline Specification

Status: draft.

## 1. Purpose

This document defines the top-level architecture, component boundaries, lifecycle, and acceptance criteria for the healthy synthetic data pipeline in the SFT fault-diagnosis project.

A healthy pipeline is a deterministic DuckDB + dbt workload that is internally consistent, can be rebuilt from declared inputs, and passes all healthy validation gates before any fault is injected. It provides the known-good baseline from which faulty diagnostic environments are later derived.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 2. Scope

Each scenario MUST describe a compact analytical pipeline with:

- 3–4 raw tables;
- a four-layer lineage: `raw → staging → intermediate → output`;
- 2–3 intermediate models; and
- 1–2 output models.

The staging layer performs source-oriented cleaning and normalization. The intermediate layer performs relational and business transformations. The output layer exposes explicitly grained analytical results and metrics.

This specification covers:

- the sources of truth for the scenario language;
- the implementation and per-scenario lifecycles;
- the responsibilities and interfaces of the authoring, validation, compilation, generation, execution, and provenance components;
- healthy acceptance gates;
- determinism and reproducibility; and
- required dimensions of structural diversity across the scenario corpus.

## 3. Non-goals

This document does not define:

- individual Pydantic classes, fields, unions, validators, or the complete `scenario.json` vocabulary;
- detailed instructions or prompts for LLM scenario authors;
- the execution algorithm of each raw-data mini-generator;
- SQL templates, dbt project layout, or renderer internals;
- fault families, fault applicability, fault injection, diagnostic tools, supervision generation, training, or evaluation.

Fault injection is downstream of this specification. A fault MUST NOT be injected into a scenario that has not first reached the accepted healthy state.

## 4. Sources of truth and specification boundaries

The scenario language is implemented before scenarios are authored. Its sources and derived artifacts have the following roles:

| Artifact | Normative responsibility |
| --- | --- |
| `PIPELINE_SPEC.md` | Defines cross-component architecture, lifecycle, healthy-state invariants, acceptance gates, and corpus-level diversity. |
| `SCENARIO_SPEC.md` | Specifies the complete Pydantic implementation for the scenario language: models, nested types, discriminated unions, local validators, and serialization rules. It also enumerates the cross-object invariants enforced by the separate semantic validator. |
| Pydantic scenario code | Executable implementation produced from `SCENARIO_SPEC.md`. It is the runtime authority for parsing and structural/local validation and SHOULD generate any JSON Schema or other machine-readable contract exposed to authoring tools. |
| Semantic validator code | Executable implementation of the global invariants defined by `SCENARIO_SPEC.md`; it operates after Pydantic parsing. |
| `SCENARIO_AUTHORING.md` | Tells an LLM or human how to compose useful, diverse, non-degenerate scenarios from the implemented scenario language. It defines authoring policy, not runtime validity. |
| `GENERATOR_SPEC.md` | Defines how a validated scenario is compiled and materialized: mini-generator semantics, relationship-aware raw generation, SQL/dbt rendering, loading, and generated artifact layout. |

If the Pydantic or semantic-validator implementation disagrees with `SCENARIO_SPEC.md`, the implementation is defective and MUST be corrected. `SCENARIO_AUTHORING.md` MUST NOT introduce constructs absent from the implemented Pydantic contract. `GENERATOR_SPEC.md` MUST NOT reinterpret a valid scenario construct incompatibly with `SCENARIO_SPEC.md`.

## 5. Core concepts and artifacts

### 5.1. Scenario

`scenario.json` is a declarative program in the implemented scenario language. It describes raw tables and columns, relationships, staging transformations, intermediate dependencies and operations, output grain, dimensions, metrics, and healthy tests. Variation in table names, columns, or DAG topology does not make the contract open-ended: every construct MUST be an instance of a Pydantic type defined by the scenario language.

### 5.2. Mini-generator

A mini-generator is a small, typed, deterministic raw-data primitive used as a building block in a column or relationship definition. Examples include formatted identifiers, bounded integers or floats, categorical values, dates or timestamps, booleans, template strings, and foreign-key sampling.

`SCENARIO_SPEC.md` owns the configuration contract for each mini-generator. `GENERATOR_SPEC.md` owns its execution semantics. Mini-generators are not free-form code, prompts, or domain-specific Faker functions. Relationship-aware generators MUST coordinate dependent keys rather than generate related columns independently.

### 5.3. Materialization inputs

A pipeline instance is materialized from:

- a structurally and semantically valid `scenario.json`; and
- a `data_seed` controlling every stochastic choice in raw-data generation.

The seed MAY be supplied outside `scenario.json`, but it MUST be recorded in provenance. Domain labels primarily provide semantic metadata: entity meaning, vocabulary, plausible value sets, and metric meaning. The compiler MUST derive behavior from explicit scenario constructs rather than hidden domain-specific conventions.

### 5.4. Healthy manifest and provenance

Every accepted instance MUST produce a machine-readable healthy manifest or equivalent provenance record containing at least:

- scenario identifier and scenario-language version;
- content identity of `scenario.json`;
- `data_seed`;
- compiler/generator and relevant runtime versions;
- generated raw tables and row counts;
- resolved dbt model DAG and model grains;
- generated artifact identities or locations; and
- the result of every healthy validation gate.

Failure records MUST identify the failed gate and retain enough context to reproduce it. Hidden infrastructure metadata MUST remain separable from any later diagnostic-model input.

## 6. Required lifecycle

There are two ordered lifecycles. Implementations MUST NOT begin routine scenario authoring before the scenario-language implementation exists and can validate authoring output.

### 6.1. Language implementation lifecycle

| Stage | Required input | Required output |
| --- | --- | --- |
| 1. Specify | `PIPELINE_SPEC.md` and `SCENARIO_SPEC.md` | Complete normative scenario-language design and semantic invariants. |
| 2. Implement contract | `SCENARIO_SPEC.md` | Pydantic models, unions, local validators, serialization behavior, and a machine-readable authoring contract generated from them where useful. |
| 3. Implement semantics | Semantic invariants from `SCENARIO_SPEC.md` | Separate semantic validator for graph-wide and cross-object checks. |
| 4. Implement compiler | `GENERATOR_SPEC.md` and validated scenario types | Raw-data generator and dbt-project generator consuming the same validated in-memory scenario representation. |

### 6.2. Scenario instance lifecycle

| Stage | Required input | Required output or gate |
| --- | --- | --- |
| 1. Author | Implemented Pydantic contract, its generated machine-readable representation where available, and `SCENARIO_AUTHORING.md` | Candidate `scenario.json`. LLM authoring is a distinct, fallible stage and MUST NOT be treated as validation or compilation. |
| 2. Parse and validate locally | Candidate `scenario.json` | Pydantic model instance. Reject malformed structure, invalid types or discriminators, missing fields, unsupported operations, and invalid local field combinations. |
| 3. Validate globally | Pydantic model instance | Semantically valid scenario. Resolve names and lineage; verify compatible types, keys and relationships, acyclic dependencies, reachable inputs, valid grains and aggregations, and other cross-object invariants. |
| 4. Compile | Semantically valid scenario | Deterministically generated raw-data plan and dbt project. The scenario compiler consists of a raw-data generator and a dbt-project generator; both MUST consume the same validated representation. |
| 5. Validate dbt structure | Generated dbt project | Successful `dbt parse` and `dbt compile`. |
| 6. Materialize raw layer | Raw-data plan and `data_seed` | Generated raw artifacts loaded into DuckDB with declared keys, relationships, types, and row-count constraints satisfied. |
| 7. Execute models | Loaded DuckDB and compiled dbt project | Successful `dbt run` across staging, intermediate, and output layers. |
| 8. Test healthy behavior | Completed dbt run | Successful `dbt test`; tests assert the intended healthy invariants. |
| 9. Accept | All previous gates and their evidence | Healthy manifest/provenance and an immutable accepted healthy baseline eligible for downstream fault injection. |

Failure at any gate MUST reject that instance as healthy. Later gates MUST NOT be used to waive an earlier failure.

## 7. Compiler responsibilities

The scenario compiler, also called the pipeline generator, has two coordinated components:

- The **raw-data generator** executes mini-generators in dependency order, creates independent key universes before dependent tables, resolves declared relationships and cardinalities, and produces data that can be loaded into DuckDB.
- The **dbt-project generator** mechanically renders sources, staging models, intermediate models, output models, configuration, healthy tests, and required project files.

Rendering MUST be deterministic and rule-based. An LLM MUST NOT generate or modify SQL during scenario compilation. Both components MUST agree on identifiers, types, keys, relationships, lineage, and grain.

## 8. Healthy acceptance gates

An instance is healthy only if all of the following pass in order:

1. Pydantic structural and local validation;
2. semantic validation;
3. dbt parse and compile;
4. raw-data generation and DuckDB load;
5. `dbt run`;
6. `dbt test`; and
7. manifest/provenance completeness checks.

Passing dbt tests alone is insufficient. Empty or degenerate outputs, unresolved declared inputs, relationship violations, and provenance gaps MUST be rejected even if dbt reports success.

## 9. Determinism and reproducibility

Given identical `scenario.json` content, `data_seed`, compiler/generator version, and declared runtime environment, the system MUST reproduce:

- logically identical raw data;
- identical generated dbt source, model, configuration, and test files;
- the same resolved DAG, model grains, and row counts;
- the same validation outcomes; and
- an equivalent healthy manifest, excluding explicitly documented volatile fields such as timestamps or absolute paths.

All randomness MUST derive from named, recorded seed streams. Iteration order, locale, current time, process-specific hash randomization, or undeclared external services MUST NOT affect accepted artifacts.

## 10. Structural diversity requirements

The scenario corpus MUST vary executable structure, not only domain vocabulary, names, literal values, or seeds. Authoring policy and corpus checks SHOULD cover variation in:

- DAG topology, dependency depth, branching, and reuse of upstream models;
- join type, join order, join placement, and relationship cardinality, including 1:1, 1:N, N:1, and N:M where meaningful;
- staging transformations such as rename, cast, normalization, null handling, and technical deduplication;
- filters and derived columns;
- business deduplication and relational transformations in intermediate models;
- aggregation before or after joins where semantically valid;
- output grain, dimensions, groupings, and metric functions; and
- allocation of logic among staging, intermediate, and output layers.

Every significant raw source SHOULD contribute to at least one accepted output. Scenarios with different domains but isomorphic DAGs and equivalent operations count as semantic variation, not new structural coverage. Additional `data_seed` values improve value-level robustness but do not create new structural scenarios.

## 11. Exit condition

The healthy-pipeline subsystem is ready for use by the fault subsystem only when the scenario language, validators, compiler components, ordered gates, and provenance mechanism are implemented, and at least one scenario can be recreated from its declared inputs and accepted through every gate. No downstream fault, oracle, training, or evaluation workflow may treat a partially validated instance as healthy.
