# Contributing

Thanks for helping improve `staged-workflow-runner`.

## Development Setup

Use Python 3.10 or newer.

```bash
python -m unittest discover -s automation/tests -p 'test_*.py'
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

Optional checks:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest automation/tests -q
scripts/pre-push-secret-scan.sh
```

## Architecture Rules

- Keep the runner one-root: workflow assets, review bundles, supervisor sessions, archives, and run outputs must resolve under the active workspace root.
- Keep task-specific behavior in task packs: prompts, input manifests, tool profiles, schemas, and reviewed handoff bundles.
- Keep the supervisor additive. Do not move low-level Responses API submission, refresh/resume, artifact finalization, sidecar extraction, or review-bundle validation out of `automation/responses_runner_v2/workflow.py`.
- Preserve the review topology: operator Codex, read-only Codex reviewer, read-only Claude reviewer, deterministic consolidation, then operator selective acceptance with applied-change evidence.

Read `AGENTS.md` before making architectural changes.

## Pull Request Expectations

- Explain the task and the affected runner contract.
- Add or update focused tests for behavior changes.
- Run the baseline test suite and at least one dry-run smoke.
- Do not commit `.env`, `.local/`, response artifacts, supervisor sessions, caches, or project-specific handoffs.
- Keep generated run evidence out of the public tree unless it is intentionally part of a small synthetic fixture.
