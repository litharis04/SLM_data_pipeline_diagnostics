# Scenario Corpus Authoring Specification

Status: draft.

## 1. Purpose

This document defines the complete workflow for authoring the scenario corpus. An author is an
LLM agent or a human working directly with the implemented scenario contract. The author writes
`scenario.json` files directly, corrects them against the two existing validation stages, and
maintains an explicit coverage plan until every quota is met.

The public scenario parser and semantic validator are the executable interfaces used during
authoring.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 2. Scope and boundaries

Authoring consumes:

- `SCENARIO_SPEC.md` and `PIPELINE_SPEC.md`;
- the implemented Pydantic contract and semantic validator;
- the generated JSON Schema;
- the valid test fixtures and accepted corpus scenarios as examples; and
- the current scenario corpus and its coverage document.

Authoring produces only:

- validated scenario files under `scenarios/`; and
- `scenarios/COVERAGE.md`.

Authoring ends when a candidate becomes a `ValidatedScenario` and its actual coverage is
recorded. It does not compile a scenario, generate raw data or dbt files, materialize a pipeline,
run dbt, or establish a clean control. Those operations belong to the compiler and concrete
pipeline-instance lifecycles defined by `PIPELINE_SPEC.md` and `GENERATOR_SPEC.md`.

The author MUST conform to the existing public contract. It MUST NOT change a validator, weaken
an invariant, or add an undocumented interpretation merely to admit a candidate. A defect or a
specification disagreement discovered during authoring is project work outside the authoring
run and MUST be resolved before that run continues.

## 3. Corpus artifacts

The corpus has a flat layout:

```text
scenarios/
├── COVERAGE.md
├── <scenario_id>.json
└── ...
```

Every JSON filename MUST be exactly `<scenario_id>.json`, where `scenario_id` is the value in
that file.

`validate_semantics` returns an in-memory `ValidatedScenario`. Its `scenario` field is the
original immutable Pydantic `Scenario` passed to the validator; its resolved schemas, lineage,
grains, keys, assertions, and symbol tables are derived compiler-facing data. The corpus file is
the authored `Scenario` JSON that produced this result, not a serialization of the
`ValidatedScenario` wrapper.

`tests/scenario/fixtures/valid/minimal.json` and
`tests/scenario/fixtures/valid/maximal.json` provide the initial complete examples. Every accepted
`scenarios/<scenario_id>.json` then becomes an additional few-shot example for later candidates.
Before writing a candidate, the author MUST inspect two to four accepted scenarios selected for
relevant operations, requirement claims, or domain structure. If fewer than two corpus scenarios
exist, it MUST inspect all available corpus scenarios and both fixtures. Examples demonstrate
coherent composition but do not define the available syntax. Test fixtures are not corpus
scenarios and MUST NOT contribute to coverage counts.

The author MUST inspect the complete implemented Pydantic model surface while building the
initial coverage plan. Before each candidate, it MUST also inspect the model definitions governing
the selected anchor and companions. Those models are the executable source of truth for syntax;
the generated schema and examples are aids for reading and composition. The root and closed
vocabulary are defined in
`src/data_pipeline_diagnostics/scenario/models.py` and its referenced modules for raw tables,
relationships, staging, intermediate models, outputs, assertions, expressions, generators, and
shared types. `artifacts/scenario.schema.json` provides the complete generated JSON shape.
`src/data_pipeline_diagnostics/scenario/semantic.py` and
`src/data_pipeline_diagnostics/scenario/errors.py` define the global invariants and structured
failures used during correction. Examples MUST NOT be used to infer that an absent variant is
unsupported.

## 4. Coverage document

### 4.1. Required structure

`scenarios/COVERAGE.md` is the durable plan and overview for the corpus. It MUST be created and
fully populated before the first scenario is authored. It MUST contain these two tables:

