# Primary Brief

## Task

Use the current staged workflow runner to design the integrated automation/supervisory lane that this repository should gain next.

The runner is being used on the task of improving itself.

For this current meta-run, stage reviews remain manual.
Do not confuse this current manual review process with the future supervisory lane being designed.

## Required Outcome

The final output of this workflow must be a drop-in-ready package for this repository that the team can apply directly without reinterpretation or manual rewriting.

The package must define the automation/supervisory lane that will operate the existing runner end to end:

- task intake from an external human delegator
- clarification questions at the beginning until the task is precise enough
- scaffold creation for the task-specific runner pack
- independent scaffold review before costly stage execution
- live monitoring of long-running stages
- substantive review of stage outputs
- reviewer-note preparation
- approved review-bundle preparation
- final approved bundle for implementation

## Human Participation Model

The only mandatory human interaction in the new supervisory lane should be the initial clarification gate with the external delegator.

After that gate, the normal path should be almost fully AI-operated.

Human pauses later in the process should be optional exception paths only. If the new lane needs a human pause, it must specify:

- the exact trigger
- what artifact should be presented
- what decision the human is being asked to make

## Integration Direction

The default target is an integrated solution inside this same repository, not an external operator system in another repository or service.

The final package should reuse the existing runner and its current contracts as much as possible.

Minimum-change integration is a core requirement.

## Quality And Stage-Economics Rules

- The quality of the runner output cannot be compromised.
- Every paid stage must do real, high-stakes, non-trivial work.
- If a stage would only perform a trivial task, the workflow should be compressed instead.
- The future supervisory lane must preserve the high-quality review discipline currently provided manually by human operators.

## Independent Scaffold Review Requirement

The initial task scaffold for any future run must be reviewed by a different independent reviewer before the costly stage execution begins.

That review must cover the full scaffold, including:

- prompts and instructions
- input manifests
- tool and model settings
- corpus and attached context
- stage structure and gates
- output contract

The purpose is to ensure that the scaffold is optimal for the nature and scope of the task before the workflow spends cost.
