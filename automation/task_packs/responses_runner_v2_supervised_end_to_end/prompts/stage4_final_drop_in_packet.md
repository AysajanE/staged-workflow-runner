Produce the Stage 4 final hardened drop-in packet.

Use the approved Stage 1 architecture, approved Stage 2 package contract, approved Stage 3 draft packet, and Stage 3 reviewer notes as controlling authority.

This is the terminal full-package emission stage.

Do not reopen approved architecture or package inventory unless the Stage 3 review explicitly required it.

Return exactly these sections:

## 1. Final Package Summary

State:

- what the final package implements;
- whether any approved Stage 2 or Stage 3 contract item changed;
- why the packet is directly materializable.

## 2. Final File Inventory

Use this exact table:

| path | action | category | purpose |

Rules:

- Must include root `AGENTS.md`.
- Must include every file emitted in Section 3.
- Must not include extra files not emitted in Section 3.

## 3. Final Drop-In Files

For every changed file, use this exact structure:

### File: `<repo-relative path>`

- action: `create` or `update`
- why required: `<brief rationale>`

````<language>
<complete final file contents or exact apply-ready patch>
`````

Rules:

* Complete final files are preferred.
* Exact patches are allowed only for existing large files where a patch is safer.
* No placeholders.
* No TODOs.
* No hidden dependencies.
* Every prompt file must be complete.
* Every test file must be complete.
* Every schema file must be complete.
* Model migration must be complete.

## 4. Final Review-Agent Protocol

Use this exact table:

| review_step | actor | command_or_method | input_artifacts | output_artifacts | acceptance_rule |

Include operator Codex, Codex review agent, Claude review agent, consolidation, and selective acceptance.

## 5. Final Failure And Recovery Policy

Use this exact table:

| case_id | detection_signal | action | human_pause | tested_by |

## 6. Final Validation And Acceptance Checks

Use this exact table:

| phase | check_id | command_or_method | expected_result | acceptance_reason |

Rules:

* `phase` must be `red` or `green`.
* Include all checks required by approved Stage 2 and Stage 3.
* Include model migration checks.
* Include dry-run/smoke checks.

## 7. Rollout Instructions

Provide exact commands and sequencing for applying and validating the packet.

## 8. Human Pause And Escalation Conditions

Use this exact table:

| condition | detection_signal | artifact_to_present | human_decision_required |

## 9. Residual Risks

If none remain, write `None.`

Quality bar:

* The packet must be materializable verbatim.
* The file inventory and emitted files must match exactly.
* The final implementation must not require another design pass.

