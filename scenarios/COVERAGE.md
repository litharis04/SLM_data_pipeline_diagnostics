# Scenario Corpus Coverage Plan

Baseline plan slice T01: domain families, scenario sizes, scalar types, raw generators,
column properties, and key roles. Slice T02 (appended below, after the `D-` rows):
relationships, staging columns and rows, cast type transitions, and the first interaction
families. Later slices (T03–T04) append intermediate-model, expression, topology, output,
assertion, and remaining interaction rows after the existing rows; row order is significant
because it breaks selection ties and MUST NOT be changed during an authoring run.

Conventions:

- `F-` rows are individual feature or boundary requirements with target 5.
- `I-` rows are important interaction requirements with target 3. Each names a combination
  that changes compilation semantics, lineage or grain reasoning, generated data behavior,
  or fault applicability; superficial combinations stay covered by their atomic rows.
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

## Scenario claims

| File | Requirement IDs |
| --- | --- |
