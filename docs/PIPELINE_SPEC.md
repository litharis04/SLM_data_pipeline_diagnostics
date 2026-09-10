# Synthetic Data Pipeline Specification

Status: draft.

## 1. Purpose

This document defines the top-level architecture and responsibility boundaries of the
synthetic data pipeline used by the SFT fault-diagnosis project. It connects the scenario
language, scenario authoring, deterministic compilation, clean pipeline materialization,
and the downstream fault subsystem.

A candidate may be accepted into the scenario corpus only after it has passed both local
Pydantic validation and global semantic validation. Executing dbt is not an additional scenario
validation stage. Execution applies to a concrete pipeline instance, which is identified by
the validated scenario, a data seed, and the compiler/runtime versions.

Before downstream code creates fault variants for a concrete instance, it MUST establish one
successful clean control for that instance and MUST reuse the resulting cached clean baseline
for every fault variant with the same identity.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 2. Scope

Each scenario describes a compact analytical pipeline with:

- 3–4 raw tables;
- a four-layer lineage: `raw → staging → intermediate → output`;
- exactly one staging model for each raw table;
- 2–3 intermediate models; and
- 1–2 output models.

This specification defines:

- the sources of truth and contracts between subsystems;
- the distinction between scenario validity and pipeline-instance health;
- the language, authoring, compiler-conformance, and instance-materialization lifecycles;
- compiler and generator responsibilities;
- clean-control, caching, and failure semantics;
- determinism and runtime provenance requirements;
- the top-level structural-diversity requirement for the scenario corpus; and
- the boundary between scenario coverage and downstream supervision sampling.

This specification does not define:

- individual Pydantic fields, unions, validators, or the complete `scenario.json` vocabulary;
- the detailed scenario-authoring prompt, coverage algorithm, or coverage quotas;
- the execution algorithm of each raw-data mini-generator;
- SQL templates, dbt project layout, or renderer internals;
- fault families, fault applicability, or fault-injection mechanics;
- detailed diagnostic-tool and oracle behavior;
- SFT serialization, training hyperparameters, or evaluation thresholds.

Those details belong to the specifications named below.

## 3. Sources of truth and specification boundaries

| Artifact | Normative responsibility |
| --- | --- |
| `PIPELINE_SPEC.md` | Defines cross-component architecture, lifecycles, clean-instance guarantees, determinism, and subsystem boundaries. |
| `SCENARIO_SPEC.md` | Defines the complete Pydantic scenario language and the invariants enforced by local and semantic validation. |
| Pydantic scenario code | Executable authority for JSON parsing and structural/local validation; source of generated JSON Schema. |
| Semantic validator code | Executable authority for graph-wide and cross-object validation; produces `ValidatedScenario`. |
| `SCENARIO_AUTHORING.md` | Defines the complete agent or human workflow for producing a useful, diverse corpus from the implemented scenario language. |
| `GENERATOR_SPEC.md` | Defines deterministic raw generation, SQL/dbt rendering, materialization, clean execution, cache layout, and generated artifacts. |
| Downstream fault specifications | Define fault applicability, assignment, injection, reset, and validation without changing scenario validity. |
| `TRAINING.md` and `EVALUATION.md` | Define trajectory selection, dataset serialization, grouped splits, learning-curve procedure, training, and metrics. |

If the Pydantic or semantic-validator implementation disagrees with `SCENARIO_SPEC.md`, the
implementation is defective and MUST be corrected. `SCENARIO_AUTHORING.md` MUST NOT introduce
constructs absent from the scenario contract. `GENERATOR_SPEC.md` MUST implement every valid
scenario construct without a second hidden subset of supported inputs.

## 4. Core concepts and artifacts

### 4.1. Candidate and validated scenario

An authored `scenario.json` is a candidate. Successful strict Pydantic parsing produces a
structurally valid `Scenario`. Successful semantic validation produces a `ValidatedScenario`.
Only a `ValidatedScenario` is a valid scenario and may be counted in the scenario corpus or
passed to the compiler.