```markdown
## Requirements

| ID | Predicate | Target | Actual |
| --- | --- | ---: | ---: |
| F-TYPE-001 | At least one raw column has type `string`. | 5 | 0 |
| D-DOMAIN-001 | The scenario's `domain` is `retail`. | 6 | 0 |

## Scenario claims

| File | Requirement IDs |
| --- | --- |
| `example.json` | F-TYPE-001, D-DOMAIN-001, ... |
```

The requirements table is the plan. Each row defines one stable requirement ID, one unambiguous
predicate over a validated scenario, a positive target, and the current actual count. The
scenario-claims table contains every corpus file and the complete set of requirement IDs whose
predicates that scenario actually satisfies.

A predicate MUST be decidable from the scenario file and its `ValidatedScenario`. It MUST state
observable structure or content, not authorial intent. For example, “contains a left join whose
left input is already aggregated” is usable; “is intended to exercise difficult joins” is not.
Predicates for domain families MAY additionally inspect the `domain` and the coherent vocabulary
of model and column names.

IDs SHOULD use these prefixes so that the selection algorithm can identify the requirement
class without adding more table columns:

- `F-` for an individual feature or boundary case;
- `I-` for an important cross-axis interaction;
- `T-` for a DAG or data-flow topology motif; and
- `D-` for a domain family.

The remaining ID components SHOULD identify the axis and a stable ordinal. Requirement row order
is significant because it breaks selection ties. Rows MUST NOT be reordered during an authoring
run.

### 4.2. Targets and counts

The default target is:

- 5 scenarios for every individual feature or boundary requirement;
- 3 scenarios for every important interaction or DAG-motif requirement; and
- 6 scenarios for each of exactly 16 domain-family requirements chosen in advance.

These are minimum quotas. `actual` MAY exceed `target`. A scenario contributes at most one count
to a requirement even when the predicate is satisfied by several objects within that scenario.
Because every scenario belongs to exactly one domain family, the domain targets require at least
96 scenarios in the baseline corpus.

For requirement `r`, its count is derived as:

```text
actual(r) = number of distinct scenario files whose complete claims include r
```

Consequently, `actual` and the claims table are mutable, derived state rather than independent
facts. After each accepted scenario they MUST agree with all corpus JSON files and all predicates.
A scenario MUST be credited for every predicate it satisfies, including requirements that were
not selected when the scenario was planned. Planned requirements that the final validated JSON
does not satisfy MUST NOT be claimed.

### 4.3. Freezing and amending the plan

Before generation begins, the author MUST expand the mandatory inventory in section 5 into
concrete predicates, select the 16 domain families, assign the standard targets, and establish
initial counts. If scenarios already exist, every one of them MUST first reach
`ValidatedScenario`, then its complete claims and all `actual` values MUST be recomputed.

Requirement definitions and targets are frozen for the duration of an authoring run. The author
MUST NOT lower a target to make the corpus finish. A requirement proven impossible under the
implemented contract stops the run; the coverage plan must be corrected explicitly before a new
run begins. This is a correction of a defective predicate, not permission to remove an awkward
but feasible case or reduce its quota.

An independent audit MAY append a requirement omitted from the mandatory inventory, using the
applicable standard target. After the fault catalog defines scenario-level applicability, its
applicability audit MUST append any predicates and targets needed to represent each required
combination of fault subtype, injection-site or layer class, and materially distinct scenario
context. These rows use stable `I-FAULT-` IDs and count distinct validated scenarios in which the
stated fault context is applicable. Their targets are owned by the fault specification.

Appended rows go after existing rows. Their addition creates new deficits and restarts the same
authoring cycle; it does not invalidate scenarios that remain valid.

## 5. Mandatory coverage inventory

The coverage plan MUST describe both individual features and the interactions that materially
change behavior. `SCENARIO_SPEC.md` and the implemented contract remain the source of truth for
the current closed vocabularies. If they evolve, the plan MUST be expanded accordingly rather
than relying on the examples below as a frozen substitute for the contract.

### 5.1. Individual feature axes

