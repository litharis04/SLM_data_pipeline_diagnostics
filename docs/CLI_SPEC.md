# Pipeline Generator CLI Specification

Status: draft, version 1. Initial defaults are explicit below and may be revised after the
large-instance and live-authoring checks. CLI implementation is not yet complete.

## 1. Purpose and authority

The CLI lets a user obtain and explore a reproducible local analytical pipeline without
writing the scenario language by hand. It manages a personal scenario catalog, authors new
scenarios through OpenRouter or the Gemini Developer API, prepares existing scenarios offline,
and rebuilds them with another data seed.

The result is an authored scenario plus generated Parquet, DuckDB, a runnable dbt project,
blocking tests, and an instance record. Successful authoring means that a concrete instance
has been prepared and verified, not merely that an LLM returned valid JSON.

This specification defines the CLI boundary. [SCENARIO_SPEC.md](SCENARIO_SPEC.md) remains the
authority for the scenario language; [GENERATOR_SPEC.md](GENERATOR_SPEC.md) and
[PIPELINE_SPEC.md](PIPELINE_SPEC.md) govern compilation, clean control, identity, and caching.
[SCENARIO_AUTHORING.md](SCENARIO_AUTHORING.md) governs maintenance of the example corpus.
Interactive authoring does not maintain corpus quotas or edit its coverage document.

The key words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

Version 1 provides a terminal interface. A GUI, interactive SQL shell, arbitrary SQL/Python
authoring, fine-grained DAG configuration, and a general request-feasibility solver are outside
this version. The existing language bounds remain: 3–4 raw tables, one staging model per raw
table, 2–3 intermediate models, and 1–2 output models. The user does not select model counts.

## 2. Commands and workspace

### 2.1. Public command surface

The public console entry point is `plgen`; the installed package MUST expose this executable
when the CLI is implemented. These six commands are required:

```text
plgen [--workspace PATH] connect --provider PROVIDER --model MODEL
      [--key-env NAME] [--max-output-tokens N]
plgen [--workspace PATH] list
plgen [--workspace PATH] open SCENARIO_ID
plgen [--workspace PATH] seed SCENARIO_ID DATA_SEED
plgen [--workspace PATH] create --domain DOMAIN [--id SCENARIO_ID]
      [--size small|medium|large]
      [--composite-keys auto|required|forbidden]
      [--staging OPERATIONS] [--intermediate FEATURES]
      [--joins auto|inner|left|mixed] [--metrics FUNCTIONS]
      [--seed DATA_SEED]
plgen [--workspace PATH] delete SCENARIO_ID
```

`--help` is available at the root and for every command. `--version` is available at the root.
The default workspace is `./pipeline_workspace`, resolved against the invocation directory;
the user can select a persistent location with the global `--workspace` option. Printed paths
MUST be absolute. Commands MUST NOT require the current directory to be the repository checkout.

`DATA_SEED` follows the existing strict integer contract, `0 <= DATA_SEED <= 2**63 - 1`.
Scenario identifiers follow the existing `ScenarioId` contract and are never arbitrary paths.

### 2.2. Personal catalog and initialization

The installed package MUST include the accepted example scenarios and the authoring resources
needed at runtime. On the first catalog command, the CLI initializes the workspace by copying
the bundled scenarios into the personal catalog, with seed `0` and no prepared instances.
Initialization MUST complete atomically; an interrupted initialization must be recoverable.
Help and version commands do not initialize a workspace or contact a provider.

The bootstrap is performed once per workspace. Subsequent commands and package upgrades MUST
NOT reimport deleted examples or overwrite personal scenario files. The package resources and
repository `scenarios/` corpus are never modified by CLI operations. New scenarios are added
only to the personal catalog; each identifier is unique within that catalog.

The workspace has these responsibility boundaries:

```text
pipeline_workspace/
  config.json                    # non-secret provider profiles and active provider
  catalog.json                   # bootstrap package version and registered scenario identifiers
  scenarios/<scenario_id>/
    scenario.json                # scenario-language document only
    entry.json                   # minimal (imported) or full (authored) CLI metadata
  cache/                         # existing v1 baselines and isolated work/ copies
  authoring/<run_id>/             # attempts, check results, and failure/provenance records
```