Scenario validity is determined entirely by these two validation stages. Compilation,
materialization, dbt execution, and runtime assertions MUST NOT act as additional routine
filters over the authored corpus.

### 4.2. Mini-generator

A mini-generator is a small, typed raw-data primitive declared by a scenario. Mini-generators
are closed-vocabulary data, not arbitrary code or prompts. `SCENARIO_SPEC.md` owns their input
contract; `GENERATOR_SPEC.md` owns their deterministic execution semantics.

Relationship-aware generation MUST coordinate dependent keys rather than generate related
columns independently.

### 4.3. Pipeline instance

A pipeline instance is identified by:

```text
canonical scenario content + data_seed + compiler/generator versions + runtime compatibility identity
```

`data_seed` is supplied outside `scenario.json` and controls every stochastic choice in raw
generation. Changing only the seed creates a different instance of the same scenario.

A **clean instance** is an instance materialized and executed without an injected fault. A
**faulty instance** is derived from the corresponding clean baseline by the downstream fault
subsystem.

### 4.4. Clean baseline and instance record

A clean baseline is the immutable or reproducibly reconstructible set of raw data, generated
dbt artifacts, database state, and execution evidence for one pipeline-instance identity. It
exists to provide the counterfactual from which one or more fault variants are created.

Each successful clean control MUST produce a machine-readable instance record containing at
least:

- scenario identifier and scenario-language version;
- canonical content identity of `scenario.json`;
- `data_seed`;
- compiler/generator and relevant runtime versions;
- generated raw-table row counts;
- generated artifact identities or locations; and
- successful clean-control status.

The instance record contains the runtime provenance needed to reproduce the concrete pipeline
instance. Infrastructure-only metadata and future hidden fault metadata MUST remain separable
from the observations exposed to a diagnostic model.

## 5. Required lifecycles

The project has separate lifecycles for the language, scenario corpus, compiler, and concrete
pipeline instances. They MUST NOT be collapsed into one sequence of per-scenario acceptance
gates.

### 5.1. Language implementation

| Stage | Required input | Required output |
| --- | --- | --- |
| 1. Specify language | `PIPELINE_SPEC.md` and `SCENARIO_SPEC.md` | Complete scenario-language design and semantic invariants. |
| 2. Implement contract | `SCENARIO_SPEC.md` | Pydantic models, local validators, parsing, serialization, and generated machine-readable contract. |
| 3. Implement semantics | Semantic invariants from `SCENARIO_SPEC.md` | Separate semantic validator producing `ValidatedScenario`. |
| 4. Specify and implement compiler | `GENERATOR_SPEC.md` and `ValidatedScenario` | Raw-data generator and dbt-project generator consuming the same validated representation. |

### 5.2. Scenario authoring

For each scenario, authoring follows this correction loop:

1. choose the next coverage need from the accepted corpus and current coverage target;
2. write one candidate `scenario.json` directly;
3. parse and validate it locally with the implemented Pydantic contract;
4. correct local failures before proceeding;
5. run semantic validation;
6. correct semantic failures until a `ValidatedScenario` is produced; and
7. add only that validated scenario to corpus counts and recompute coverage needs.

The current candidate MUST be resolved or deliberately abandoned before another candidate is
counted. After every accepted scenario, the author MUST record its complete actual requirement
claims and recompute the corpus counts in `scenarios/COVERAGE.md`. Corpus-wide correctness is
established by the fresh-session independent audit defined in `SCENARIO_AUTHORING.md`.

Authoring does not compile, materialize, or execute the candidate. dbt behavior is not an
authoring acceptance criterion. `SCENARIO_AUTHORING.md` owns the complete workflow, prompts,
coverage dimensions, quota policy, examples, naming, correction rules, and corpus completion
criteria.

### 5.3. Compiler conformance

Compiler correctness is established by implementation tests. The conformance suite MUST cover
every language variant, representative cross-feature combinations, and full end-to-end
generation and execution.

Conformance checks run when the compiler, generator, templates, scenario contract, or relevant
runtime dependencies change. Purpose-built fixtures provide complete language coverage, and
selected corpus scenarios MAY supplement them.

