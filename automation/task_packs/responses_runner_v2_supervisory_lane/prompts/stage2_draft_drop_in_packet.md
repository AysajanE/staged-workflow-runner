Produce the stage-two draft drop-in packet for this repository.

This is the draft implementation-package stage.
Its job is to convert the approved stage-one architecture into the exact repo file set that would add the integrated supervisory lane with minimum change.

Use the approved stage-one review bundle and reviewer notes as the controlling authority above the attached repository files.
Do not reopen approved architecture unless the reviewer explicitly reopened it.

Non-negotiable rules:

- Draft the package, do not merely describe it.
- Treat the approved stage-one architecture and the primary job inputs as the controlling source for target-system requirements.
- Do not restate prompt text as if it were the source of target-system policy.
- Do not add, remove, tighten, or relax target-system requirements unless the approved architecture or reviewer notes support that change.
- Use web search in this stage when current official guidance or current technical docs could materially improve exact file design, validation steps, or prompt quality.
- The current meta-run is still manually reviewed between stages. If the target design needs review templates, checklists, or similar artifacts, create them only as part of the future package you are drafting, not as commentary about this current run.
- Include a root `AGENTS.md`.
- Include the minimum documentation and tests required for safe adoption.
- For tests and validation, follow the controlling red/green TDD requirement: specify test-first checks, make the red phase fail against the pre-change state, then make the green phase pass with the drafted package.
- Only include files that truly need to change.
- Every changed file must be emitted as a complete final file, not a diff or fragment.
- Use exact repo-relative paths.
- Use quadruple-backtick fences for all full file contents.
- Do not include TODOs, placeholders, or unresolved text inside file contents.

Return these sections in this exact order:

## 1. Locked Package Summary

State:

- the approved supervisory-lane architecture in one paragraph
- the approved minimum-change strategy in one paragraph
- the exact file-set posture stage 3 must preserve
- the exact failure-handling posture stage 3 must preserve

## 2. Draft File Inventory

Use this exact table:

| path | action | category | purpose | why_included_now |

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
- Include only files that appear in the draft package below.

## 3. Draft Drop-In Files

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
- If a file does not need to change, omit it entirely.
- The file inventory and emitted file blocks must match exactly.

## 4. Validation And Test Plan

Use this exact table:

| phase | check_id | command_or_method | expected_result | why_it_matters |

Rules:

- Include the exact validation steps the team should run against this draft.
- `phase` must be `red` or `green`.
- Order rows so each `red` check appears before its corresponding `green` check.
- A `red` row must fail against the pre-change state and must exercise the new or changed behavior.
- A `green` row must pass after the draft package is applied.
- Do not count a test that already passes before the change as valid red-phase evidence.
- Cover at least:
  - unit tests
  - workflow dry run or smoke path
  - any recovery-path validation that the package depends on

## 5. Reviewer Focus

Use this exact table:

| focus_area | why_high_risk | what_to_audit |

Rules:

- Focus on the parts of the draft most likely to hide correctness, completeness, or minimum-change defects.
- Do not use this section as a substitute for fixing obvious problems in the draft itself.

## 6. Final Stage Charter

Format this section exactly as:

- `Preserve:`
  Then a flat bullet list.
- `Tighten first:`
  Then a flat bullet list.
- `Do not reopen:`
  Then a flat bullet list.

Quality bar:

- This should read like a real draft package, not an architecture memo.
- Do not collapse into broad advice.
- Do not omit tests or runbook changes if the package depends on them.
- Do not weaken the package by pushing obvious implementation decisions to stage 3.
- Make stage 3 valuable by leaving it real hardening work:
  - consistency repair
  - validation tightening
  - file-set pruning
  - wording normalization
  - human-pause condition tightening
  - acceptance-check refinement
