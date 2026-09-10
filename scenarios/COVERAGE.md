# Scenario Corpus Coverage Plan

Baseline plan slice T01: domain families, scenario sizes, scalar types, raw generators,
column properties, and key roles. Slice T02 (appended below, after the `D-` rows):
relationships, staging columns and rows, cast type transitions, and the first interaction
families. Slice T03 (appended below, after the T02 rows): intermediate operations, joins
and grain, structured expressions and conditions, DAG topology motifs, and the related
interactions. Slice T04 (appended below, after the T03 rows): outputs, metrics, explicit
assertions, and the remaining interactions. The baseline plan is now frozen and ready for
T05 authoring. Output counts are covered by F-SIZE-005/006 and are not repeated; row order
is significant because it breaks selection ties and MUST NOT be changed during an
authoring run.
because it breaks selection ties and MUST NOT be changed during an authoring run.

Conventions:

- `F-` rows are individual feature or boundary requirements with target 5.
- `I-` rows are important interaction requirements with target 3. Each names a combination
  that changes compilation semantics, lineage or grain reasoning, generated data behavior,
  or fault applicability; superficial combinations stay covered by their atomic rows.
- `T-` rows are DAG or data-flow topology motif requirements with target 3. Each names an
  executable graph structure, not a renaming of an equivalent graph in another domain.
- `D-` rows are domain-family requirements with target 6. Every scenario belongs to exactly
  one domain family and claims exactly one `D-` row.
- Every predicate is decidable from the scenario file and its `ValidatedScenario`.
- `actual(r)` is the number of distinct scenario files whose claims include `r`. A scenario
  contributes at most one count per requirement.
- No corpus scenario exists yet (`scenarios/*.json` is empty), so every `actual` is 0.

## Requirements

