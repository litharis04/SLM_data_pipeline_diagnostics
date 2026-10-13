# plgen

`plgen` generates reproducible local data pipelines with synthetic data for learning and
experimentation. You can use a bundled scenario or author a new one through an external LLM API.

Each pipeline has four layers:

1. **Raw:** 3–4 synthetic tables, written as Parquet files and loaded into DuckDB.
2. **Staging:** one dbt model per raw table, with transformations such as `trim`, `replace`,
   and `map_values`.
3. **Intermediate:** 2–3 dbt models for joins, filters, derived columns, deduplication, and
   preliminary aggregations.
4. **Output:** 1–2 dbt models producing final aggregations with functions such as `count_rows`,
   `sum`, and `avg`.

All dbt models run in DuckDB. The `scenario.json` file defines the domain, tables, relationships,
transformations, and output metrics. A separate data seed controls data variation while
preserving the pipeline's structure and logic.

## Installation

Requires Python 3.14 or later.

### From source

```bash
git clone https://github.com/litharis04/SLM_data_pipeline_diagnostics.git
cd SLM_data_pipeline_diagnostics
pip install . # or: uv pip install .
```

### For development (editable install)

From the repository directory:

```bash
pip install -e . # or: uv pip install -e .
```

### From Git, without a checkout

```bash
pipx install --include-deps git+https://github.com/litharis04/SLM_data_pipeline_diagnostics.git
```

The [`--include-deps` option](https://pipx.pypa.io/latest/how-to/expose-apps.html) also exposes
the `dbt` executable on `PATH`, which `plgen` needs to build pipelines.

Alternatively, run directly with uv:

```bash
uvx --from git+https://github.com/litharis04/SLM_data_pipeline_diagnostics.git plgen --help
```

### Verify the installation

```bash
plgen --version
```

## Usage

The default workspace is `./pipeline_workspace`, relative to the current directory. Use
`plgen --workspace PATH COMMAND ...` to choose another location, and `plgen COMMAND --help`
for command options.

- `plgen list`: list available scenarios. The package includes 144 bundled scenarios covering
  a range of pipeline structures and operations. They are copied into the workspace on the
  first catalog command, such as `list`.

- `plgen open SCENARIO_ID`: prepare a verified pipeline using the scenario's current seed
  (initially `0` for bundled scenarios). Generate data, build a dbt project, and run its tests,
  or restore a previously verified build from the cache.

- `plgen seed SCENARIO_ID SEED`: rebuild the pipeline with the chosen seed and make it the
  current instance. This preserves the scenario's logic and requires no LLM calls.
  Both `open` and `seed` replace the scenario's existing working copy, including any local edits.

- `plgen delete SCENARIO_ID`: delete a scenario from the personal catalog and remove its
  working copy. Cached baselines and authoring history remain.

- `plgen connect --provider {openrouter,gemini} --model MODEL`: configure and verify an
  OpenRouter or Gemini provider profile. Set the API key in `OPENROUTER_API_KEY` or
  `GEMINI_API_KEY`, respectively (e.g., `export OPENROUTER_API_KEY='...'`). Use `--key-env NAME`
  to reference a different environment variable. The optional `--max-output-tokens N` sets
  the output budget per authoring attempt; the default is 32,768 tokens.

- `plgen create --domain DOMAIN`: author, validate, and materialize a scenario using the
  active provider profile configured by `connect`. The scenario is saved under
  `WORKSPACE/scenarios/SCENARIO_ID/`, and the prepared pipeline is placed in its `work/`
  subdirectory. Available options:

  - `--domain DOMAIN`: the subject area used to guide scenario authoring (e.g., `agriculture`);
    required.
  - `--id ID`: an explicit scenario identifier; assigned automatically if omitted.
  - `--size {small,medium,large}`: the number of rows in the largest raw table:
    1–1,000 / 1,001–10,000 / 10,001–100,000, respectively. The default is `small`.
    Row counts are determined by the scenario's ranges and data seed.
  - `--composite-keys {auto,required,forbidden}`: `required` ensures at least one raw table
    has a composite primary key; `forbidden` disallows such keys. The default, `auto`, lets
    the LLM decide.
  - `--staging`: `trim`, `lower`, `upper`, `replace`, `map_values`, `null_if`, `coalesce`,
    `cast`, `filter`, `deduplicate`.
  - `--intermediate`: `filter`, `derive`, `deduplicate`.
  - `--joins {auto,inner,left,mixed}`: `inner` or `left` restricts all joins to that type;
    `mixed` requires both types. The default is `auto`.
  - `--metrics`: `count_rows`, `count`, `count_distinct`, `sum`, `avg`, `min`, `max`,
    `conditional_count`, `conditional_sum`.
  - `--seed SEED`: the data seed; defaults to `0`.

For `--staging`, `--intermediate`, and `--metrics`, use comma-separated lists (e.g.,
`--staging trim,cast`). Each selected feature must appear at least once; other supported
features may also appear.
