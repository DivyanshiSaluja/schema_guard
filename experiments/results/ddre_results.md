# DDRE measured results

Rename confirmation: RENAME full_name -> name, match ratio 1.00 on 50 probe rows.
Ambiguity case: AMBIGUOUS for full_name, candidates ['display_name', 'name'].

| Scenario | Candidate | Defect | Committed fixture | Existing checks on changed rows | DDRE verdict | Witness | Shared with reference (input data) | ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A. Rename (504 rows) | C1 | correct | reject | pass | EQUIVALENT: deployable |  | non_empty(name) on keys [504] | 2.1 |
| A. Rename (504 rows) | C0 | stale: the committed repair | pass | reject | DIVERGENT: rejected | 1 row(s), key [1], ('*', 'error'); read only by candidate ['full_name'], only by reference ['email', 'name'] |  | 18.4 |
| A. Rename (504 rows) | C2 | reads the wrong column | pass | pass | DIVERGENT: rejected | 1 row(s), key [1], ('name', 'value'); read only by candidate [], only by reference ['name'] |  | 4.0 |
| A. Rename (504 rows) | C3 | turns empty names into NULL | reject | reject | DIVERGENT: rejected | 1 row(s), key [504], ('name', 'value') | non_empty(name) on keys [504] | 1.6 |
| A. Rename (504 rows) | C4 | blanks names over 40 chars | reject | pass | DIVERGENT; invariant non_empty(name): rejected | 1 row(s), key [502], ('name', 'value') | non_empty(name) on keys [504] | 2.0 |
| A'. Rename, seed rows only (500 rows) | C3 | turns empty names into NULL | - | pass | EQUIVALENT: deployable |  |  | 1.5 |
| A'. Rename, seed rows only (500 rows) | C4 | blanks names over 40 chars | - | pass | EQUIVALENT: deployable |  |  | 4.8 |
| B. Add column (504 rows) | A1 | incumbent unchanged | - | pass | EQUIVALENT: deployable |  | non_empty(name) on keys [504] | 1.5 |
| B. Add column (504 rows) | A2 | writes phone into email | - | pass | DIVERGENT: rejected | 1 row(s), key [1], ('email', 'value'); read only by candidate ['phone'], only by reference ['email'] | non_empty(name) on keys [504] | 1.9 |
| B. Add column (504 rows) | A3 | adds an output field | - | pass | DIVERGENT: rejected | 1 row(s), key [1], ('phone', 'extra'); read only by candidate ['phone'], only by reference [] | non_empty(name) on keys [504] | 1.7 |
| C. Retype id (504 rows) | R1 | casts back to integer | - | pass | EQUIVALENT: deployable |  | non_empty(name) on keys ['504'] | 1.7 |
| C. Retype id (504 rows) | R2 | keeps text id | - | reject | DIVERGENT: rejected | 1 row(s), key ['1'], ('id', 'value') | non_empty(name) on keys ['504'] | 1.8 |
| D. Drop column (504 rows) | D1 | writes NULL email | - | reject | UNVERIFIABLE; invariant not_null(email): rejected | 1 row(s), key [1], ('email', 'not_null'); read only by candidate [], only by reference ['email'] | non_empty(name) on keys [504] | 1.5 |
| D. Drop column (504 rows) | D2 | writes a placeholder email | - | pass | UNVERIFIABLE ['email']: waiver needed |  | non_empty(name) on keys [504] | 1.5 |
| E. Ambiguous rename (504 rows) | C1 | correct if name is the rename | - | pass | UNVERIFIABLE; invariant non_empty(name): rejected | 1 row(s), key [504], ('name', 'non_empty'); read only by candidate ['name'], only by reference ['full_name'] |  | 1.6 |

| Post-deployment case | Action | Quarantined keys | Detail |
| --- | --- | --- | --- |
| C1 deployed; 3 upstream rows arrive with NULL name | QUARANTINE | [11, 12, 13] | non_empty(name): UPSTREAM_INDUCED |
| C4 deployed; 5 rows with names over 40 chars | ACTIVATE_REFERENCE | [] | non_empty(name): CANDIDATE_INDUCED |
| Buggy incumbent (health check flags ['non_empty(name)']); same upstream batch | HALT_FIELD | [] | non_empty(name): INDETERMINATE |

Restoring the pre-drift snapshot on the C4 batch: 200 of 200 rows fail.

| Freshness check | Deploy allowed | Reason |
| --- | --- | --- |
| Same code, reformatted | yes |  |
| Different code (C2) written instead | no | artifact differs from the approved candidate |
| Second drift after approval (email renamed) | no | live source schema changed after approval |