| ID | Predicate | Target | Actual |
| --- | --- | ---: | ---: |
| F-SIZE-001 | The scenario declares exactly 3 raw tables (minimum raw/staging count). | 5 | 0 |
| F-SIZE-002 | The scenario declares exactly 4 raw tables (maximum raw/staging count). | 5 | 0 |
| F-SIZE-003 | The scenario declares exactly 2 intermediate models (minimum intermediate count). | 5 | 0 |
| F-SIZE-004 | The scenario declares exactly 3 intermediate models (maximum intermediate count). | 5 | 0 |
| F-SIZE-005 | The scenario declares exactly 1 output model (minimum output count). | 5 | 0 |
| F-SIZE-006 | The scenario declares exactly 2 output models (maximum output count). | 5 | 0 |
| F-SIZE-007 | At least one raw table has an exact row count (`rows.min == rows.max`). | 5 | 0 |
| F-SIZE-008 | At least one raw table has a ranged row count (`rows.max > rows.min`). | 5 | 0 |
| F-SIZE-009 | At least one raw table is narrow (at most 2 columns). | 5 | 0 |
| F-SIZE-010 | At least one raw table is wide (at least 5 columns). | 5 | 0 |
| F-TYPE-001 | At least one raw column has type `string`. | 5 | 0 |
| F-TYPE-002 | At least one raw column has type `integer`. | 5 | 0 |
| F-TYPE-003 | At least one raw column has type `float`. | 5 | 0 |
| F-TYPE-004 | At least one raw column has type `boolean`. | 5 | 0 |
| F-TYPE-005 | At least one raw column has type `date`. | 5 | 0 |
| F-TYPE-006 | At least one raw column has type `timestamp`. | 5 | 0 |
| F-GEN-001 | At least one raw column uses a `formatted_id` generator. | 5 | 0 |
| F-GEN-002 | At least one raw column uses an `integer_range` generator. | 5 | 0 |
| F-GEN-003 | At least one raw column uses a `float_range` generator. | 5 | 0 |
| F-GEN-004 | At least one raw column uses a `date_range` generator. | 5 | 0 |
| F-GEN-005 | At least one raw column uses a `timestamp_range` generator. | 5 | 0 |
| F-GEN-006 | At least one raw column uses a `categorical` generator. | 5 | 0 |
| F-GEN-007 | At least one raw column uses a `boolean` generator. | 5 | 0 |
| F-GEN-008 | At least one raw column uses a `random_string` generator. | 5 | 0 |
| F-GEN-009 | At least one raw column uses a `template_string` generator. | 5 | 0 |
| F-GEN-010 | At least one raw column uses a `foreign_key` generator. | 5 | 0 |
| F-GEN-011 | At least one raw column uses a `person_name` generator. | 5 | 0 |
| F-GEN-012 | At least one raw column uses an `email` generator. | 5 | 0 |
| F-GEN-013 | At least one raw column uses a `city` generator. | 5 | 0 |
| F-GEN-014 | At least one raw column uses a `street_address` generator. | 5 | 0 |
| F-GEN-015 | At least one raw column uses a `company_name` generator. | 5 | 0 |
| F-GEN-016 | At least one raw column uses a `phone_number` generator. | 5 | 0 |
| F-OPT-001 | At least one `formatted_id` generator has an empty prefix (`prefix == ""`). | 5 | 0 |
| F-OPT-002 | At least one `formatted_id` generator has a non-empty prefix. | 5 | 0 |
| F-OPT-003 | At least one `formatted_id` generator uses the default start (`start == 1`). | 5 | 0 |
| F-OPT-004 | At least one `formatted_id` generator uses a custom start (`start != 1`). | 5 | 0 |
| F-OPT-005 | At least one `float_range` generator uses the default `decimal_places` (`== 2`). | 5 | 0 |
| F-OPT-006 | At least one `float_range` generator uses custom `decimal_places` (`!= 2`). | 5 | 0 |
| F-OPT-007 | At least one `categorical` generator has absent `weights`. | 5 | 0 |
| F-OPT-008 | At least one `categorical` generator has present `weights`. | 5 | 0 |
| F-OPT-009 | At least one `boolean` generator uses the default probability (`true_probability == 0.5`). | 5 | 0 |
| F-OPT-010 | At least one `boolean` generator uses a biased probability (`true_probability != 0.5`). | 5 | 0 |
| F-OPT-011 | At least one `random_string` generator uses the default alphabet. | 5 | 0 |
| F-OPT-012 | At least one `random_string` generator uses a custom alphabet. | 5 | 0 |
| F-OPT-013 | At least one `foreign_key` generator has `target_side` `left`. | 5 | 0 |
| F-OPT-014 | At least one `foreign_key` generator has `target_side` `right`. | 5 | 0 |
| F-OPT-015 | At least one Faker-backed generator uses the default locale (`en_US`). | 5 | 0 |
| F-OPT-016 | At least one Faker-backed generator uses a non-default locale. | 5 | 0 |
| F-BND-001 | At least one `formatted_id` generator uses minimum capacity (`digits == 1`). | 5 | 0 |
| F-BND-002 | At least one `formatted_id` generator uses maximum capacity (`digits == 18`). | 5 | 0 |
| F-BND-003 | At least one `random_string` generator has fixed length (`min_length == max_length`). | 5 | 0 |
| F-BND-004 | At least one `random_string` generator has `min_length == 1`. | 5 | 0 |
| F-BND-005 | At least one `categorical` generator has exactly one value. | 5 | 0 |
| F-BND-006 | At least one `integer_range` generator has minimal span (`max - min == 1`). | 5 | 0 |
| F-BND-007 | At least one `float_range` generator has `decimal_places == 0`. | 5 | 0 |
| F-NULL-001 | At least one raw column is non-nullable (`nullable == false`). | 5 | 0 |
| F-NULL-002 | At least one raw column is nullable (`nullable == true`). | 5 | 0 |
| F-NULL-003 | At least one nullable column declares `null_probability == 0.0`. | 5 | 0 |
| F-NULL-004 | At least one column has partial nulls (`0.0 < null_probability < 1.0`). | 5 | 0 |
| F-NULL-005 | At least one column has `null_probability == 1.0`. | 5 | 0 |
| F-NULL-006 | At least one raw column has `unique == true`. | 5 | 0 |
| F-KEY-001 | At least one raw table has an empty `primary_key`. | 5 | 0 |
| F-KEY-002 | At least one raw table has a single-column `primary_key`. | 5 | 0 |
| F-KEY-003 | At least one raw table has a composite `primary_key` (at least 2 members). | 5 | 0 |
| F-KEY-004 | At least one raw column uses a `foreign_key` generator (dependent or bridge key role). | 5 | 0 |
| F-KEY-005 | At least one plain payload column exists: not a `primary_key` member, no `foreign_key` generator, and not named in any relationship endpoint of its table. | 5 | 0 |
| F-KEY-006 | At least one multi-role column exists: satisfies at least two of `primary_key` membership, `unique == true`, and `foreign_key` generator. | 5 | 0 |
| D-DMN-001 | The scenario's `domain` is `retail`. | 6 | 0 |
| D-DMN-002 | The scenario's `domain` is `healthcare`. | 6 | 0 |
| D-DMN-003 | The scenario's `domain` is `finance`. | 6 | 0 |
| D-DMN-004 | The scenario's `domain` is `logistics`. | 6 | 0 |
| D-DMN-005 | The scenario's `domain` is `education`. | 6 | 0 |
| D-DMN-006 | The scenario's `domain` is `manufacturing`. | 6 | 0 |
| D-DMN-007 | The scenario's `domain` is `hospitality`. | 6 | 0 |
| D-DMN-008 | The scenario's `domain` is `telecom`. | 6 | 0 |
| D-DMN-009 | The scenario's `domain` is `energy`. | 6 | 0 |
| D-DMN-010 | The scenario's `domain` is `agriculture`. | 6 | 0 |
| D-DMN-011 | The scenario's `domain` is `transport`. | 6 | 0 |
| D-DMN-012 | The scenario's `domain` is `human_resources`. | 6 | 0 |
| D-DMN-013 | The scenario's `domain` is `real_estate`. | 6 | 0 |
| D-DMN-014 | The scenario's `domain` is `media`. | 6 | 0 |
| D-DMN-015 | The scenario's `domain` is `sports`. | 6 | 0 |
| D-DMN-016 | The scenario's `domain` is `insurance`. | 6 | 0 |
| F-REL-001 | The scenario declares at least one `one_to_one` relationship. | 5 | 0 |
| F-REL-002 | The scenario declares at least one `one_to_many` relationship. | 5 | 0 |
| F-REL-003 | The scenario declares at least one `many_to_one` relationship. | 5 | 0 |
| F-REL-004 | The scenario declares at least one `many_to_many` relationship materialized through a declared bridge table (bridge table distinct from both endpoints, disjoint left/right bridge columns). | 5 | 0 |
| F-REL-005 | The scenario declares at least one direct relationship (cardinality other than `many_to_many`, no bridge table). | 5 | 0 |
| F-REL-006 | A `one_to_one` relationship has its foreign-key columns on the left endpoint (left columns generated as foreign keys targeting the right side). | 5 | 0 |
| F-REL-007 | A `one_to_one` relationship has its foreign-key columns on the right endpoint (right columns generated as foreign keys targeting the left side). | 5 | 0 |
| F-REL-008 | At least one relationship endpoint has exactly one column (single-column key arity). | 5 | 0 |
| F-REL-009 | At least one relationship endpoint has at least two columns (composite key arity). | 5 | 0 |
| F-STG-001 | At least one staging column is a passthrough (`source == target` with no operations). | 5 | 0 |
| F-STG-002 | At least one staging column is a rename (`source != target`). | 5 | 0 |
| F-STG-003 | At least one raw column is omitted from its staging model (no staging column lists it as `source`). | 5 | 0 |
| F-STG-004 | At least one staging column applies a `cast` operation. | 5 | 0 |
| F-STG-005 | At least one staging column applies a `trim` operation. | 5 | 0 |
| F-STG-006 | At least one staging column applies a `lower` operation. | 5 | 0 |
| F-STG-007 | At least one staging column applies an `upper` operation. | 5 | 0 |
| F-STG-008 | At least one staging column applies a `replace` operation. | 5 | 0 |
| F-STG-009 | At least one staging column applies a `map_values` operation. | 5 | 0 |
| F-STG-010 | At least one staging column applies a `null_if` operation. | 5 | 0 |
| F-STG-011 | At least one staging column applies a `coalesce` operation. | 5 | 0 |
| F-STG-012 | At least one staging column applies a multi-operation chain (at least 2 operations in declared order). | 5 | 0 |
| F-STG-013 | At least one staging chain changes the column type (chain final type differs from the raw source type, per `ValidatedScenario` staging lineage). | 5 | 0 |
| F-STG-014 | At least one `cast` to `date`/`timestamp` carries an explicit `format`. | 5 | 0 |
| F-STG-015 | At least one `cast` to `date`/`timestamp` omits `format` (default ISO parsing). | 5 | 0 |
| F-STG-016 | At least one `map_values` operation uses `on_unmapped == "keep"`. | 5 | 0 |
| F-STG-017 | At least one `map_values` operation uses `on_unmapped == "null"`. | 5 | 0 |
| F-STG-018 | At least one `map_values` operation uses `on_unmapped == "error"`. | 5 | 0 |
| F-TRN-001 | At least one staging chain contains a `cast` from `string` to `date`. | 5 | 0 |
| F-TRN-002 | At least one staging chain contains a `cast` from `string` to `timestamp`. | 5 | 0 |
| F-TRN-003 | At least one staging chain contains a `cast` from `string` to `integer`. | 5 | 0 |
| F-TRN-004 | At least one staging chain contains a `cast` from `string` to `float`. | 5 | 0 |
| F-TRN-005 | At least one staging chain contains a `cast` from `string` to `boolean`. | 5 | 0 |
| F-TRN-006 | At least one staging chain contains a `cast` from `integer` to `string`. | 5 | 0 |
| F-TRN-007 | At least one staging chain contains a `cast` from `float` to `string`. | 5 | 0 |
| F-TRN-008 | At least one staging chain contains a `cast` from `boolean` to `string`. | 5 | 0 |
| F-TRN-009 | At least one staging chain contains a `cast` from `date` to `string`. | 5 | 0 |
| F-TRN-010 | At least one staging chain contains a `cast` from `timestamp` to `string`. | 5 | 0 |
| F-TRN-011 | At least one staging chain contains a `cast` from `integer` to `float`. | 5 | 0 |
| F-TRN-012 | At least one staging chain contains a `cast` from `float` to `integer`. | 5 | 0 |
| F-TRN-013 | At least one staging chain contains a `cast` from `integer` to `boolean`. | 5 | 0 |
| F-TRN-014 | At least one staging chain contains a `cast` from `float` to `boolean`. | 5 | 0 |
| F-TRN-015 | At least one staging chain contains a `cast` from `boolean` to `integer`. | 5 | 0 |
| F-TRN-016 | At least one staging chain contains a `cast` from `boolean` to `float`. | 5 | 0 |
| F-ROW-001 | At least one staging model declares a `filter` row operation. | 5 | 0 |
| F-ROW-002 | At least one staging model declares a `deduplicate` row operation. | 5 | 0 |
| F-ROW-003 | At least one staging model declares `filter` before `deduplicate` in `row_operations` order. | 5 | 0 |
| F-ROW-004 | At least one staging model declares `deduplicate` before `filter` in `row_operations` order. | 5 | 0 |
| I-TGK-001 | A raw column combines a `foreign_key` generator with `nullable == true` and `null_probability > 0` (optional foreign key; only non-null dependents keep relationship-test derivation). | 3 | 0 |
| I-TGK-002 | A raw column combines `unique == true` with a weighted `categorical` generator (`weights` present; distinct-value capacity binds the row count). | 3 | 0 |
| I-TGK-003 | A raw table has a composite primary key whose member columns use at least two different generator kinds. | 3 | 0 |
| I-TGK-004 | A primary-key column combines `unique == true` with a minimum-capacity `formatted_id` generator (`digits == 1`). | 3 | 0 |
| I-REL-001 | A `one_to_many` relationship has composite endpoint arity (at least 2 columns per endpoint). | 3 | 0 |
| I-REL-002 | A `many_to_one` relationship has composite endpoint arity (at least 2 columns per endpoint). | 3 | 0 |
| I-REL-003 | A `many_to_many` relationship has composite bridge keys (a bridge side with at least 2 columns). | 3 | 0 |
| I-REL-004 | A `one_to_one` relationship combines a left-side foreign key with composite endpoint arity. | 3 | 0 |
| I-STG-001 | A staging chain on a nullable column ends with `null_if` (introduced nulls suppress the derived not-null assertion). | 3 | 0 |
| I-STG-002 | A staging chain combines a type-changing `cast` with a later `coalesce` whose literal matches the final type. | 3 | 0 |
| I-STG-003 | A staging chain on a nullable column applies `map_values` with `on_unmapped == "null"`. | 3 | 0 |
| I-ROW-001 | A staging model deduplicates on keys equal to its declared `grain` (deduplication establishes the grain). | 3 | 0 |
| I-ROW-002 | A staging model with both `filter` and `deduplicate` deduplicates on keys that trace via lineage to the raw primary key. | 3 | 0 |
| F-INT-001 | The scenario declares at least one `transform` intermediate model. | 5 | 0 |
| F-INT-002 | The scenario declares at least one `join` intermediate model. | 5 | 0 |
| F-INT-003 | The scenario declares at least one `aggregate` intermediate model. | 5 | 0 |
| F-INT-004 | The scenario declares at least one `deduplicate` intermediate model. | 5 | 0 |
| F-JOIN-001 | At least one join uses type `inner`. | 5 | 0 |
| F-JOIN-002 | At least one join uses type `left`. | 5 | 0 |
| F-JOIN-003 | At least one join's keys resolve to a declared `one_to_many` relationship lineage. | 5 | 0 |
| F-JOIN-004 | At least one join's keys resolve to a declared `many_to_one` relationship lineage. | 5 | 0 |
| F-JOIN-005 | At least one join's keys resolve to a declared `one_to_one` relationship lineage. | 5 | 0 |
| F-JOIN-006 | At least one join's keys resolve through a `many_to_many` bridge pattern. | 5 | 0 |
| F-PLC-001 | At least one `transform` model reads directly from a staging model. | 5 | 0 |
| F-PLC-002 | At least one `transform` model reads from another intermediate model. | 5 | 0 |
| F-PLC-003 | At least one `aggregate` model reads directly from a staging model. | 5 | 0 |
| F-PLC-004 | At least one `aggregate` model reads from another intermediate model. | 5 | 0 |
| F-PLC-005 | At least one `deduplicate` model reads directly from a staging model. | 5 | 0 |
| F-PLC-006 | At least one `deduplicate` model reads from another intermediate model. | 5 | 0 |
| F-PRJ-001 | At least one join projects left-side columns only (no right-side columns). | 5 | 0 |
| F-PRJ-002 | At least one join projects right-side columns only (no left-side columns). | 5 | 0 |
| F-PRJ-003 | At least one join projects columns from both sides. | 5 | 0 |
| F-GRN-001 | At least one intermediate model declares a single-column grain. | 5 | 0 |
| F-GRN-002 | At least one intermediate model declares a composite grain (at least 2 columns). | 5 | 0 |
| F-SRT-001 | At least one deduplication `order_by` (staging or intermediate) uses direction `asc`. | 5 | 0 |
| F-SRT-002 | At least one deduplication `order_by` (staging or intermediate) uses direction `desc`. | 5 | 0 |
| F-EXP-001 | At least one expression is a `column` reference. | 5 | 0 |
| F-EXP-002 | At least one expression is a `literal`. | 5 | 0 |
| F-EXP-003 | At least one expression is `binary` arithmetic. | 5 | 0 |
| F-EXP-004 | At least one expression is a `date_part` extraction. | 5 | 0 |
| F-EXP-005 | At least one expression is a `coalesce`. | 5 | 0 |
| F-EXP-006 | At least one `binary` expression uses operator `add`. | 5 | 0 |
| F-EXP-007 | At least one `binary` expression uses operator `subtract`. | 5 | 0 |
| F-EXP-008 | At least one `binary` expression uses operator `multiply`. | 5 | 0 |
| F-EXP-009 | At least one `binary` expression uses operator `divide` (runtime safe-division semantics). | 5 | 0 |
| F-EXP-010 | At least one `date_part` expression extracts `year`. | 5 | 0 |
| F-EXP-011 | At least one `date_part` expression extracts `quarter`. | 5 | 0 |
| F-EXP-012 | At least one `date_part` expression extracts `month`. | 5 | 0 |
| F-EXP-013 | At least one `date_part` expression extracts `day`. | 5 | 0 |
| F-EXP-014 | At least one `date_part` expression extracts `day_of_week`. | 5 | 0 |
| F-EXP-015 | At least one `binary` expression operates on integer operands. | 5 | 0 |
| F-EXP-016 | At least one `binary` expression operates on float operands. | 5 | 0 |
| F-EXP-017 | At least one `date_part` expression operates on a date value. | 5 | 0 |
| F-EXP-018 | At least one `date_part` expression operates on a timestamp value. | 5 | 0 |
| F-EXP-019 | At least one `coalesce` expression combines string values. | 5 | 0 |
| F-EXP-020 | At least one nested expression exists (an expression node with a non-leaf expression child, depth at least 2). | 5 | 0 |
| F-EXP-021 | At least one `transform` model declares a derived column. | 5 | 0 |
| F-EXP-022 | At least one `join` model declares a derived column. | 5 | 0 |
| F-CND-001 | At least one condition is a `comparison`. | 5 | 0 |
| F-CND-002 | At least one condition is an `in` membership test. | 5 | 0 |
| F-CND-003 | At least one condition is an `is_null` test. | 5 | 0 |
| F-CND-004 | At least one condition is an `all` conjunction. | 5 | 0 |
| F-CND-005 | At least one condition is an `any` disjunction. | 5 | 0 |
| F-CND-006 | At least one condition is a `not` negation. | 5 | 0 |
| F-CND-007 | At least one `comparison` uses operator `eq`. | 5 | 0 |
| F-CND-008 | At least one `comparison` uses operator `ne`. | 5 | 0 |
| F-CND-009 | At least one `comparison` uses operator `lt`. | 5 | 0 |
| F-CND-010 | At least one `comparison` uses operator `lte`. | 5 | 0 |
| F-CND-011 | At least one `comparison` uses operator `gt`. | 5 | 0 |
| F-CND-012 | At least one `comparison` uses operator `gte`. | 5 | 0 |
| F-CND-013 | At least one `in` condition uses `negated == true`. | 5 | 0 |
| F-CND-014 | At least one `is_null` condition uses `negated == true` (not-null test). | 5 | 0 |
| F-CND-015 | At least one `comparison` operates on numeric operands. | 5 | 0 |
| F-CND-016 | At least one `comparison` operates on string operands. | 5 | 0 |
| F-CND-017 | At least one `comparison` operates on date/timestamp operands. | 5 | 0 |
| F-CND-018 | At least one boolean combination (`all`/`any`/`not`) is nested inside another condition (depth at least 2). | 5 | 0 |
| F-CND-019 | At least one staging model declares a `filter` row-operation condition. | 5 | 0 |
| F-CND-020 | At least one `transform` model declares a filter condition. | 5 | 0 |
| F-CND-021 | At least one `join` model declares a filter condition. | 5 | 0 |
| F-CND-022 | At least one `aggregate` model declares a pre-aggregation filter condition. | 5 | 0 |
| F-CND-023 | At least one output model declares a pre-aggregation filter condition. | 5 | 0 |
| T-CHAIN-001 | At least two intermediate models form a linear chain (an intermediate directly consuming another intermediate, single upstream). | 3 | 0 |
| T-FAN-001 | A join has two distinct direct inputs (fan-in from two upstream branches). | 3 | 0 |
| T-FAN-002 | A model has at least three distinct transitive staging ancestors (fan-in from three upstream branches). | 3 | 0 |
| T-BRANCH-001 | A staging or intermediate model is consumed by at least two intermediate models (branching from a shared upstream). | 3 | 0 |
| T-REUSE-001 | An intermediate model is the source of at least two output models, or of an intermediate and an output model (upstream reuse). | 3 | 0 |
| T-JOIN-001 | A join over two staging inputs feeds a downstream model (early join). | 3 | 0 |
| T-JOIN-002 | A join has at least one intermediate input that is itself transformed, joined, or aggregated (late join). | 3 | 0 |
| T-AGG-001 | A join has an aggregated intermediate ancestor (aggregation before the join). | 3 | 0 |
| T-AGG-002 | An aggregate model has a join ancestor (aggregation after the join). | 3 | 0 |
| T-DEDUP-001 | A deduplicate intermediate is consumed by a transform, join, or aggregate intermediate (deduplication before another transformation). | 3 | 0 |
| T-DEDUP-002 | A deduplicate intermediate has a join ancestor (deduplication after a fan-in operation). | 3 | 0 |
| T-OUT-001 | Two output models share a transitive intermediate ancestor (shared ancestry). | 3 | 0 |
| T-OUT-002 | Two output models declare different grains (grain column sets are not equal). | 3 | 0 |
| T-PART-001 | Every raw table reaches at least one output, and at least two outputs have different transitive staging-ancestor sets (full participation through structurally different paths). | 3 | 0 |
| I-ORD-001 | A `transform` model reads directly from an `aggregate` model (row-level derivation over a grouped grain). | 3 | 0 |
| I-ORD-002 | A `transform` model reads directly from a `deduplicate` model (derivation over a deduplicated grain). | 3 | 0 |
| I-ORD-003 | A `join` model has a direct `deduplicate` input (fan-in over deduplicated keys). | 3 | 0 |
| I-JOIN-001 | An `inner` join over a `one_to_many` lineage projects both sides and declares a composite grain. | 3 | 0 |
| I-JOIN-002 | A `left` join over a `many_to_one` lineage projects both sides and keeps the left input's projected grain. | 3 | 0 |
| I-JOIN-003 | An `inner` join over a `one_to_one` lineage projects a single side and declares a single-column grain. | 3 | 0 |
| I-JAG-001 | A `join` model has a direct `aggregate` input (aggregation before the join). | 3 | 0 |
| I-JAG-002 | An `aggregate` model has a direct `join` input (aggregation after the join). | 3 | 0 |
| I-SHR-001 | Two intermediate models share one upstream with different operations (branching into different downstream operations). | 3 | 0 |
| I-SHR-002 | Two output models share one intermediate source with different grains. | 3 | 0 |
| I-EXP-001 | A `binary` arithmetic expression over numeric columns appears in a derived column. | 3 | 0 |
| I-EXP-002 | A `date_part` extraction over a timestamp appears in a derived column. | 3 | 0 |
| I-CND-001 | A `comparison` over string operands appears in a filter condition. | 3 | 0 |
| I-CND-002 | A `comparison` over date/timestamp operands appears in a filter condition. | 3 | 0 |
| F-OUT-001 | At least one output is row-preserving (grouping covers the source model's grain). | 5 | 0 |
| F-OUT-002 | At least one output is coarsening (grouping reduces the source model's grain). | 5 | 0 |
| F-OUT-003 | At least one output groups by a single key. | 5 | 0 |
| F-OUT-004 | At least one output groups by multiple keys. | 5 | 0 |
| F-OUT-005 | At least one output declares no dimensions. | 5 | 0 |
| F-OUT-006 | At least one output declares at least one dimension. | 5 | 0 |
| F-OUT-007 | At least one output `group_by` contains a rename (`source != target`). | 5 | 0 |
| F-OUT-008 | At least one output `group_by` is fully passthrough (every `source == target`). | 5 | 0 |
| F-OUT-009 | At least one output declares a single-column grain. | 5 | 0 |
| F-OUT-010 | At least one output declares a composite grain (at least 2 columns). | 5 | 0 |
| F-OUT-011 | At least one output model shares its source with another intermediate or output model. | 5 | 0 |
| F-MET-001 | At least one metric uses function `count_rows`. | 5 | 0 |
| F-MET-002 | At least one metric uses function `count`. | 5 | 0 |
| F-MET-003 | At least one metric uses function `count_distinct`. | 5 | 0 |
| F-MET-004 | At least one metric uses function `sum`. | 5 | 0 |
| F-MET-005 | At least one metric uses function `avg`. | 5 | 0 |
| F-MET-006 | At least one metric uses function `min`. | 5 | 0 |
| F-MET-007 | At least one metric uses function `max`. | 5 | 0 |
| F-MET-008 | At least one metric uses function `conditional_count`. | 5 | 0 |
| F-MET-009 | At least one metric uses function `conditional_sum`. | 5 | 0 |
| F-MET-010 | At least one metric appears in an `aggregate` intermediate model. | 5 | 0 |
| F-MET-011 | At least one metric appears in an output model. | 5 | 0 |
| F-MET-012 | At least one numeric metric (`sum`, `avg`, or `conditional_sum`) takes an integer or float column. | 5 | 0 |
| F-MET-013 | At least one `min`/`max` metric takes a date or timestamp column. | 5 | 0 |
| F-MET-014 | At least one `count`/`count_distinct` metric takes a nullable column (null-skipping aggregation). | 5 | 0 |
| F-ASM-001 | The scenario declares an explicit `not_null` assertion. | 5 | 0 |
| F-ASM-002 | The scenario declares an explicit `unique` assertion. | 5 | 0 |
| F-ASM-003 | The scenario declares an explicit `accepted_values` assertion. | 5 | 0 |
| F-ASM-004 | The scenario declares an explicit `relationships` assertion. | 5 | 0 |
| F-ASM-005 | The scenario declares an explicit `row_count` assertion. | 5 | 0 |
| F-ASM-006 | The scenario declares an explicit `column_range` assertion. | 5 | 0 |
| F-ASM-007 | At least one `not_null` assertion covers at least 2 columns. | 5 | 0 |
| F-ASM-008 | At least one `unique` assertion covers at least 2 columns (composite order retained). | 5 | 0 |
| F-ASM-009 | At least one `accepted_values` assertion targets a string column. | 5 | 0 |
| F-ASM-010 | At least one `accepted_values` assertion targets a numeric column. | 5 | 0 |
| F-ASM-011 | At least one `relationships` assertion has composite arity (at least 2 columns per side). | 5 | 0 |
| F-ASM-012 | At least one `row_count` assertion sets only `min`. | 5 | 0 |
| F-ASM-013 | At least one `row_count` assertion sets only `max`. | 5 | 0 |
| F-ASM-014 | At least one `row_count` assertion sets both `min` and `max`. | 5 | 0 |
| F-ASM-015 | At least one `column_range` assertion sets only `min`. | 5 | 0 |
| F-ASM-016 | At least one `column_range` assertion sets only `max`. | 5 | 0 |
| F-ASM-017 | At least one `column_range` assertion sets both `min` and `max`. | 5 | 0 |
| F-ASM-018 | At least one `column_range` assertion uses `inclusive == false`. | 5 | 0 |
| F-ASM-019 | At least one explicit assertion targets a raw table. | 5 | 0 |
| F-ASM-020 | At least one explicit assertion targets a staging model. | 5 | 0 |
| F-ASM-021 | At least one explicit assertion targets an intermediate model. | 5 | 0 |
| F-ASM-022 | At least one explicit assertion targets an output model. | 5 | 0 |
| I-OUT-001 | An output model computes a conditional metric (`conditional_count` or `conditional_sum`) over an aggregated intermediate source. | 3 | 0 |
| I-OUT-002 | An output model computes a numeric metric (`sum` or `avg`) over a join-sourced upstream. | 3 | 0 |
| I-OUT-003 | An output model declares at least 2 dimensions equal to its composite grain. | 3 | 0 |
| I-OUT-004 | An output model with a single-column grain reads from a deduplicate model (grain preserved through deduplication). | 3 | 0 |
| I-ASM-001 | A `not_null` assertion targets a nullable string column of a staging model. | 3 | 0 |
| I-ASM-002 | A `unique` assertion over a composite key targets an intermediate model. | 3 | 0 |
| I-ASM-003 | A `row_count` assertion with both bounds targets an output model. | 3 | 0 |
| I-ASM-004 | A `column_range` assertion targets a metric column of an output model. | 3 | 0 |
| I-ASM-005 | A composite-arity `relationships` assertion points from an intermediate model to a raw table. | 3 | 0 |
| I-CND-003 | A conditional metric (`conditional_count` or `conditional_sum`) carries a `comparison` condition. | 3 | 0 |

## Scenario claims

| File | Requirement IDs |
| --- | --- |
