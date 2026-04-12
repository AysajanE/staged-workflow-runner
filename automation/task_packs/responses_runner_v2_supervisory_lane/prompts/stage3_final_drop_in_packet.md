Produce the final hardened drop-in packet for this repository.

This is the final hardening stage.
Its job is to take the approved stage-two draft contract, reconcile approved review findings, eliminate inconsistencies, fill in the remaining full file contents, and emit the final package that the team can apply directly without reinterpretation.

Use the approved stage-two review bundle, the stage-two reviewer notes, the earlier approved architecture, and the approved stage-two draft inventory, per-file implementation contracts, boundary-locking files, and validation matrix as the controlling authority above the attached repository files.
Do not reopen approved architecture unless the review explicitly required a change.

Non-negotiable rules:

- Emit the final package, not a memo about a future package.
- This is the only full-package emission stage.
- Treat the approved architecture, approved reviewer notes, and primary job inputs as the controlling source for target-system requirements.
- Do not restate prompt text as if it were the source of target-system policy.
- Do not add, remove, tighten, or relax target-system requirements unless the approved architecture or review findings support that change.
- Preserve the approved stage-two file inventory, per-file implementation contracts, boundary-locking full files, and validation matrix unless the stage-two review explicitly reopened them.
- Any change to a stage-two file path, file action, or locked boundary file must be justified by approved reviewer notes, not by convenience.
- Only include files that truly need to change.
- Use web search in this stage only when current official guidance or current technical docs could materially improve final hardening, validation, or the currentness of a claim that affects the preserved draft contract.
- The current meta-run is still manually reviewed between stages. Any review templates, checklists, or reviewer-note artifacts that appear in the final package must belong to the future supervisory lane being implemented, not to this current scaffold.
- For tests and validation, preserve the controlling red/green TDD requirement from the primary job inputs and approved architecture.
- Every changed file must be emitted as a complete final file, not a diff or fragment.
- Use exact repo-relative paths.
- Use quadruple-backtick fences for all full file contents.
- Do not leave TODOs, placeholders, or unresolved text inside final file contents.
- Ensure code, docs, tests, and `AGENTS.md` are internally consistent.

Return these sections in this exact order:

## 1. Final Package Summary

State the final package in one concise but complete section.
Also state whether any approved stage-two inventory or file-contract item changed under explicit review authority.

## 2. Final File Inventory

Use this exact table:

| path | action | category | purpose |

Rules:

- `path` must be an exact repo-relative path.
- `action` must be `create` or `update`.
- `category` must be one of:
  - `engine`
  - `task_pack`
  - `docs`
  - `tests`
  - `config`
  - `ops`
- Include the root `AGENTS.md`.
- Include only the files that appear in the final package below.
- Every row must correspond to a file already present in the approved stage-two draft inventory unless the stage-two review explicitly reopened the file set.

## 3. Final Drop-In Files

For every changed file, use this exact structure:

### File: `<repo-relative path>`

- action: `create` or `update`
- why required: `<brief rationale>`

````<language>
<complete final file contents>
````

Rules:

- Order files by path.
- Use an accurate language tag when possible.
- Emit complete final contents for every file in the inventory.
- Do not include any extra file outside the inventory.
- The file inventory and emitted file blocks must match exactly.
- For any file that stage 2 emitted as a boundary-locking full file, preserve that approved content unless reviewer notes explicitly required a change or a narrow consistency fix is necessary to keep the package coherent.
- Do not use stage 3 to redesign a file that stage 2 already locked.

## 4. Final Validation And Acceptance Checks

Use this exact table:

| phase | check_id | command_or_method | expected_result | acceptance_reason |

Rules:

- Include only the checks that should pass before the package is considered ready to apply.
- `phase` must be `red` or `green`.
- Order rows so each `red` check appears before its corresponding `green` check.
- A `red` row must fail against the pre-change state and must exercise the new or changed behavior.
- A `green` row must pass after the final package is applied.
- Do not count a test that already passes before the change as valid red-phase evidence.
- Preserve the approved stage-two validation matrix as the baseline and only tighten or clarify it where final hardening or approved review requires it.
- Cover the exact tests, dry runs, and smoke paths that matter.
- Make the acceptance reason concrete.

## 5. Rollout And Safe-Adoption Notes

Keep this section practical and minimal.
List only the sequencing or rollout notes the team actually needs.

## 6. Human Pause And Escalation Conditions

Use this exact table:

| condition | detection_signal | artifact_to_present | human_decision_required |

Rules:

- Include only real exception paths.
- Do not smuggle routine human review into this table.
- Make the detection signal operational and specific.

## 7. Residual Risks

List only what still remains unresolved after final hardening.
If nothing remains, say `None.`

Quality bar:

- The result must be directly applicable to this repository.
- The result must not require reinterpretation or hidden follow-up design work.
- The file set must be as small as possible while still complete.
- The result must preserve the approved stage-two draft contract unless review explicitly reopened a part of it.
- The final package must survive a serious review for:
  - internal consistency
  - minimum-change discipline
  - grounded current-state claims
  - explicit failure handling
  - explicit human-pause conditions
  - validation completeness
