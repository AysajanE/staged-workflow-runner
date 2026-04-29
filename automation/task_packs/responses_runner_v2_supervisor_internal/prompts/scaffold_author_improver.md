# Scaffold Author / Improver Prompt

## Objective

Generate or improve a task-specific `responses_runner_v2` scaffold that can be dry-run validated and independently reviewed before any paid stage execution.

## Success Criteria

The scaffold is acceptable only if it includes:

- clear workflow objective;
- material stages where every paid stage performs non-trivial work;
- outcome-first prompts;
- high-signal input manifests;
- appropriate attached repository files and reviewed handoffs;
- GPT-5.5-family model defaults;
- explicit `prompt_cache_retention: "24h"` for committed GPT-5.5-family profiles;
- stage gates aligned with review requirements;
- output schemas where structured extraction is needed;
- dry-run validation evidence;
- launch blockers explicitly listed.

## Scaffold File Requirements

A generated scaffold should include, when applicable:

- workflow manifest;
- shared instructions;
- stage prompts;
- input manifests;
- tool profiles;
- sidecar schemas;
- README/runbook notes.

All paths must remain under the active workspace root.

## Model And Tool Requirements

Use:

- primary generation `gpt-5.5-pro`;
- structural processing `gpt-5.5`;
- `xhigh` reasoning for high-stakes long-running primary stages;
- `high` or `medium` reasoning for structural sidecars;
- `24h` prompt-cache retention for committed GPT-5.5-family model roles.

Tool profiles must be justified by task need.

## Stage-Economics Criteria

Do not create a stage that only renames, reformats, or lightly summarizes another stage. Every paid stage must materially reduce risk or produce essential content.

## Dry-Run Requirements

Before launch approval:

1. run the runner dry-run/load validation;
2. inspect generated request payloads and input manifests;
3. record dry-run command, exit code, artifact paths, and result;
4. fix mechanical failures before review.

## Review-Gating Rules

No live stage may launch until:

- operator provisional scaffold review exists;
- Codex review-agent output exists and validates;
- Claude review-agent output exists and validates;
- consolidation exists;
- operator acceptance accepts only supported recommendations with applied-change evidence.

## Output Format

Emit JSON conforming to `responses_runner_v2.review_decision.v1` for operator scaffold jobs. Include a markdown report path, changed-file list, dry-run result summary, blocking issues, and next action.

## Stopping Conditions

Block when:

- task scope remains ambiguous after clarification;
- required context cannot be attached without changing scope;
- stage economics are weak;
- model/cache settings violate policy;
- dry-run validation fails and cannot be repaired within authorized side effects.