The plan MUST expand all of the following axes into concrete predicates. Individual feature and
boundary rows use `F-`; the 16 primary domain-family rows use `D-`:

| Axis | Required distinctions |
| --- | --- |
| Scenario size | Minimum and maximum raw-table, intermediate-model, and output-model counts; exact and ranged raw row counts; materially different table widths. |
| Scalar types | `string`, `integer`, `float`, `boolean`, `date`, and `timestamp` in semantically useful roles. |
| Raw generators | Every generator variant in the contract, its material option forms, and meaningful capacity or range boundaries. |
| Column properties and keys | Nullable and non-nullable columns, null probabilities, unique columns, non-key columns, single and composite primary keys, foreign keys, and columns participating in more than one legitimate role. |
| Relationships | Every cardinality, foreign-key orientation for one-to-one relationships, single and composite key arity, direct relationships, and many-to-many bridge relationships. |
| Staging columns | Passthrough, rename, omission, every column-operation variant, multi-operation chains, and every supported type transition. |
| Staging rows | Filtering and deduplication, including meaningful orderings when both occur. |
| Intermediate models | Transform, join, aggregate, and deduplicate operations; meaningful operation ordering and placement across the intermediate layer. |
| Joins and grain | Inner and left joins, relationship orientations, join placement, projected columns from both sides, single and composite grains, and grain preservation or expansion. |
| Expressions and conditions | Every expression and condition variant, representative operand types, nesting, and placement in derived columns, filters, and conditional metrics where supported. |
| Outputs | One and two outputs, passthrough and grouped outputs, dimensions, renames, single and composite grains, and shared upstream reuse. |
| Metrics | Every metric function and its material argument form at every model layer where the contract permits it. |
| Assertions | Every healthy-assertion type, representative eligible model layers, scalar types, key arities, and derived versus explicit assertion contexts. |
| Domain and vocabulary | Sixteen preselected domain families, varied but coherent table/model/column vocabulary, and descriptions that match the executable scenario. |

“Every variant” means every discriminator or enum member exposed by the current public contract.
It does not require arbitrary sampling of equivalent literal values. Numeric and collection
boundaries deserve separate predicates only where they affect validation, generator capacity,
data behavior, grain, or later fault applicability.

The 16 domain families MUST be selected before generation, be recognizably different problem
settings, and support plausible multi-table analytical pipelines. A change of domain, identifier,
description, literal, or future `data_seed` alone does not provide structural coverage.
The `D-` predicates MUST define non-overlapping primary domain families. Every authored scenario
MUST belong to exactly one of them and claim its corresponding `D-` requirement, including after
all domain-family targets have already been met.

### 5.2. DAG and topology motifs

The plan MUST expand the following into concrete `T-` predicates where the contract permits
them:

- a linear intermediate chain;
- fan-in from two and from three upstream branches;
- branching from a shared upstream model;
- upstream reuse by multiple intermediate or output models;
- joins early and late in the intermediate DAG;
- aggregation before a join and aggregation after a join;
- deduplication before another transformation and after a fan-in operation;
- multiple outputs with shared ancestry and with meaningfully different grains; and
- full participation of every raw source in at least one output, through structurally different
  paths.

Equivalent graphs renamed into another domain are not distinct topology motifs. The concrete
topology predicates in `COVERAGE.md` are the coverage contract.

### 5.3. Important interaction families

The plan MUST add concrete `I-` predicates for combinations within these families:

- scalar type × generator × nullable/unique/key role, including capacity-sensitive boundaries;
- relationship cardinality × foreign-key orientation × key arity, including bridge endpoints;
- staging operation or chain × input/output type × null behavior;
- staging filter/deduplicate order × declared grain or key preservation;
- intermediate operation order × DAG placement × resulting lineage and grain;
- join type × cardinality × projected sides × declared grain;
- join placement × aggregation placement, both before and after the join where semantically valid;
- shared upstream or branching topology × downstream operation and output grain;
- expression or condition kind × operand type × placement;
- output grouping/dimensions/metrics × upstream grain and model layer; and
- assertion kind × eligible layer × scalar/key/metric structure.

