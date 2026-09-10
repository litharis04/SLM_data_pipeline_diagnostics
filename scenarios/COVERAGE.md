# Scenario Corpus Coverage Plan

Baseline plan slice T01: domain families, scenario sizes, scalar types, raw generators,
column properties, and key roles. Later slices (T02–T04) append relationship, staging,
intermediate-model, expression, topology, output, assertion, and interaction rows after the
existing rows; row order is significant because it breaks selection ties and MUST NOT be
changed during an authoring run.

Conventions:

- `F-` rows are individual feature or boundary requirements with target 5.
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

## Scenario claims

| File | Requirement IDs |
| --- | --- |