CLI metadata MUST be kept outside the closed scenario JSON and core instance-record contracts.

`config.json` holds non-secret provider profiles (provider, model, key-env name,
max-output-tokens budget) and the active provider. It MUST NOT contain API keys.

`catalog.json` holds the bootstrap package version and the registered scenario identifiers.
The bootstrap version is the package `major.minor` at workspace-initialization time; patch
versions do not change the bootstrap contract. A version mismatch after a package upgrade is
ignored on a best-effort basis: the workspace is used as-is. Metadata-schema migration is
out of scope for version 1.

Every personal scenario has an `entry.json`. Imported examples carry a minimal entry with
`origin: "imported"`, the current seed, the saved scenario content hash, and the current
instance path/digest. Authored entries additionally retain the
normalized user requirements, provider/model provenance, and accepted structural/raw-size
check results. The authoring log records attempt outcomes and provider-returned model identity
when available. It does not contain credentials or transport authentication headers.

Catalog publication and changes to the current-instance pointer MUST be atomic for a single
command (write temporary file plus atomic rename). Failed or
interrupted authoring, preparation, and seed changes MUST leave an existing scenario and its
current instance usable. Concurrent mutating commands against one workspace are unsupported
in version 1; there is no inter-process lock. An interrupted command may leave a temporary
file behind but MUST NOT leave a half-written catalog or entry.

## 3. API connection

### 3.1. Supported providers

Version 1 MUST implement both providers behind one authoring interface:

| Provider identifier | API | Credential source by default |
| --- | --- | --- |
| `openrouter` | OpenAI-compatible Chat Completions at `https://openrouter.ai/api/v1/chat/completions` | `OPENROUTER_API_KEY` |
| `gemini` | Native Gemini Developer API `models.generateContent` at `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` | `GEMINI_API_KEY` |

OpenRouter uses Bearer authentication. Gemini uses the `x-goog-api-key` header, so the key is
not placed in a URL. Each provider has one thin adapter behind the common authoring interface.
An adapter translates the common authoring instructions, previous candidate,
and validation feedback to its provider's message format and normalizes the provider reply
into a common internal result equivalent to:

```python
@dataclass(frozen=True)
class NormalizedGeneration:
    text: str  # raw candidate text returned by the provider
    finish: Literal["stop", "length", "refusal", "error"]
    usage: Usage | None  # input/output/reasoning token counts when reported
    model_identity: str | None  # actual model reported by the provider, when available
```

OpenRouter maps `choices[0].message.content` plus its `finish_reason` onto this result;
Gemini maps `candidates[0].content` text parts plus `finishReason` and `usageMetadata` onto
the same result. Adapters do not implement separate
scenario-validation or materialization logic. The supported endpoints are fixed in version 1.

