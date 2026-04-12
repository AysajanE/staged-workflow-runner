Produce the stage-two locked draft package contract for this repository.

This is the draft contract-and-boundary stage.
Its job is to convert the approved stage-one architecture into the exact package contract that stage 3 must complete and harden into the final integrated supervisory lane with minimum change.

Use the approved stage-one review bundle and reviewer notes as the controlling authority above the attached repository files.
Do not reopen approved architecture unless the reviewer explicitly reopened it.

Non-negotiable rules:

- Lock the package contract, do not merely describe it.
- Treat the approved stage-one architecture and the primary job inputs as the controlling source for target-system requirements.
- Do not restate prompt text as if it were the source of target-system policy.
- Do not add, remove, tighten, or relax target-system requirements unless the approved architecture or reviewer notes support that change.
- Use web search in this stage only when current official guidance or current technical docs could materially change an implementation contract, a validation command, or a boundary-locking file you need to lock now.
- The current meta-run is still manually reviewed between stages. If the target design needs review templates, checklists, or similar artifacts, create them only as part of the future package you are drafting, not as commentary about this current run.
- Include a root `AGENTS.md`.
- Include the minimum documentation and tests required for safe adoption.
- For tests and validation, follow the controlling red/green TDD requirement: specify test-first checks, make the red phase fail against the pre-change state, then make the green phase pass with the drafted package.
- Only include files that truly need to change.
- Lock the exact file inventory and per-file implementation contract for every changed file.
- Emit complete final contents only for the smallest non-empty subset of boundary-locking files whose exact wording or semantics must be fixed now to keep stage 3 from drifting.
- Do not emit the full package in this stage.
- Use exact repo-relative paths.
- Use quadruple-backtick fences for any full file contents emitted in this stage.
- Do not include TODOs, placeholders, or unresolved text inside boundary-locking full file contents.
- Treat the approved reviewer-note "must address first" items as the first candidates for early locking.
- The approved stage-two draft inventory, per-file contracts, boundary-locking files, and validation matrix become controlling authority for stage 3 unless the stage-two review explicitly reopens them.

Return these sections in this exact order:

## 1. Locked Package Summary

State:

- the approved supervisory-lane architecture in one paragraph
- the approved minimum-change strategy in one paragraph
- the exact stage-2/stage-3 boundary in one paragraph
- the exact failure-handling posture stage 3 must preserve

## 2. Draft File Inventory

Use this exact table:

| path | action | category | purpose | stage3_obligation |

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
- `stage3_obligation` must state exactly what stage 3 has to preserve, complete, or tighten for that file.
- Include only files covered by the per-file implementation contracts below.

## 3. File Implementation Contracts

Use this exact table:

| path | required_behavior | must_include | dependencies_or_interfaces | stage3_completion_rule |

Rules:

- Include one row for every file in the inventory.
- `must_include` should name the exact sections, behaviors, CLI flags, schema fields, tables, or workflow surfaces that the final file must contain.
- `dependencies_or_interfaces` should name the concrete repo surfaces, schemas, tests, or handoff artifacts the file must fit.
- `stage3_completion_rule` must make the stage-3 handoff unambiguous.
- The inventory and the contract rows must match exactly.

## 4. Boundary-Locking Draft Files

For every boundary-locking file, use this exact structure:

### File: `<repo-relative path>`

- action: `create` or `update`
- why required now: `<brief rationale>`
- stage-3 rule: `<what stage 3 must preserve or may tighten>`

````<language>
<complete final file contents>
````

Rules:

- Order files by path.
- Use an accurate language tag when possible.
- Emit complete final contents only for the smallest subset of files whose exact contents must be locked now to prevent stage-3 drift.
- Every file in this section must also appear in the inventory and in the file implementation contracts.
- If a file can be safely specified through section 3 without exact early locking, do not emit it here.
- Do not emit complete contents for the whole package in this stage.

## 5. Validation And Test Plan

Use this exact table:

| phase | check_id | command_or_method | expected_result | why_it_matters |

Rules:

- Include the exact validation steps the team should run against this draft.
- `phase` must be `red` or `green`.
- Order rows so each `red` check appears before its corresponding `green` check.
- A `red` row must fail against the pre-change state and must exercise the new or changed behavior.
- A `green` row must pass after the draft package is applied.
- Do not count a test that already passes before the change as valid red-phase evidence.
- This matrix becomes the controlling validation baseline for stage 3 unless the stage-two review explicitly reopens it.
- Cover at least:
  - unit tests
  - workflow dry run or smoke path
  - any recovery-path validation that the package depends on

## 6. Reviewer Focus

Use this exact table:

| focus_area | why_high_risk | what_to_audit |

Rules:

- Focus on the parts of the draft most likely to hide correctness, completeness, or minimum-change defects.
- Do not use this section as a substitute for fixing obvious problems in the draft itself.

## 7. Final Stage Charter

Format this section exactly as:

- `Preserve:`
  Then a flat bullet list.
- `Tighten first:`
  Then a flat bullet list.
- `Do not reopen:`
  Then a flat bullet list.

Quality bar:

- This should read like a real draft implementation contract, not an architecture memo.
- Do not collapse into broad advice.
- Do not omit tests or runbook changes if the package depends on them.
- Do not weaken the package by pushing unresolved file-contract decisions to stage 3.
- Make stage 3 valuable by leaving it real hardening work:
  - completing the remaining full file contents
  - consistency repair
  - validation tightening
  - file-set pruning
  - wording normalization
  - human-pause condition tightening
  - acceptance-check refinement
