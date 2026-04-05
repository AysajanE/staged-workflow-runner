# Final Deliverable Contract

## Final Output Form

The final stage of this workflow must output a drop-in-ready package for this repository.

That package must be directly applicable without the team needing to reinterpret the design, infer omitted details, or rewrite partial drafts.

## Required Characteristics

The final package must:

- target this repository directly
- minimize repo churn
- reuse existing runner contracts wherever possible
- specify exact repo-relative file paths
- specify whether each file is created or updated
- provide complete final file contents for every changed file
- include a root `AGENTS.md`
- include tests needed to validate the new lane
- include documentation or runbook updates required to operate the new lane safely

## Minimum-Change Rule

Do not create broad new subsystems if the capability can be achieved by a smaller coherent layer on top of the current engine.

If a file does not need to change, do not include it in the package.

## Failure-Handling Rule

The final package must explicitly encode behavior for:

- practically retrievable `failed` runs that still contain a complete substantive artifact
- legitimate server-side failures that should trigger rerun-as-is
- rare `incomplete` outcomes caused by output limits

## Testing Rule

The final package must express validation for the new lane using red/green TDD.

That means:

- define the automated tests for each new or changed behavior before implementation
- explicitly identify the red phase where those tests are expected to fail against the pre-change state
- explicitly identify the green phase where the corresponding tests are expected to pass after the package is applied
- avoid counting already-passing or non-exercising tests as valid red-phase evidence

## Final-Stage Formatting Rule

When the workflow asks for exact file contents, every file must be emitted as a complete final file, not as a patch fragment.

To keep markdown safe even when files themselves contain fenced code blocks, full file contents should be wrapped in quadruple-backtick fences with an appropriate info string.

## Unresolved Issues

If anything remains unresolved by the final stage, isolate it under a clearly labeled residual-risk or open-question section.

Do not hide uncertainty inside supposed final file contents.