The plan MUST NOT enumerate the full Cartesian product. A combination belongs in the plan only
when the interaction changes at least one of:

- compilation semantics;
- lineage or grain reasoning;
- generated data behavior; or
- applicability of a plausible future fault.

Each selected interaction predicate must name the combination precisely enough that the author
and an independent auditor reach the same result. Superficial combinations whose components do
not affect one another remain covered by their individual feature rows.

## 6. Requirement selection

For every row, the author computes:

```text
deficit = max(target - actual, 0)
relative_deficit = deficit / target
```

Every scenario receives one mandatory domain context in addition to its structural coverage
bundle. The next scenario is planned as follows:

1. If any quota is unmet, work in deficit mode. Otherwise, if the prompt-requested corpus size has
   not been reached, work in balancing mode. If neither condition holds, authoring is complete.
2. In deficit mode, choose the unmet `I-` or `T-` row with the greatest `relative_deficit` as the
   structural anchor. If none remains, choose the unmet `F-` row by the same rule. Break equal
   values by row order. If only domain deficits remain, the domain selected in step 3 is the
   anchor. In balancing mode, choose the `I-`, `T-`, or `F-` row with the lowest `actual / target`,
   breaking ties by row order.
3. Choose exactly one compatible `D-` row. Prefer an unmet domain row with the greatest
   `relative_deficit`, breaking ties by document order. If every domain quota is met, choose a
   compatible domain with the lowest `actual`, again breaking ties by document order.
4. In deficit mode, add two compatible unmet non-domain requirements from axes other than the
   structural anchor's axis, choosing the greatest `relative_deficit` and then document order. In
   balancing mode, choose companions from other axes by the lowest `actual / target` and then
   document order. If fewer than two compatible rows are available, use those available. The
   mandatory domain does not consume a companion slot.
5. Check the proposed bundle against the contract and against subject-matter coherence before
   writing JSON.

Requirements are compatible when one valid, natural scenario can satisfy all of their
predicates. Mutually exclusive values may coexist only if the scenario can contain separate
eligible objects that satisfy them without an artificial construction. If a companion proves
incompatible while writing or validating the candidate, the author MUST preserve the anchor and
domain context and replace that companion using the same ranking rule. If the chosen domain is
incompatible, the author MUST preserve the structural anchor and choose another domain by step 3.
An impossible anchor triggers the plan correction rule in section 4.3.

The domain, anchor, and companions guide construction; they are not reserved claims. The author
SHOULD prefer the smallest coherent design that expresses the chosen interactions clearly. It
MUST NOT add implausible tables, columns, joins, operations, or outputs merely to collect
unrelated coverage in one scenario.

## 7. Direct authoring and correction loop

The invocation prompt MUST state the desired total number of corpus scenarios. This specification
and the reusable authoring task define no fixed total. The requested total counts only accepted
`scenarios/*.json` files, not fixtures, and MUST be large enough to close every coverage quota. If
the existing corpus already exceeds it, or if the total is reached while deficits remain, the
author MUST report the conflict rather than delete scenarios, lower targets, or count an invalid
candidate.

For each scenario, the author MUST complete this loop before counting another candidate:

1. Select the mandatory domain, a structural anchor when one remains, and up to two companions by
   section 6.
2. Design one connected, internally coherent domain pipeline and write the complete JSON file
   directly.
3. Parse the file with strict Pydantic validation. Use the returned structured errors to correct
   the same candidate until parsing succeeds, or deliberately abandon the candidate without
   credit.
4. Pass the parsed `Scenario` to semantic validation. Use its structured issues to correct the
   same candidate until `ValidatedScenario` is returned, returning to Pydantic validation after
   every JSON edit.
5. Evaluate every predicate in `COVERAGE.md` against the final file and
   `ValidatedScenario`—not only the anchor and companions.
