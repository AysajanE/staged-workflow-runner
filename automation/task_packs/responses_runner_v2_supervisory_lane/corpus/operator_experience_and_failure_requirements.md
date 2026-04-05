# Operator Experience And Failure Requirements

## Current Manual Workflow That Must Be Automated

The current operator workflow around the runner typically looks like this:

1. A human delegator provides the task.
2. The operator asks clarification questions until the task is precise enough.
3. The operator creates the task scaffold for the staged runner.
4. A different reviewer audits that scaffold before any costly run begins.
5. Stage 1 is launched.
6. While the stage is running, an operator periodically checks the live remote status to confirm that the run is healthy.
7. When the stage finishes, the output is reviewed for:
   - quality
   - comprehensiveness
   - completeness
   - substantive soundness of the actual task output
8. The reviewer prepares reviewer notes and an approved review bundle for the next stage.
9. The same pattern repeats until the final stage.
10. After the final stage, the output is reviewed again and the final approved bundle is prepared for implementation.

The new supervisory lane must automate this operating pattern as much as possible while preserving quality.

## Stage Review Expectations

The substantive part of an intermediate stage output should be reviewed carefully before approval.

The current human process is to read the substantive content multiple times before approval. The new lane should preserve that quality bar in automated form and should define what constitutes enough evidence to approve a stage.

Reviewer notes must clearly state:

- what is approved
- what is not approved
- what the next stage must address first

## Observed Status Outcomes

Observed terminal or near-terminal outcomes have been:

- `completed`
- `failed`
- `incomplete`

`completed` is the ideal case.

`incomplete` has been rare and was observed when the output exceeded the max output token limit.

The challenging class is `failed`.

## Observed Failure Types

### 1. Rate-Limit Failure With Practically Complete Output

One real pattern has been a terminal `failed` status caused by rate limiting where the assistant still produced the complete required markdown artifact in the assistant message.

Operationally:

- the nominal API status is `failed`
- the substantive artifact is still present and complete
- the operator can retrieve the markdown artifact from the assistant message and continue

The future supervisory lane must distinguish this case from a true unrecoverable failure. If the assistant message or equivalent substantive artifact is complete, the lane should treat it as practically retrievable rather than automatically discarding it.

### 2. Legitimate Server-Side Failure

Another real pattern has been a legitimate server-side failure where rerunning the exact same stage as-is, without changing the scaffold, is usually the correct recovery action and often succeeds.

The future supervisory lane must explicitly support this recovery path.

## Required Recovery Distinction

The future supervisory lane must distinguish at least these cases:

- `failed` with complete substantive assistant artifact
- `failed` with empty or insufficient substantive artifact
- `incomplete` caused by output-limit exhaustion
- `completed`

If a `failed` run has no substantive assistant artifact, treat it as a true failure.

If a `failed` run has a complete substantive assistant artifact, treat it as a practically retrievable output path and integrate that behavior into the supervision model.
