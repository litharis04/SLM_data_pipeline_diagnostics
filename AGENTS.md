## 1. Project purpose

This project builds a synthetic data pipeline fault-diagnosis environment and trains a small language model (SLM) to perform constrained tool-based diagnosis. The SLM is trained to follow a diagnostic policy for known fault families using a limited set of tools. It is not expected to discover arbitrary unknown failures.

## 2. System architecture overview

The project consists of five main subsystems.

### 2.1. Data pipeline subsystem

This is the synthetic data pipeline used to produce normal and faulty pipeline runs.

High-level flow:
raw data generation
  -> load into DuckDB
  -> dbt staging models
  -> dbt intermediate models
  -> dbt output models
  -> dbt tests

Pipeline layers:
- raw layer: generated Parquet files loaded into DuckDB.
- staging layer: initial cleaning, typing, normalization.
- intermediate layer: joins and basic business transformations.
- output layer: final analytical models, including aggregations.

Detailed pipeline structure, data profiles, and variability rules are described in `docs/PIPELINE_SPEC.md`.

### 2.2. Fault subsystem

The fault subsystem is responsible for deterministic fault injection.

It can:
- reset the pipeline to a healthy state;
- inject a fault using a seed;
- run the pipeline with the injected fault;
- produce a reproducible failure or test anomaly.

Faults may affect:
- raw data values;
- raw data structure;
- loading behavior;
- staging logic;
- output logic;

Important rule: The fault generator may know the hidden fault label. The diagnostic model and tool outputs must not receive that hidden label directly.

Detailed fault types and injection mechanics are described in `docs/FAULT_CATALOG.md` and `docs/FAULT_INJECTION.md`.

### 2.3. Diagnostic environment

The diagnostic environment exposes observations from faulty pipeline runs to the diagnosing model.
It provides:
- dbt run and test failure information;
- extracted log snippets;
- diagnostic tools;
- read-only inspection of pipeline state.

The diagnosing model does not directly fix the pipeline. It only observes the pipeline state and produces a diagnosis.
Diagnostic tools must be:
- deterministic for a fixed seed and fault state;
- read-only;
- limited in scope;
- unable to reveal hidden ground-truth labels.

Detailed tool contracts are described in `docs/DIAGNOSTIC_TOOLS.md`.

### 2.4. Supervision generation subsystem

This subsystem generates gold trajectories for SFT. It uses oracle solvers.
An oracle solver:
- receives a symptom or failed-test context;
- calls allowed diagnostic tools;
- observes tool outputs;
- chooses the next diagnostic step;
- produces a final diagnosis.

Oracle solvers are used as teachers for the SLM. They are not the runtime product agent.
Important rules:
- Oracle solvers must use only allowed diagnostic tools.
- Oracle solvers must not read hidden fault labels during the diagnostic trajectory.
- Hidden fault metadata may be used outside the trajectory to validate the oracle's final diagnosis and generated supervision.
- Oracle solvers must stay within the configured tool-call budget.

Detailed oracle rules are described in `docs/ORACLE_SPEC.md`.

### 2.5. ML subsystem

The ML subsystem uses generated trajectories to train and evaluate a small language model. Training may run in the cloud because local hardware is limited.
It includes:
- SFT dataset building;
- train/val/test splitting;
- model training;
- evaluation;
- baseline comparison.

Detailed training and evaluation rules are described in `docs/TRAINING.md` and `docs/EVALUATION.md`.

### 2.6. End-to-end data flow

healthy pipeline
  -> fault injection
  -> faulty pipeline run
  -> failed dbt model or dbt test
  -> extracted symptom / log snippet
  -> diagnostic environment
      -> oracle trajectory
          -> final diagnosis
          -> gold trajectory / SFT dataset
      -> SLM trajectory
          -> final diagnosis
          -> evaluation record


## 3. Non-negotiable rules

- Keep behavior deterministic where possible.
- Use seeds for all synthetic data and fault variants.
- Do not commit secrets.
- Do not run long cloud training jobs inside unit tests.
- Do not change public tool names without updating docs and tests.
- Do not introduce new fault families, diagnostic tools, or public data contracts unless required by the current task or specification.
- Keep fault generation separate from diagnosis: diagnostic code must not access fault-injection configuration or oracle-only metadata.
- Prefer small, testable vertical slices.
- Before marking a task complete, run all quality gates relevant to the changed code.
- Update `STATE.md` when a task materially changes the implemented project state.
- When implementation, tests, and documentation disagree, do not guess. Report the inconsistency and follow the task specification unless it explicitly updates the relevant project specification.


## 4. Repository map

- `STATE.md` - concise record of the current implementation state; not a specification.
- `tasks/` - task definitions.
- `docs/` - detailed specifications.
- `data/` - generated raw data.
- `dbt/` - dbt project.
- `artifacts/` - logs, trajectories, evaluation outputs.
- `training/` - SFT dataset preparation and cloud training scripts.
- `tests/` - pytest tests.


## 5. Minimal quality gates

A task is complete only if:
- acceptance criteria from the task file are met;
- existing tests pass;
- lint passes;
- docs are updated if public behavior changed;
- the change is reproducible by available commands.

If required checks do not exist yet, explicitly state what is missing.