### 5.4. Pipeline-instance preparation

When downstream work first requests a particular pipeline-instance identity:

1. look for an exact successful clean baseline in the cache;
2. if none exists, compile the `ValidatedScenario` into a raw-data plan and dbt project;
3. materialize and load raw data using `data_seed`;
4. execute the unmodified pipeline and its generated and explicit healthy assertions;
5. on success, record and cache the clean baseline; and
6. give downstream fault injection an isolated copy or reproducible reconstruction of that
   baseline.

Later fault variants with the same identity MUST reuse the successful baseline when its cache
identity and artifact integrity still match. Fault injection MUST NOT mutate the cached clean
baseline itself.

Pipeline-instance preparation occurs on demand when downstream work first requests an exact
instance identity.

## 6. Compiler and generator responsibilities

The scenario compiler, also called the pipeline generator, has two coordinated components:

- The **raw-data generator** executes mini-generators in dependency order, creates independent
  key universes before dependent tables, resolves declared relationships and cardinalities,
  and produces loadable data satisfying raw structural constraints.
- The **dbt-project generator** mechanically renders sources, staging models, intermediate
  models, output models, configuration, healthy assertions, and required project files.

Both components MUST consume the same `ValidatedScenario` and agree on identifiers, types,
keys, relationships, lineage, and grain. Rendering MUST be deterministic and rule-based.

The compiler MUST treat every `ValidatedScenario` as supported input. It MUST NOT reject constructs that passed the public contract, or infer
behavior from a domain label or naming convention.

For every `ValidatedScenario`, supported `data_seed`, and declared runtime environment, the
combined compiler and generator MUST be capable of producing a clean instance that satisfies
the scenario's generated and explicit healthy assertions. A counterexample exposes a defect
or an omitted contract invariant; it is not normal corpus filtering.

## 7. Verification and failure semantics

| Concern | Mechanism | When it runs | Failure meaning |
| --- | --- | --- | --- |
| JSON grammar and local invariants | Strict Pydantic validation | Every authored candidate | Correct or abandon the current candidate. |
| Cross-object semantics | Semantic validation | Every structurally valid candidate | Correct or abandon the current candidate. |
| Corpus usefulness and diversity | Coverage document plus fresh-session independent audit | Update after each accepted scenario; audit after reported quota completion and after every remediation pass | Coverage remains incomplete or the corpus returns to authoring. |
| Compiler implementation | Compiler conformance and integration tests | On relevant implementation or dependency changes | Compiler, generator, template, or contract defect. |
| Concrete clean instance | Cached clean control | Once on first use of an exact instance identity | The dataset build aborts pending a project fix. |
| Fault reproducibility and diagnosis | Downstream fault and oracle checks | For generated fault variants | Downstream defect or unsuitable fault assignment. |

A clean-control failure MUST stop the current downstream dataset build. The system MUST NOT
silently discard the scenario, choose another seed, weaken an assertion, continue accepting
other generated trajectories, or emit trajectories from the failed baseline merely to
preserve a target example count.

The failure record MUST preserve enough context to reproduce and classify the problem. The
project MUST correct the responsible specification, semantic invariant, compiler/generator
behavior, or runtime integration before restarting the build. A compiler, generator, or
runtime fix that preserves the inputs MUST retry the same identity. If the correction changes
the scenario language's validity boundary, the evolution rules in `SCENARIO_SPEC.md` apply,
the corpus MUST be revalidated, and any newly invalid scenario MUST be explicitly replaced.

## 8. Cache identity and reuse

The clean-baseline cache key MUST include at least:

- canonical `scenario.json` content identity;
- `data_seed`;
- compiler and raw-generator versions; and
- every dbt, DuckDB, adapter, or other runtime version declared compatibility-relevant by
  `GENERATOR_SPEC.md`.

A cache entry MUST NOT be reused after any identity component changes or when artifact
integrity cannot be verified. Volatile metadata such as timestamps and absolute paths MAY be
recorded, but MUST NOT change logical content identity.

One clean baseline MAY serve any number of fault variants. The association between the clean
identity and each hidden fault configuration belongs to the downstream fault subsystem.

