Produce the Stage 3 draft drop-in packet.

Use the approved Stage 1 architecture, approved Stage 2 package contract, and reviewer notes as controlling authority.

This is a full draft implementation stage.

Emit complete file contents or exact patches for every file in the approved Stage 2 inventory.

Return exactly these sections:

## 1. Draft Package Summary

State what the draft implements and whether it preserves the approved Stage 2 contract.

## 2. Contract Preservation Matrix

Use this exact table:

| stage2_contract_item | preserved_or_changed | justification | affected_files |

If anything changed without reviewer authority, mark it as a defect.

## 3. Draft File Inventory

Use this exact table:

| path | action | category | purpose |

The inventory must match the file blocks in Section 4 exactly.

## 4. Draft Drop-In Files

For every changed file, use this exact structure:

### File: `<repo-relative path>`

- action: `create` or `update`
- why required: `<brief rationale>`

````<language>
<complete final draft file contents or exact apply-ready patch>
`````

Rules:

* Use full replacement files unless a patch is safer for an existing large file.
* If using a patch, it must be complete and apply-ready.
* Do not include TODOs.
* Include all operator/reviewer/consolidation prompts as complete files.
* Include all tests as complete files.
* Include all docs as complete files or exact patches.
* Include model migration changes.

## 5. Draft Review-Agent Prompt Quality Check

Use this exact table:

| prompt_file | outcome_first_contract | evidence_rules | non_interactive_safety | machine_output_contract | remaining_risk |

## 6. Draft Failure-Policy Check

Use this exact table:

| failure_case | implemented_detection | implemented_action | tested_by | remaining_gap |

Include all required failure cases.

## 7. Draft Validation Plan

Use this exact table:

| phase | check_id | command_or_method | expected_result | acceptance_reason |

Rules:

* `phase` must be `red` or `green`.
* Red checks must be real pre-change failures.
* Green checks must be post-change passes.

## 8. Reviewer Notes For Stage 3 Reviewers

Use this exact table:

| issue_area | why_reviewer_should_focus | evidence_to_inspect |

## 9. Stage 4 Charter

Format exactly:

* `Preserve:`
* `Tighten:`
* `Do not reopen:`

