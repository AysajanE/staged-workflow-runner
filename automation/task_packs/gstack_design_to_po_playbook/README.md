# Gstack Design To PO Playbook Pack

This task pack is the high-stakes lane for turning reviewed gstack planning material into a `markdown_playbook_v1` draft for plan-orchestrator.

It is intentionally not an execution wrapper. The terminal artifact is a playbook draft that still requires:

1. plan-orchestrator `list-items` against the emitted playbook;
2. plan-orchestrator `doctor --playbook` against the emitted playbook;
3. human review before any plan-orchestrator execution command.

Use this pack when a deterministic compiler scaffold is not enough because the plan affects security, money, compliance, irreversible writes, production data, or customer-facing claims.

## Inputs

Supply one to four primary job inputs at runtime, normally:

- the approved gstack design doc;
- the reviewed autoplan output;
- the approved build brief;
- optional repo-specific constraints or acceptance notes.

All input files must live under the active SWR root. If the target workspace is different from this repository, stage or copy this pack into that target workspace first; the runner intentionally enforces one exact workspace root.

## Workflow

The five stages are:

1. `source_authority_map` - lock source hierarchy, ambiguity list, and non-goals.
2. `repo_grounding` - map repo surfaces, likely write roots, tests, and package commands.
3. `execution_row_draft` - draft candidate PO rows with narrow reads/writes and verification.
4. `gate_and_contract_review` - harden gates, path safety, prerequisites, and contract compliance.
5. `final_markdown_playbook` - emit only the final `markdown_playbook_v1` document.

Stages 1 through 4 require review bundles. Stage 5 is terminal.

## Dry Run

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/gstack/<approved-design-or-brief>.md \
  --dry-run
```

## Post-Output Verification

After Stage 5, save the terminal artifact under `docs/playbooks/<slug>.playbook.md` in the target repo and run:

```bash
python $KEEL_ROOT/tools/plan-orchestrator/automation/run_plan_orchestrator.py list-items \
  --playbook docs/playbooks/<slug>.playbook.md \
  --format json

python $KEEL_ROOT/tools/plan-orchestrator/automation/run_plan_orchestrator.py doctor \
  --playbook docs/playbooks/<slug>.playbook.md \
  --format json
```

Do not treat SWR completion as plan-orchestrator execution approval.