## 9. Determinism and reproducibility

Given identical canonical scenario content, `data_seed`, compiler/generator versions, and
declared runtime environment, the system MUST reproduce:

- logically identical raw data;
- identical generated dbt source, model, configuration, and test files;
- the same resolved DAG, model grains, and row counts;
- the same clean-control result; and
- an equivalent instance record, excluding documented volatile fields.

All randomness MUST derive from named, recorded seed streams. Iteration order, locale, current
time, process-specific hash randomization, or undeclared external services MUST NOT affect
logical artifacts or clean-control outcomes.

## 10. Structural diversity

The scenario corpus MUST vary executable structure, not only domain vocabulary, names, literal
values, or data seeds. At minimum, the authoring coverage plan MUST consider:

- DAG topology, depth, branching, and upstream reuse;
- join type, order, placement, and relationship cardinality, including 1:1, 1:N, N:1, and N:M;
- staging normalization, typing, null handling, filtering, and deduplication;
- intermediate projection, derivation, filtering, joining, aggregation, and deduplication;
- aggregation before and after joins where semantically valid;
- output grain, dimensions, grouping, metrics, and output count; and
- allocation of logic among staging, intermediate, and output layers.

Every raw source MUST reach at least one output in every valid scenario. Scenarios that differ
only in domain, names, literals, or seed do not provide new structural coverage.
`SCENARIO_AUTHORING.md` defines how these dimensions become coverage targets and corpus
completion criteria.

After fault applicability is specified, the applicability audit defined by
`SCENARIO_AUTHORING.md` MUST evaluate the validated corpus and add any missing fault-context
requirements. Those deficits reopen scenario authoring and the independent-audit cycle.

## 11. Corpus-to-supervision boundary

Scenario coverage establishes a pool of valid pipeline contexts. Coverage targets and claims are
constraints on that pool; downstream code MUST derive SFT allocation independently from them.

Trajectory allocation follows this hierarchy:

```text
fault family
  -> fault subtype
  -> injection site or layer
  -> observed symptom class
  -> scenario context
  -> data_seed
```

The fault and training specifications assign quotas in that order. Per-scenario trajectory counts
are a result of applicable fault strata rather than the primary balancing unit. Each stratum MUST
limit correlated repetitions from the same scenario and fault site so that they do not dominate
the training mixture.

The fault catalog MUST classify each fault subtype or applicability stratum by whether changing
`data_seed` can materially change diagnostic observations or decision states. A seed-invariant
case contributes one seed variant to SFT. A seed-sensitive case MAY contribute multiple variants
after each has produced a valid manifestation with materially different observable evidence or a
different justified diagnostic decision. Additional reproducible seeds MAY be reserved for
robustness evaluation.

Train, validation, and test assignment occurs at `scenario_id` granularity before fault and seed
expansion. Every trajectory derived from the same scenario, across all faults and seeds, MUST stay
in one partition.

Training-set size is selected using nested, fault-balanced subsets and a fixed held-out scenario
partition. `TRAINING.md` and `EVALUATION.md` MUST define an approximately geometric learning curve
and a predeclared stopping rule over macro fault-diagnosis and tool-use metrics. Further generation
targets the underperforming strata identified by that evaluation.

## 12. Exit conditions

The scenario-language subsystem is ready for authoring when the Pydantic contract, parsing,
semantic validation, generated JSON Schema, and their tests are complete. A scenario is ready
for inclusion in the corpus immediately after it reaches `ValidatedScenario`. The corpus as a
whole is complete only after the independent audit defined by `SCENARIO_AUTHORING.md` verifies
every scenario, claim, count, requirement, and quota without finding an error.

The pipeline-generation subsystem is ready for downstream use when the compiler and raw-data
generator implement the full validated language, the conformance suite passes, and at least
one representative instance can be reproduced end to end.

A concrete `(scenario, data_seed)` instance is eligible for fault injection only after its
clean control succeeds and its exact baseline is recorded or cached. No downstream component
may use a bare `Scenario`, a partially validated candidate, or a failed clean baseline.
