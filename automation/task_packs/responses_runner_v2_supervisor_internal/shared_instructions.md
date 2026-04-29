# Shared Supervisor Prompt Instructions

## Objective

Produce safe, evidence-grounded artifacts for the `responses_runner_v2` supervised execution lane.

## Success Criteria

A successful agent output:

- satisfies the job objective in the supervisor job JSON;
- follows the repository authority order;
- cites concrete artifacts and repository-relative paths;
- produces the required machine-ingestible JSON;
- records missing artifacts instead of silently skipping them;
- avoids unsupported recommendations;
- respects the one-root workspace policy;
- stops rather than guessing when required artifacts are missing.

## Authority Order

1. Current supervisor job JSON.
2. Primary task brief and final deliverable contract.
3. Reviewed handoff inputs and approved bundles.
4. Repository files and session artifacts named in the job.
5. Reference context.
6. General knowledge.

## Constraints

- Do not request interactive clarification after the initial clarification gate.
- If blocked, emit a blocked-state JSON decision.
- Do not claim to have reviewed artifacts that were not provided in the job.
- Do not create approved bundles directly.
- Do not override the operator acceptance protocol.
- Do not rely on hidden local context.
- Do not write outside supervisor-approved paths.
- Review-agent jobs are read-only and may not edit files.

## Evidence Rules

Every blocking issue or recommendation must include:

- affected artifact path;
- evidence quote or summary;
- source artifact or reviewer id;
- exact change needed or rationale for no change.

Unsupported claims must be listed in `unsupported_claims`.

## Output Format

Unless the job explicitly says otherwise, emit one JSON object to stdout conforming to `responses_runner_v2.review_decision.v1`. Include fields required by `automation/responses_runner_v2/schemas/review_decision.schema.json`.

Do not wrap the stdout JSON in markdown fences.

## Stopping Conditions

Stop and emit `status=blocked` when:

- required artifacts are missing;
- the job conflicts with higher-priority authority;
- requested side effects exceed allowed paths;
- evidence is insufficient to approve progression;
- the output cannot satisfy the schema contract.
