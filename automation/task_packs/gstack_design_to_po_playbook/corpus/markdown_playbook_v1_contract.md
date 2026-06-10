# Markdown Playbook V1 Contract Summary

The terminal artifact must be a single markdown document compatible with plan-orchestrator's `markdown_playbook_v1` parser.

Required sections:

1. title and brief context;
2. `## 1. Phase Overview`;
3. `## 2. Execution Items`;
4. `## 3. Phase Details`;
5. `## 4. Shared Guidance`;
6. `## 5. Risks And Contingencies`;
7. `## 6. Immediate Next Actions`.

The execution table must contain these columns:

| step_id | phase | action | why_now | owner_type | prerequisites | repo_surfaces | deliverable | exit_criteria | allowed_write_roots | requires_red_green | required_verification_commands |

Rules:

- `step_id` values are zero-padded sequential integers beginning at `01`.
- `prerequisites` is `none`, explicit step ids, or a clear range.
- `repo_surfaces`, `deliverable`, and `allowed_write_roots` use repo-relative paths.
- `allowed_write_roots` must be narrower than the repository root and should usually match the deliverable subtree.
- Behavioral changes set `requires_red_green=true` and include concrete verification in the `required_verification_commands` cell. Phase details may repeat or explain commands, but they do not substitute for the table cell.
- Docs-only rows can set `requires_red_green=false` only when `required_verification_commands` and exit criteria contain concrete reviewable evidence or content checks.
- Manual gates must be explicit in phase details for rows involving secrets, external dependencies, ambiguous source authority, irreversible operations, or production/customer risk.
- Never add plan-orchestrator derived columns.
- If a target repository policy, autonomous brief, or validator requires additional literal gate terms, include those exact terms in the playbook narrative and gate details.
