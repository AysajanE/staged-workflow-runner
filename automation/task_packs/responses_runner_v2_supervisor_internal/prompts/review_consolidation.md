# Review Consolidation Policy

## Objective

Merge independent operator, Codex review-agent, and Claude review-agent findings into one provenance-preserving consolidation report.

## Inputs

- operator provisional review, when available;
- Codex review-agent JSON and markdown report;
- Claude review-agent JSON and markdown report;
- artifact hash manifest;
- provisional bundle or final packet under review;
- review decision schema.

## Classification Taxonomy

Each recommendation must be classified as exactly one of:

- `accepted_for_operator_review` — supported enough for the operator to evaluate;
- `rejected` — contradicted by evidence or invalid;
- `needs_operator_judgment` — evidence is incomplete, conflicting, or requires accountable judgment;
- `duplicate` — substantially same as another recommendation;
- `already_satisfied` — target artifact already satisfies the recommendation;
- `out_of_scope` — not authorized by task brief or approved handoffs.

## Evidence Preservation

Every consolidated item must preserve:

- source agent;
- source recommendation id;
- affected artifacts;
- evidence;
- exact change requested;
- severity.

Do not merge away a blocking severity merely because only one reviewer raised it.

## Duplicate Handling

When duplicate recommendations exist:

1. retain the most precise version as the primary item;
2. mark the others `duplicate`;
3. list all source agents in the duplicate map.

## Separation From Acceptance

Consolidation is advisory. It must never set `operator_decision`, create an approved bundle, or mark the packet finally accepted.

Only a later operator Codex acceptance artifact may accept or reject recommendations.

## Output Format

Emit consolidation JSON conforming to `responses_runner_v2.review_decision.v1` with:

- `actor_role`: `consolidation_pass`;
- `review_kind`: `consolidation`;
- `recommendations[].consolidation_recommendation`;
- no `recommendations[].operator_decision`.

Also emit a markdown summary listing blocker count, recommendation count, duplicate map, and unresolved conflicts.

## Stopping Conditions

Stop with a consolidation failure when:

- either required independent review JSON is missing;
- review JSON is malformed or schema-invalid;
- reviewer artifact paths do not match expected job paths;
- consolidation would require editing source artifacts;
- recommendations cannot be traced back to source evidence.
