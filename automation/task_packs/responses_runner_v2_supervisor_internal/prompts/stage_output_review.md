# Stage Output Review Prompt

## Objective

Review a completed or failed-with-complete-artifact stage output and its provisional next-stage bundle for safe progression.

## Reviewed Artifacts

Use the job JSON to locate:

- stage `response.final.md`;
- stage `response.final.json`;
- optional structured output;
- optional sidecar response artifacts;
- `run_manifest.json`;
- `stage_checkpoint.json`;
- `input_manifest.md` and `input_manifest.json`;
- `request_payload.json`;
- monitoring log;
- outcome classification;
- provisional reviewer notes;
- provisional approved handoff;
- provisional review bundle.

## Completeness Checklist

Verify:

- required sections are present;
- stage objective is answered;
- assistant artifact is substantive;
- facts are supported by attached evidence;
- failure classification is correct;
- output-limit incomplete is not advanced;
- failed-no-artifact is not advanced;
- next-stage bundle contains correct artifact paths and hashes.

## Failure Classification

Respect these classes:

- `completed_complete_artifact` is reviewable;
- `failed_complete_artifact` is reviewable;
- `failed_no_artifact` requires archive before rerun;
- `incomplete_output_limit` blocks progression;
- `blocked_token_preflight` blocks progression;
- `long_running_monitoring_anomaly` must not duplicate-submit.

## Bundle Validation

A provisional review bundle is safe only if:

- source workflow/run/stage ids match;
- primary markdown and response JSON paths match recorded stage artifacts;
- hashes match;
- reviewer notes reflect accepted findings;
- approved handoff does not misrepresent the stage output.

## Downstream Readiness

Assess whether the next stage would receive:

- high-signal reviewed handoff;
- correct authority ordering;
- enough context;
- no unsupported claims;
- clear open dependencies.

## Output Contract

Emit JSON conforming to `responses_runner_v2.review_decision.v1`. Include approval decision, blockers, improvements, recommendations, evidence, missing artifacts, unsupported claims, and next action.

## Stopping Conditions

Do not approve progression when:

- output is incomplete due to output limit;
- failed artifact lacks substantive complete content;
- bundle hashes or paths do not match;
- reviewer notes contain unsupported claims;
- blocking issues remain unresolved;
- review-agent JSON is missing or invalid.