6. Add or replace the file's row in the scenario-claims table with the full true requirement set,
   then recompute every `actual` value.
7. Recompute deficits and the accepted corpus size. Continue according to section 6 until the
   prompt-requested total is reached and every quota is closed.

The public validation boundary can be exercised directly:

```python
from data_pipeline_diagnostics.scenario import parse_scenario_file, validate_semantics

scenario = parse_scenario_file("scenarios/example.json")
validated = validate_semantics(scenario)
```

The existing Pydantic errors and semantic issues are the correction interface. Validation is
immediate and per scenario.

An invalid or abandoned candidate contributes no claims. If an already counted file is edited,
all its prior claims MUST first be treated as stale; the edited file must pass both validators and
have its complete requirement set recomputed before it counts again.

The authoring run ends only when the prompt-requested corpus size has been reached and every
requirements row independently has `actual >= target`.

## 8. Independent audit

The completed authoring run MUST be checked in one fresh session that did not author the corpus.
The audit prompt MUST state the expected total number of corpus scenarios; the reusable audit task
defines no fixed total. At the start of the session, the auditor freezes the complete
lexicographically sorted `scenarios/*.json` list and treats every requirement definition, target,
`actual` value, and scenario claim in `COVERAGE.md` as untrusted.

The auditor MUST:

1. compare the requirements plan with section 5, the current scenario contract, and any active
   fault-applicability requirements, checking that all mandatory individual axes, interaction
   families, DAG motifs, and exactly 16 domain-family rows were expanded into unambiguous
   predicates with their applicable targets;
2. enumerate every `scenarios/*.json` file, verify that their count equals the prompt value, and
   verify that every filename equals its `scenario_id`;
3. rerun strict Pydantic validation and semantic validation for every file;
4. independently evaluate every requirement predicate for every `ValidatedScenario`, including
   verifying that every scenario belongs to exactly one selected `D-` family;
5. replace each file's claims with the complete recomputed set and recompute all `actual` values;
6. append any omitted mandatory requirement with its standard or fault-specified target and
   calculate its existing actual coverage; and
7. report invalid files, filename mismatches, ambiguous or defective predicates, incorrect
   claims, corrected counts, and all remaining deficits.

The auditor MAY edit only the coverage document's derived claims and counts and append omitted
requirements. It MUST NOT edit scenario JSON, lower a target, silently rewrite an existing
predicate, or author additional scenarios. A JSON file that does not reach `ValidatedScenario`
receives no coverage credit and is returned as an authoring defect.

The auditor MAY finish corrections to derived claims and counts within the same session. If it
finds an invalid JSON file, a filename mismatch, a defective existing predicate that requires
redesign, or any remaining deficit, the corpus returns to authoring; the changed corpus then
requires another independent audit. A self-review in the authoring session does not replace this
audit.

The corpus is complete only when the independent audit leaves:

- no invalid scenario or filename mismatch;
- no missing or defective coverage requirement;
- no scenario without exactly one domain-family claim;
- no missing, extra, or false scenario claim;
- exact `actual` values derived from those claims; and
- no requirement below its target; and
- exactly the number of scenarios stated in the audit prompt.

## 9. Fault-applicability extension

Once fault applicability is specified, the applicability audit evaluates every
`ValidatedScenario` and identifies all eligible injection sites without modifying scenario JSON.
For each underrepresented fault subtype and context, it appends a precise scenario predicate to
`COVERAGE.md` with the target defined by the fault specification.

The next authoring session treats those deficits like the existing interaction requirements,
authors additional scenarios, validates and records them through sections 6–7, and then submits
the expanded corpus to the independent audit in section 8. This cycle repeats whenever the fault
catalog or its applicability rules add an uncovered context.

Coverage targets ensure that suitable scenario contexts exist. Fault assignments, concrete
pipeline instances, `data_seed` variants, oracle trajectories, and SFT sampling weights belong to
their downstream subsystems and are not scenario coverage claims.
