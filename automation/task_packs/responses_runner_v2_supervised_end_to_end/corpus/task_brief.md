# Primary Task Brief

## Task

Design a complete task-pack scaffold that will be used by `responses_runner_v2` to generate a final drop-in-ready implementation packet for the next-generation end-to-end supervisory lane.

The runner is improving itself.

## Required Final Capability

The final implementation packet produced by this workflow must make the staged workflow runner capable of:

1. receiving a high-stakes task after an initial clarification gate;
2. generating or improving a task-specific scaffold;
3. independently reviewing that scaffold before any costly stage execution;
4. launching stages;
5. monitoring long-running remote Responses API stages;
6. retrieving terminal artifacts;
7. reviewing intermediate stage outputs substantively;
8. invoking independent non-interactive review agents:
   - Codex review agent using `codex exec` as the canonical current command, with local compatibility for `codex -exec` if the team's wrapper supports it;
   - Claude review agent using `claude -p`;
9. consolidating independent reviews;
10. allowing the operator Codex agent to accept only supported reviewer recommendations;
11. preparing reviewer notes and approved review bundles for the next stage;
12. repeating the process until the terminal stage;
13. preparing the final approved implementation bundle.

## Human Participation Model

The only mandatory human interaction in the future lane should be the initial clarification gate.

After that gate, normal execution should be AI-operated.

Human pauses after the initial gate are exception paths only and must specify:

- exact trigger;
- artifact to present;
- decision required;
- safe continuation action.

## Stage-Economics Rule

Every paid stage must perform critical, non-trivial work.

Do not create a stage that only renames, formats, or lightly summarizes another stage.

## Review Quality Rule

The future review loop must preserve or exceed the current manual quality bar.

Automated reviews must evaluate:

- scaffold quality;
- prompt specificity;
- input manifest signal quality;
- attached context quality;
- model and tool settings;
- stage structure;
- stage-output quality;
- completeness;
- substantive soundness;
- next-stage readiness.

## Model Migration Rule

The final packet must update model configuration from `gpt-5.4` / `gpt-5.4-pro` to GPT-5.5 family models wherever appropriate.

At minimum, this must address:

- engine defaults;
- model caps;
- workflow manifests;
- example packs;
- self-improvement packs;
- tests;
- docs and runbooks;
- generated supervisor internal workflows.

## Output Requirement

The final stage of this workflow must output a full implementation packet.

For new files, it must provide complete file contents.

For existing files, it must provide complete replacement content or exact apply-ready patches.

The team must be able to materialize the packet without reinterpretation.