These API contracts are documented in the [OpenRouter quickstart](https://openrouter.ai/docs/quickstart),
[Gemini generateContent reference](https://ai.google.dev/api/generate-content), and
[Gemini key documentation](https://ai.google.dev/gemini-api/docs/api-key).

`--model` is required rather than inferred from the key. OpenRouter supports the explicit
`openrouter/free` router and individual model identifiers, including free variants. The router
can choose different models between requests; this is provider behavior and must be reflected
in provenance when reported. [OpenRouter Free Models Router](https://openrouter.ai/openrouter/free/apps).
For Gemini, the user chooses a currently available text-generation model with free-tier access
for their project. Model identifiers are configuration, not hard-coded validator vocabulary.

Both providers offer free access with conditions and changing quotas. The CLI MUST NOT promise
unlimited or universally free inference, infer billing status from a key, silently substitute a
paid model, enable billing, or switch providers on failure. Gemini billing depends on the
project; free-tier access and limits depend on the model and account. Current information is
available in [OpenRouter limits](https://openrouter.ai/docs/api_reference/limits),
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing),
[Gemini billing](https://ai.google.dev/gemini-api/docs/billing/), and
[Gemini limits](https://ai.google.dev/gemini-api/docs/rate-limits).

### 3.2. Credential handling and connection behavior

The user obtains a key from the provider's dashboard. `connect` configures one profile per
provider, including the model and output-token budget, and makes the successfully configured
profile active. The two profiles may coexist; rerunning `connect` selects or updates a provider
without deleting the other profile.

Without credential options, the profile references its default environment variable.
`--key-env NAME` references another environment variable. Neither mode copies the key into a
configuration file. There is no `--store-key` mode, OS credential store, hidden terminal
input, or plaintext-file fallback in version 1.

Environment credentials are explicit per profile; there is no implicit
cross-provider key lookup. Empty or missing keys fail before a request. API keys MUST NOT be
accepted as command-line argument values or written into scenario files, logs, prompts, dbt
subprocess environments, or generated artifacts. Provider exceptions MUST be sanitized before
printing or persistence. The materializer receives no credential-bearing configuration; its
subprocess environment MUST exclude configured credential environment variables.

`connect` checks credentials and the selected model through non-generation provider requests:
OpenRouter key metadata plus model catalog, or Gemini model metadata. It persists the new active
profile only after these checks succeed. The output names the provider, model, and credential
source without showing the key. This verifies configuration and model discovery, not guaranteed
inference capacity, remaining quota, or free billing. It spends no generation request as a probe.

### 3.3. Transport and output limits

Requests are non-streaming text generation with one candidate, no tool execution, no web
grounding, and no provider plugins. The default output budget is 32,768 tokens per authoring
attempt. `connect --max-output-tokens N` sets a positive integer budget stored in that provider
profile; omission uses the default. The request timeout is 180 seconds. The finite authoring
budget is defined in section 6.

The output budget does not limit the size of the input prompt. Model input/output limits and
any combined context-window limit apply separately. Where metadata exposes these limits, the
adapter checks known incompatibilities; otherwise provider capacity errors are reported clearly.
The CLI MUST NOT silently truncate reference materials, lower the configured budget, or change
models to fit a request. For reasoning models, generation limits may cover both reasoning and
visible output; record provider-returned usage when available. See the
[OpenRouter parameter reference](https://openrouter.ai/docs/api_reference/parameters) and
[Gemini model limits](https://ai.google.dev/api/models).

The portable baseline asks for one complete JSON object. Gemini requests JSON MIME output;
OpenRouter's baseline does not require strict schema-output support from every selected model.
The full Pydantic-derived JSON Schema is local authority and prompt context. It MUST NOT be sent
unchanged as a strict provider schema without a tested compatibility transformation; schema
support varies and large recursive schemas may be rejected. Any transformed schema remains
derived from the implemented contract. [Gemini structured-output limitations](https://ai.google.dev/gemini-api/docs/structured-output).

Authentication failures, quota/rate limits, unavailable models, network failures, and timeouts
abort the current authoring run with a provider error. SDK/client automatic retries MUST be
disabled in version 1; a `429` must not consume the remaining authoring budget in a retry loop.
Show a sanitized retry hint when supplied. The user can rerun the command or explicitly choose
another profile/model with `connect`.

## 4. Authoring requirements

### 4.1. User controls

Comma-separated selections are normalized to unique items and checked before contacting the
provider. A nonempty selection means every selected feature must occur at least once. Other
supported features may also occur unless an option explicitly forbids them. Omission delegates
the choice to the author; it does not require the feature's absence.

| Option | Allowed values | Default and meaning |
| --- | --- | --- |
| `--domain` | Any value satisfying the existing `DomainName` identifier rules; no fixed domain list | Required; `Scenario.domain` must equal this value. The author supplies coherent entities and business meaning. |
| `--id` | Existing `ScenarioId` | Optional; otherwise the CLI allocates a unique `<domain>_<suffix>` identifier before the first request. It remains fixed throughout retries. |
| `--size` | `small`, `medium`, `large` | `small`; realized raw-layer size as defined in section 4.2. |
| `--composite-keys` | `auto`, `required`, `forbidden` | `auto`; presence or absence of composite raw primary keys. |
| `--staging` | `trim`, `lower`, `upper`, `replace`, `map_values`, `null_if`, `coalesce`, `cast`, `filter`, `deduplicate` | Omitted; every selected operation must be present in staging. |
| `--intermediate` | `filter`, `derive`, `deduplicate` | Omitted; filtering, structured derived columns, or a business deduplication model. |
| `--joins` | `auto`, `inner`, `left`, `mixed` | `auto`; `inner`/`left` constrain all JOINs to that type; `mixed` requires at least one of each. |
| `--metrics` | `count_rows`, `count`, `count_distinct`, `sum`, `avg`, `min`, `max`, `conditional_count`, `conditional_sum` | Omitted; every selected function must occur in an output model. |
| `--seed` | Existing data-seed range | `0`; held fixed through authoring and repair. |

`required` composite keys mean at least one raw table declares a primary key with more than
one member. `forbidden` means no raw table declares such a primary key. `auto` leaves this
choice to the author. Composite analytical grains and groupings are not raw primary keys and
remain permitted. The CLI does not additionally require a composite PK to be used by a
relationship or JOIN; relationship/key/JOIN validity remains the existing validator's concern.
Authors SHOULD avoid nominal composite keys whose uniqueness depends entirely on one
independently unique component.

`derive` requests a nontrivial arithmetic or date-part derived expression in the intermediate
layer. Filters and derived columns may be hosted by JOIN models; an additional transform model
is not required. Intermediate aggregation remains available to the author but is not a separate
user control in version 1. Output aggregations remain mandatory under the scenario contract.

The author chooses source columns, thresholds, category sets, JOIN orientation, grouping keys,
and metric names. The user does not specify exact column expressions, individual row counts,
relationship cardinalities, or DAG edges. Domain equality is mechanically checked; the quality
of business semantics is evaluated through examples and live-authoring review, not claimed as
a formally proved property.

### 4.2. Size presets

Let `M_raw` be the maximum realized row count over the raw tables of the prepared instance.
Presets constrain only this raw-layer maximum. Initial inclusive bounds are:

| Preset | Required `M_raw` | Intended scale |
| --- | ---: | --- |
| `small` | 1–1,000 | Typically several hundred rows in the largest raw table. |
| `medium` | 1,001–10,000 | Several thousand rows in the largest raw table. |
| `large` | 10,001–100,000 | Tens of thousands of rows in the largest raw table. |

The author sets heterogeneous raw row-count intervals, giving reference tables and event tables
appropriate relative sizes. It also sizes ID domains, unique pools, and dependent-key capacities
accordingly. CLI preflight rejects a raw `rows.max` above the requested preset's upper bound
before generating data. Authors SHOULD choose at least one raw interval whose minimum meets
the preset's lower bound to keep later seed variants within the preset. The final check uses
the raw `row_count` values already recorded in `instance_record.json`; it does not issue new
database queries. Raw intervals alone do not establish the realized size of a concrete instance.

Staging, intermediate, and output row counts do not participate in preset acceptance. JOIN
expansion may exceed the preset ceiling and is not a size-requirement failure. Existing blocking
tests still apply to downstream results. Tables are never truncated or downsampled to pass.

Size applies to a concrete instance. An authored scenario retains the requested preset for
later seed changes; every newly prepared instance must meet it. Imported corpus scenarios have
no inherited CLI requirements and receive an observed raw-size classification only. A legacy
instance outside the raw preset bounds is displayed as outside presets rather than silently
rejected.

## 5. Verification and request checks

### 5.1. Separate checks

Acceptance of a CLI-authored result requires, in order:

1. Strict parsing and the existing semantic validator, producing `ValidatedScenario`.
2. Structural request checks, including fixed ID/domain, selected features, key/JOIN policy,
   metric presence, and raw size preflight.
3. Preparation through the existing clean-instance/cache boundary and all blocking dbt tests.
4. Realized raw-size check using the prepared instance record.

Neither user requirements nor CLI check-result fields are added to `Scenario`. Invalid candidates
are never compiled. Existing derived and explicit assertions remain blocking. The author is
not required to emit a large explicit assertion set; `tests: []` is allowed when structural
tests cover the relevant guarantees. Assertions check result invariants; structural request
checks establish the presence and configuration of requested features. Version 1 does not additionally
prove that every selected operation changes the realized values or row counts.

### 5.2. Shared structural feature extraction

One small CLI module extracts features from `ValidatedScenario` and checks the normalized
request. It operates in memory, using the existing typed models, without SQL execution or a
separate configurable rule language. A shape equivalent to the following is required:

```python
extract_features(validated: ValidatedScenario) -> ScenarioFeatures
check_requirements(features: ScenarioFeatures, request: AuthoringRequest) -> tuple[RequirementIssue, ...]
```

`ScenarioFeatures` is an immutable record containing the scenario ID/domain, a composite-raw-PK
boolean, sets of staging operations, intermediate features, JOIN types and output metric
functions, and the maximum raw `rows.max`. Standard dataclasses and immutable sets are
sufficient. The same extractor and feature definitions MUST serve both request checking and
example selection; do not maintain separate keyword searches or coverage-claim interpreters.

| Requirement | Structural predicate |
| --- | --- |
| Fixed ID and domain | Exact equality with the allocated identifier and requested domain. |
| Staging selections | Requested names are a subset of operations found in staging columns and staging `row_operations`. |
| Intermediate `filter` | At least one intermediate model has nonempty `filters`. |
| Intermediate `derive` | At least one intermediate `derived_columns` expression contains a `binary` or `date_part` node, including nested occurrences. |
| Intermediate `deduplicate` | At least one intermediate model has `operation == "deduplicate"`. |
| JOIN mode | `inner` requires exactly the type set `{"inner"}`, `left` requires `{"left"}`, and `mixed` requires `{"inner", "left"}`; `auto` adds no condition. |
| Output metrics | Requested functions are a subset of functions declared in output-model metrics. Intermediate aggregate metrics do not count. |
| Composite-key mode | `required` requires `any(len(table.primary_key) > 1 for table in scenario.raw_tables)`; `forbidden` requires its negation; `auto` adds no condition. |
| Raw size preflight | Maximum declared raw `rows.max` is at most the preset's upper bound. |

Feature extraction respects layer boundaries. A staging filter cannot satisfy intermediate
`filter`, and a composite analytical grain cannot satisfy the raw-PK requirement. An expression
containing only `column`, `literal`, and `coalesce` nodes cannot satisfy `derive`. Expression
traversal detects node kinds only; it does not evaluate expressions or prove that values change.

Missing selected features and policy mismatches produce deterministic structured
`RequirementIssue` records with a stable CLI-owned code, JSON-style path, and actionable message.
Collect independent violations in one pass. These are request violations, not new core semantic
errors. They are suitable for repair feedback; no field is added to the core scenario contract.

### 5.3. Deferred operation-effect checks

Proving that selected operations have an observable effect on a concrete seed is outside the
version 1 MVP. No additional evidence queries, guaranteed operation-effect data profiles, or
effect-based acceptance/reseed/retry gates are required. An operation may be present and valid
while leaving values or row counts unchanged. LEFT JOIN does not promise unmatched rows, and
metric functions need not produce distinct results on the sample.

Authors SHOULD choose meaningful examples where the data makes requested operations visible.
Existing generators can express some such inputs, for example padded categorical values or
`template_string` literals for `trim`; these are recommendations, not an additional acceptance
contract. Coordinated data profiles and step-level effect evidence may be added later while
preserving seed determinism, raw-key integrity, and cache identity. Existing validators and
blocking dbt tests remain unchanged.

## 6. Bounded authoring lifecycle

`create` resolves a unique identifier, normalized immutable requirements, active provider,
credential reference, model, and seed before its first generation request. With `--id`, a
collision fails locally; without it the identifier is allocated locally, not guessed by the LLM.

### 6.1. Prompt resources

The prompt contains four clearly separated blocks:

1. Concise instructions to author one complete scenario, obey the fixed requirements, return
   JSON only, and choose coherent entities and meaningful data.
2. The full `SCENARIO_SPEC.md` text matching the installed implementation and the complete JSON
   Schema generated from its current Pydantic models, serialized as compact JSON without
   dropping definitions or constraints.
3. Two complete accepted bundled scenarios, serialized as compact JSON, with deterministic
   annotations derived from the section 5.2 feature extractor: which requested feature tags
   each example covers and which requested features are absent from the pair.
4. The normalized request: fixed ID/domain, selected features and policies, raw-size bounds,
   and the data seed supplied separately to the materializer.

The actual contents are included in the request; local paths are not references that an API
model can follow. The full specification and bundled examples MUST be installed runtime
resources. The schema is derived from the installed models. Authoring does not require sending
the Python sources, `SCENARIO_AUTHORING.md`, `PIPELINE_SPEC.md`, task files, or `COVERAGE.md`.
It cannot depend on reading the checkout or satisfying corpus quotas. The author chooses a
feasible topology within the existing model-count bounds; data-effect advice is non-blocking.

### 6.2. Deterministic example selection

Extract section 5.2 features from the accepted bundled corpus, independently of personal
catalog edits or deletions. Feature-extraction results SHOULD be cached per bundled scenario
(keyed by scenario content hash); the bundled corpus is fixed at install time. Select two
distinct scenarios by comparing every unordered pair.
Use this ordered ranking, resolving each tie with the next criterion:

1. Maximize the number of requested feature tags covered by the pair's union: selected staging
   and intermediate features, output metric functions, explicitly requested JOIN types, and
   presence/absence of a composite raw PK when requested.
2. Maximize the count of individually satisfied explicit composite-key and JOIN-mode predicates
   across the two examples; `auto` contributes no predicate.
3. Minimize combined character length of the compact JSON actually included in the prompt.
   Length is measured on the whitespace-free serialization sent to the provider; repository
   pretty-printed files are compacted before measuring and including.
4. Choose the lexicographically smallest sorted pair of scenario identifiers.

For ranking, `inner` requests the `inner` type tag, `left` the `left` tag, and `mixed` both;
the stricter whole-example JOIN-mode check belongs to the second criterion. A requested
composite mode contributes one coverage tag when either example satisfies that mode.
Example identifiers, domains, and size presets are not matching requirements; other domains and smaller
instances can provide useful structure. Domain equality is mechanically checked for the authored
scenario but plays no role in example ranking. Missing requested features in the best pair are
reported to the author, not dropped from the request or declared infeasible. Selection uses
the typed feature extractor and shared predicates, not text search or an LLM reading coverage
claims. It makes no provider request and does not materialize examples.

### 6.3. Attempts and publication

There are at most **five generation requests**, including the initial request and up to four
repairs. Each request repeats the fixed reference material, chosen examples, and requirements.
A repair additionally includes only the latest candidate and current structured errors, rather
than accumulating all earlier candidates and error lists. An attempt returns a complete
replacement scenario, not a patch. The response is parsed as one JSON
document, optionally enclosed by one outer JSON Markdown fence. Extra prose, concatenated
objects, duplicate keys, and speculative extraction of a valid-looking substring are rejected.
The existing parser size/depth guards remain in force.

Invalid JSON, semantic issues, missing requirements, an empty/truncated candidate, or a raw-size
failure can be returned as structured feedback for another attempt. Feedback includes the
relevant code/path/message, requested constraint, and recorded raw counts when applicable.
Each repair keeps the original requirements, ID, and seed. It must not relax assertions, omit
features, switch seeds, or edit an already validated immutable scenario object in place.

A data-dependent model/test failure such as a bad authored cast, unmapped category, or empty
output can be returned to the author with its preserved failure record. It aborts that concrete
instance; a repaired scenario is revalidated and receives a new content identity. Cache integrity,
filesystem, runtime/tooling failures, unexplained generator defects, and provider errors abort
the run instead of asking the LLM to compensate for infrastructure problems. A provider refusal
is reported distinctly and is not treated as proof that the requested pipeline is impossible.

The core cache establishes clean-control success. A clean baseline may already be cached when
a later CLI requirement check fails; the CLI MUST NOT invalidate or edit that baseline. It
retains failed-attempt references/check results under the authoring run and does not register
them as successful personal scenarios. Neither a failed request nor exhausted attempts proves
general infeasibility. Version 1 rejects unsupported options locally and does not use an LLM feasibility
verdict as an authoritative refusal.

Only after every check succeeds does `create` publish the personal scenario, request/check-result
metadata, and current working-copy reference. Success output contains the identifier, seed,
observed raw size, and absolute paths to the catalog scenario JSON and prepared instance directory.
Failure output contains the final actionable cause, attempt count, and preserved diagnostic
path; a new failed scenario does not appear as a successful catalog entry.

## 7. Listing and opening

`list` shows identifiers in deterministic order, domain, short description, current seed, and
whether an instance has been prepared. It may show observed raw size for prepared entries.
Before preparation, planned raw counts must not be presented as realized raw size. Listing does
not call an LLM or materialize pipelines; only initial catalog bootstrap may write files.

`open` prepares a scenario using its current seed and the existing materializer,
then saves its current-instance reference. The working copy is disposable: `open` always
provides a fresh working copy from the verified cache baseline or from a fresh build, replacing
any existing working copy. When a working copy is replaced, the output says so. There is no
modified-copy detection and no reuse of an edited working copy.

Opening prints a terminal summary with:

- identifier, domain, description, current seed, and instance state;
- model counts per layer and a table of raw/model names, layers, and actual row counts;
- the largest raw table and observed raw-size category;
- the outcome of the fresh dbt build and blocking tests;
- up to five rows of each output, ordered by its declared grain;
- absolute paths to `scenario.json`, the working directory, DuckDB, and the dbt project.

Counts and model mappings come from the fresh instance record;
previews use bounded read-only DuckDB queries. Terminal tables must bound displayed cell width
without changing stored values. Opening does not dump the full JSON, scan all raw rows for a
preview, launch an editor/browser, or open a SQL shell.

Working copies are disposable and are not a place for durable experiments. `open` replaces the
working copy unconditionally. The scenario definition is parsed and semantically validated
before preparation, and saved structural request and raw-size checks still apply to authored
entries before preparation; a manually changed definition must not silently bypass them.
Preview/query failures remain visible
rather than being rendered as a successful empty result.

## 8. Seed changes and deletion

`seed` parses and validates the current personal scenario JSON and prepares a fresh working copy
for the requested seed. It never calls the LLM or changes the scenario's authored logic. For
CLI-authored entries it repeats saved structural request and raw-size checks; imported examples
require the existing clean control only. A failure does not trigger a seed search or automatic
scenario repair and leaves the previous seed/current pointer intact.

After success the current pointer and seed are updated atomically; any existing working copy
is replaced, not retained. Requesting the current seed again restores a fresh copy from the
verified cache baseline (or from a fresh build on a cache miss), regardless of the state of
the existing working copy. Different seeds are not guaranteed to change every value.
If the user has manually changed the scenario definition, saved structural requirements for an
authored entry still apply before preparation; a changed definition must not silently bypass them.

`delete` removes the selected personal scenario, its catalog/entry metadata, and its owned
working copy. It never modifies package examples, repository files, other catalog entries,
provider profiles, or immutable cache baselines. A deleted bundled example is not reimported
on the next invocation. Cached baselines and authoring history may remain; general cache pruning
is outside version 1. Deletion accepts a validated catalog identifier and confines every removal
to the corresponding workspace-owned paths; unknown identifiers produce an input error. The
explicit command is authorization to delete, with no additional interactive confirmation required.
Failed or interrupted deletion MUST retain enough catalog metadata to identify remaining owned
paths and complete deletion by rerunning the command. Remove the catalog entry only after its
owned scenario files and working copy have been removed.

## 9. Output and error contract

Normal results go to stdout; progress and actionable errors go to stderr. Expected failures have
no default traceback and include the failed stage and relevant local artifact path when present.
Long operations report their stage: authoring attempt, validation, materialization, and requirement
checks. A successful `connect` must not be advertised as a successful scenario build.

| Exit status | Meaning |
| --- | --- |
| `0` | Requested command completed successfully. |
| `2` | Invalid arguments, unsupported selection, identifier collision, missing scenario, or invalid existing scenario input. |
| `3` | Missing credential, provider authentication/quota/model/network failure, or provider refusal. |
| `4` | Authoring attempts exhausted, prepared instance fails saved CLI requirements, or a data-dependent clean-control failure during `create` (bad authored cast, unmapped category, empty output). The `failure_record.json` is still preserved for diagnosis. |
| `5` | Local workspace, materializer, runtime, or integrity failure; clean-control failure during `seed`/`open` of an imported example; infrastructure failure during `create`. |
| `130` | User interruption; no partial success or current-instance switch. |

A `seed` failure against the saved structural request or raw-size preset of an authored entry
reports `4`; the same clean-control failure for an imported example, where no user request is
at fault, reports `5`.

Noninteractive commands never pause for unspecified input. Tests should assert meaningful
fields, exit statuses, and effects rather than incidental terminal spacing or color codes.

## 10. Validation and release gates

### 10.1. Fast offline tests

Use pytest temporary workspaces and fake provider transports.
Normal tests MUST NOT call either live API or depend on a real key or home files.
Cover command parsing/help, choices/defaults and preset boundaries, environment credential
source/redaction,
provider request/response normalization, and atomic catalog/current-instance updates.

Command coverage includes bootstrap once, persistent deletion of imported examples, deterministic
listing, fresh-copy replacement on repeated opening, unknown identifiers, rejected out-of-workspace deletion,
preserved neighboring entries, unchanged scenario logic on reseed, same-seed fresh-copy restore,
and previous-state preservation
on failed/interrupted preparation. Test resumed deletion and atomic single-command publication
with leftover temporary files.

Structural tests cover requested features present/absent, layer isolation, permitted additional
operations, intermediate derive-node detection, strict JOIN modes, and composite raw PK versus
analytical grain. A valid operation with no data effect satisfies the MVP's structural check.
Test raw-size boundaries and downstream counts above the preset ceiling without a size failure.

Authoring tests include repairs accepted on the fifth response and exhaustion after exactly
five generation requests, fixed requirements/ID/seed across repairs, raw-size failures, provider
refusal/truncation, and `429`/authentication/timeouts with no hidden retry or paid fallback.
Check that prompts include the full packaged specification, current schema and selected pair;
pair selection is deterministic, independent of personal edits, ranks without a domain-match
criterion, measures length on the compact JSON actually sent, derives annotations from the
shared feature extractor, and keeps requirements even
when two examples cannot cover them all. Repairs include only the last candidate/current errors.
Cover the default/configured output budget and known model-limit incompatibilities. Assert
that invalid candidates are not compiled and API keys do not reach prompts, saved files,
output, or dbt child environments.

### 10.2. Real pipeline and installation tests

A few small end-to-end cases use real validators, Parquet, DuckDB, dbt, and cache, with only the
LLM transport substituted. Cover create -> list -> open, reseed -> open, failed preparation with
the previous seed/pointer retained, and delete -> fresh-process list. At least one full command
sequence runs as separate CLI processes to verify persisted state. Check real output queries,
passing dbt tests, artifact paths, and cache-hit copy without rebuild on repeated opening
(the working copy is still replaced by a fresh copy).

Build both wheel and source distribution. Install the wheel in a clean supported Python
environment and run `plgen` outside the checkout, including opening a bundled example without
credentials. Verify the bundled full specification, scenarios, prompt resources, schema
generation, runtime dependencies and dbt executable discovery. Runtime resources must not depend
on developer-only `tasks/`, checkout `docs/`, or preexisting `artifacts/` paths. Existing relevant generator and
contract quality gates continue to apply; the CLI suite does not duplicate all their internals.

### 10.3. Scale and live-authoring checks

Separate offline slow tests prepare representative medium and large instances, including
composite keys, LEFT/INNER JOINs, filtering, and deduplication. Record elapsed time, peak memory,
realized maximum raw rows, downstream row counts, and clean/requirement results. Include a
topology with JOIN expansion beyond the raw preset ceiling and a large raw-table case near
that ceiling. Measure generation, record creation/checksums, cache verification/copy, and
structural/raw-size checks; avoid fragile wall-time assertions in routine unit tests.
Medium/large support is not declared verified until these checks pass.

Live-authoring evaluation is a separate explicitly enabled run using the user's selected free
model/project. It is not a unit-test gate. Evaluate both adapters on the same small diverse
request set, including combined requirements, and report accepted instances, attempts needed,
failure categories, input/output/reasoning token usage when available, and elapsed time.
Five attempts is a budget, not a guaranteed success rate. API unavailability is recorded
separately from authoring quality.
No automatic corpus-wide live generation or paid fallback is performed.
