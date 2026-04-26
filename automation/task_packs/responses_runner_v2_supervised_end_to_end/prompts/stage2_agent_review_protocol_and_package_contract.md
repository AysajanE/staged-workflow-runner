Produce the Stage 2 agent review protocol and package contract.

Use the approved Stage 1 architecture and reviewer notes as controlling authority.

This stage must lock the exact contract that Stage 3 must implement.

Do not emit the full implementation package yet, except for the smallest subset of boundary-locking prompt or schema files whose exact content must be fixed now to avoid Stage 3 drift.

Return exactly these sections:

## 1. Locked Protocol Summary

Summarize:

- approved architecture
- operator/reviewer/consolidator topology
- model migration posture
- failure recovery posture
- exact Stage 2 to Stage 3 boundary

## 2. Final Package File Inventory

Use this exact table:

| path | action | category | purpose | stage3_obligation |

Categories must be one of:

- `engine`
- `task_pack`
- `docs`
- `tests`
- `config`
- `ops`

Rules:

- Include root `AGENTS.md`.
- Include all required operator/reviewer prompt files.
- Include all required schemas and tests.
- Include only files that Stage 3 must implement.

## 3. File Implementation Contracts

Use this exact table:

| path | required_behavior | must_include | dependencies_or_interfaces | stage3_completion_rule |

There must be exactly one row for every file in Section 2.

## 4. Agent Command Contracts

Use this exact table:

| agent | canonical_command_shape | compatibility_notes | required_inputs | required_outputs | failure_handling |

Include:

- operator Codex
- Codex review agent
- Claude review agent
- consolidation pass

## 5. Prompt Contracts

Use this exact table:

| prompt_artifact | target_agent | purpose | required_sections | output_contract | grounding_rules | non_interactive_constraints |

Include at least:

- operator Codex prompt
- Codex review prompt
- Claude review prompt
- review consolidation prompt
- scaffold author/improver prompt
- stage-output review prompt

## 6. Review Decision Schema Contract

Specify the schema fields the final implementation must include for machine-ingestible review decisions.

Use this exact table:

| field | type | required | meaning | validation_rule |

## 7. Supervisor Session Schema Contract

Specify the session state fields the final implementation must include.

Use this exact table:

| field | type | required | meaning | validation_rule |

## 8. Boundary-Locking Draft Files

Emit full file contents only where exact wording or structure must be preserved into Stage 3.

For each emitted file:

### File: `<repo-relative path>`

- action: `create` or `update`
- why locked now: `<reason>`
- stage3 rule: `<what Stage 3 must preserve or may tighten>`

````<language>
<complete file contents>
`````

If no file truly needs early locking, write `None.`

## 9. Red/Green Validation Baseline

Use this exact table:

| phase | check_id | command_or_method | expected_result | why_it_matters |

Rules:

* `phase` must be `red` or `green`.
* Each red check must fail before implementation.
* Each green check must pass after implementation.
* Include tests for review-agent invocation, consolidation, selective acceptance, failure recovery, model migration, and final packet schema.

## 10. Reviewer Focus For Stage 2

Use this exact table:

| focus_area | why_high_risk | what_reviewer_must_audit |

## 11. Stage 3 Charter

Format exactly:

* `Preserve:`
* `Implement fully:`
* `Do not reopen:`

