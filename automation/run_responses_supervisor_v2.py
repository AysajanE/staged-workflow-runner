#!/usr/bin/env python3
"""Supervisor CLI for Responses Runner v2.

This entrypoint performs deterministic supervisor state transitions. It does
not duplicate the low-level Responses API submission logic owned by
automation.responses_runner_v2.workflow.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from automation.responses_runner_v2.contracts import load_json, repo_root, resolve_under_root
from automation.responses_runner_v2 import supervisor, telemetry


def _path_argument(value: str) -> Path:
    return Path(value)


def _add_root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=_path_argument,
        help=(
            "Exact workspace root. If omitted, RESPONSES_RUNNER_V2_ROOT is used "
            "when set; otherwise the current working directory is used as-is."
        ),
    )


def _add_session_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", required=True, help="Supervisor session id or session path.")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Operate the Responses Runner v2 supervisor.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init-session", help="Create a supervisor session.")
    _add_root_argument(init_parser)
    init_parser.add_argument("--clarified-task-brief", required=True, type=_path_argument)
    init_parser.add_argument("--summary", required=True)
    init_parser.add_argument("--session-id")

    stage_parser = subparsers.add_parser("stage-scaffold", help="Stage a scaffold version.")
    _add_root_argument(stage_parser)
    _add_session_argument(stage_parser)
    stage_parser.add_argument("--scaffold-path", required=True, type=_path_argument)
    stage_parser.add_argument("--created-by", default="operator_codex")

    examine_parser = subparsers.add_parser(
        "examine-scaffold",
        help="Statically examine a scaffold before executable Stage 1 request construction.",
    )
    _add_root_argument(examine_parser)
    _add_session_argument(examine_parser)
    examine_parser.add_argument("--workflow-file", required=True, type=_path_argument)
    examine_parser.add_argument("--output", type=_path_argument)

    dry_parser = subparsers.add_parser("dry-run-scaffold", help="Dry-run a scaffold workflow.")
    _add_root_argument(dry_parser)
    _add_session_argument(dry_parser)
    dry_parser.add_argument("--workflow-file", required=True, type=_path_argument)
    dry_parser.add_argument("--run-name", default="supervisor-scaffold-dry-run")
    dry_parser.add_argument("--stage")
    dry_parser.add_argument("--primary-job-input", action="append", default=[])
    dry_parser.add_argument("--reference-context", action="append", default=[])
    dry_parser.add_argument("--review-bundle", action="append", default=[])
    dry_parser.add_argument(
        "--input-binding-file",
        type=_path_argument,
        help="Versioned stage-scoped runtime input bindings, identical to a live launch.",
    )

    op_parser = subparsers.add_parser(
        "invoke-operator",
        help="Invoke and record an operator Codex provisional job for a review cycle.",
    )
    _add_root_argument(op_parser)
    _add_session_argument(op_parser)
    op_parser.add_argument("--review-cycle", required=True)
    op_parser.add_argument(
        "--review-kind",
        required=True,
        choices=["scaffold", "stage_output", "final_packet", "recovery"],
    )
    op_parser.add_argument("--job-json", required=True, type=_path_argument)
    op_parser.add_argument("--output-dir", type=_path_argument)

    invoke_parser = subparsers.add_parser(
        "invoke-reviewers",
        help="Invoke Codex and Claude review agents for a review cycle.",
    )
    _add_root_argument(invoke_parser)
    _add_session_argument(invoke_parser)
    invoke_parser.add_argument("--review-cycle", required=True)
    invoke_parser.add_argument(
        "--review-kind",
        required=True,
        choices=["scaffold", "stage_output", "final_packet", "recovery"],
    )
    invoke_parser.add_argument("--job-json", required=True, type=_path_argument)
    invoke_parser.add_argument("--output-dir", type=_path_argument)

    cycle_parser = subparsers.add_parser(
        "review-cycle",
        help="Run operator review, parallel Codex and Claude reviews, and consolidation; never acceptance.",
    )
    _add_root_argument(cycle_parser)
    _add_session_argument(cycle_parser)
    cycle_parser.add_argument("--review-cycle", required=True)
    cycle_parser.add_argument("--review-kind", required=True, choices=["scaffold", "stage_output", "final_packet", "recovery"])
    cycle_parser.add_argument("--job-json", required=True, type=_path_argument)

    revision_parser = subparsers.add_parser(
        "prepare-revision",
        help="Freeze an evidence-supported revision directive from one consolidation.",
    )
    _add_root_argument(revision_parser)
    _add_session_argument(revision_parser)
    revision_parser.add_argument("--review-cycle", required=True)
    revision_parser.add_argument("--accept-recommendation", action="append", required=True)
    revision_parser.add_argument(
        "--reject-recommendation",
        action="append",
        default=[],
        metavar="ID=RATIONALE",
    )
    revision_parser.add_argument("--revised-artifact", action="append", required=True, type=_path_argument)
    revision_parser.add_argument("--revision-scaffold-path", type=_path_argument)

    run_revision_parser = subparsers.add_parser(
        "run-revision",
        help="Run a prepared operator revision and send its outputs through a fresh full review cycle.",
    )
    _add_root_argument(run_revision_parser)
    _add_session_argument(run_revision_parser)
    run_revision_parser.add_argument("--source-review-cycle", required=True)
    run_revision_parser.add_argument("--new-review-cycle", required=True)

    consolidate_parser = subparsers.add_parser("consolidate", help="Consolidate reviews.")
    _add_root_argument(consolidate_parser)
    _add_session_argument(consolidate_parser)
    consolidate_parser.add_argument("--review-cycle", required=True)
    consolidate_parser.add_argument("--codex-review", type=_path_argument)
    consolidate_parser.add_argument("--claude-review", type=_path_argument)
    consolidate_parser.add_argument("--operator-review", type=_path_argument)
    consolidate_parser.add_argument("--output", type=_path_argument)

    accept_parser = subparsers.add_parser("accept", help="Create operator acceptance record.")
    _add_root_argument(accept_parser)
    _add_session_argument(accept_parser)
    accept_parser.add_argument("--review-cycle", required=True)
    accept_parser.add_argument("--consolidated-review", type=_path_argument)
    accept_parser.add_argument("--accept-recommendation", action="append", default=[])
    accept_parser.add_argument(
        "--applied-change-evidence",
        type=_path_argument,
        help=(
            "JSON file containing operator-applied change evidence for accepted recommendations. "
            "Selected recommendations without matching evidence are rejected, not fabricated."
        ),
    )
    accept_parser.add_argument("--blocker-resolution", action="append", type=_path_argument, default=[])
    accept_parser.add_argument("--output", type=_path_argument)

    resolve_parser = subparsers.add_parser("resolve-blocker", help="Record one hash-bound blocker resolution for a review cycle.")
    _add_root_argument(resolve_parser)
    _add_session_argument(resolve_parser)
    resolve_parser.add_argument("--review-cycle", required=True)
    resolve_parser.add_argument("--blocker-id", required=True)
    resolve_parser.add_argument("--resolution", required=True, choices=["resolved", "accepted_risk", "superseded", "still_blocking"])
    resolve_parser.add_argument("--evidence", action="append", required=True)
    resolve_parser.add_argument(
        "--resolution-evidence-json",
        required=True,
        type=_path_argument,
        help="JSON object containing affected_artifacts, applied_changes, validation_evidence, operator_rationale, and optional accepted_risk_rationale.",
    )

    launch_parser = subparsers.add_parser("launch", help="Launch the current accepted scaffold and register its run.")
    _add_root_argument(launch_parser)
    _add_session_argument(launch_parser)
    launch_parser.add_argument("--workflow-file", required=True, type=_path_argument)
    launch_parser.add_argument("--run-name")
    launch_parser.add_argument("--stage")
    launch_parser.add_argument("--primary-job-input", action="append", default=[])
    launch_parser.add_argument("--reference-context", action="append", default=[])
    launch_parser.add_argument("--review-bundle", action="append", default=[])
    launch_parser.add_argument(
        "--input-binding-file",
        type=_path_argument,
        help="Versioned stage-scoped runtime input bindings, frozen into the run contract.",
    )
    launch_parser.add_argument("--skip-token-count", action="store_true")
    launch_parser.add_argument("--wait", action="store_true")

    rerun_parser = subparsers.add_parser("rerun-archived", help="Rerun one eligible archived failed-no-artifact stage.")
    _add_root_argument(rerun_parser)
    _add_session_argument(rerun_parser)
    rerun_parser.add_argument("--archive-manifest", required=True, type=_path_argument)
    rerun_parser.add_argument("--workflow-file", required=True, type=_path_argument)
    rerun_parser.add_argument("--primary-job-input", action="append", default=[])
    rerun_parser.add_argument("--reference-context", action="append", default=[])
    rerun_parser.add_argument("--review-bundle", action="append", default=[])
    rerun_parser.add_argument(
        "--input-binding-file",
        type=_path_argument,
        help="The same versioned stage-scoped runtime input bindings frozen for the original run.",
    )
    rerun_parser.add_argument("--wait", action="store_true")

    monitor_parser = subparsers.add_parser("monitor", help="Record monitoring state/anomaly.")
    _add_root_argument(monitor_parser)
    _add_session_argument(monitor_parser)
    monitor_parser.add_argument("--run-dir", required=True, type=_path_argument)
    monitor_parser.add_argument("--stage", required=True)
    monitor_parser.add_argument("--stale-after-seconds", type=float, default=6 * 60 * 60)

    classify_parser = subparsers.add_parser("classify", help="Classify a stage outcome.")
    _add_root_argument(classify_parser)
    _add_session_argument(classify_parser)
    classify_parser.add_argument("--run-dir", required=True, type=_path_argument)
    classify_parser.add_argument("--stage", required=True)
    classify_parser.add_argument("--output", type=_path_argument)

    archive_parser = subparsers.add_parser("archive-attempt", help="Archive a failed attempt.")
    _add_root_argument(archive_parser)
    _add_session_argument(archive_parser)
    archive_parser.add_argument("--run-dir", required=True, type=_path_argument)
    archive_parser.add_argument("--stage", required=True)
    archive_parser.add_argument("--reason", required=True)

    bundle_parser = subparsers.add_parser("create-bundle", help="Create approved review bundle.")
    _add_root_argument(bundle_parser)
    _add_session_argument(bundle_parser)
    bundle_parser.add_argument("--output", type=_path_argument)
    bundle_parser.add_argument("--workflow-id", required=True)
    bundle_parser.add_argument("--source-stage-id", required=True)
    bundle_parser.add_argument("--source-run-id", required=True)
    bundle_parser.add_argument("--primary-artifact-markdown", required=True, type=_path_argument)
    bundle_parser.add_argument("--response-artifact-json", required=True, type=_path_argument)
    bundle_parser.add_argument("--reviewer-notes", required=True, type=_path_argument)
    bundle_parser.add_argument("--acceptance-record", required=True, type=_path_argument)
    bundle_parser.add_argument("--approved-handoff-markdown", type=_path_argument)
    bundle_parser.add_argument("--structured-artifact-json", type=_path_argument)

    final_parser = subparsers.add_parser("finalize-bundle", help="Create final implementation bundle.")
    _add_root_argument(final_parser)
    _add_session_argument(final_parser)
    final_parser.add_argument("--packet-json", required=True, type=_path_argument)
    final_parser.add_argument("--output", type=_path_argument)

    usage_parser = subparsers.add_parser(
        "usage-report",
        help="Build reviewer-attempt usage totals for a supervisor session.",
    )
    _add_root_argument(usage_parser)
    _add_session_argument(usage_parser)

    validate_parser = subparsers.add_parser("validate-session", help="Validate session manifest.")
    _add_root_argument(validate_parser)
    _add_session_argument(validate_parser)

    return parser.parse_args(argv)


def _print_result(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root(getattr(args, "root", None))

    if args.command == "init-session":
        payload = supervisor.create_session(
            root=root,
            clarified_task_brief=args.clarified_task_brief,
            summary=args.summary,
            session_id=args.session_id,
        )
        _print_result({"session": payload["supervisor_session_id"], "manifest": payload["_manifest_path"]})
        return 0

    if args.command == "stage-scaffold":
        _print_result(
            supervisor.stage_scaffold(
                root=root,
                session_ref=args.session,
                scaffold_path=args.scaffold_path,
                created_by=args.created_by,
            )
        )
        return 0

    if args.command == "examine-scaffold":
        _print_result(
            supervisor.examine_scaffold(
                root=root,
                session_ref=args.session,
                workflow_file=args.workflow_file,
                output=args.output,
            )
        )
        return 0

    if args.command == "dry-run-scaffold":
        _print_result(
            supervisor.dry_run_scaffold(
                root=root,
                session_ref=args.session,
                workflow_file=args.workflow_file,
                run_name=args.run_name,
                primary_job_inputs=args.primary_job_input,
                reference_context=args.reference_context,
                review_bundles=args.review_bundle,
                input_binding_file=args.input_binding_file,
                stage_id=args.stage,
            )
        )
        return 0

    if args.command == "invoke-operator":
        _print_result(
            supervisor.invoke_operator(
                root=root,
                session_ref=args.session,
                review_cycle_id=args.review_cycle,
                review_kind=args.review_kind,
                job_json=args.job_json,
                output_dir=args.output_dir,
            )
        )
        return 0

    if args.command == "invoke-reviewers":
        _print_result(
            supervisor.invoke_reviewers(
                root=root,
                session_ref=args.session,
                review_cycle_id=args.review_cycle,
                review_kind=args.review_kind,
                job_json=args.job_json,
                output_dir=args.output_dir,
            )
        )
        return 0

    if args.command == "review-cycle":
        _print_result(
            supervisor.run_review_cycle(
                root=root,
                session_ref=args.session,
                review_cycle_id=args.review_cycle,
                review_kind=args.review_kind,
                job_json=args.job_json,
            )
        )
        return 0

    if args.command == "prepare-revision":
        rejected: dict[str, str] = {}
        for item in args.reject_recommendation:
            recommendation_id, separator, rationale = item.partition("=")
            if not separator or not recommendation_id.strip() or not rationale.strip():
                raise SystemExit("--reject-recommendation must use ID=RATIONALE with both values non-empty.")
            if recommendation_id in rejected:
                raise SystemExit(f"Duplicate rejected recommendation: {recommendation_id}")
            rejected[recommendation_id] = rationale
        _print_result(
            supervisor.create_revision_directive(
                root=root,
                session_ref=args.session,
                review_cycle_id=args.review_cycle,
                accepted_recommendation_ids=args.accept_recommendation,
                rejected_recommendations=rejected,
                revised_artifacts=args.revised_artifact,
                revision_scaffold_path=args.revision_scaffold_path,
            )
        )
        return 0

    if args.command == "run-revision":
        _print_result(
            supervisor.run_revision_and_review(
                root=root,
                session_ref=args.session,
                source_review_cycle_id=args.source_review_cycle,
                new_review_cycle_id=args.new_review_cycle,
            )
        )
        return 0

    if args.command == "consolidate":
        _print_result(
            supervisor.consolidate_reviews(
                root=root,
                session_ref=args.session,
                review_cycle_id=args.review_cycle,
                codex_review=args.codex_review,
                claude_review=args.claude_review,
                output=args.output,
                operator_review=args.operator_review,
            )
        )
        return 0

    if args.command == "accept":
        _print_result(
            supervisor.accept_consolidated_review(
                root=root,
                session_ref=args.session,
                review_cycle_id=args.review_cycle,
                consolidated_review=args.consolidated_review,
                accepted_recommendation_ids=args.accept_recommendation,
                output=args.output,
                applied_change_evidence=args.applied_change_evidence,
                blocker_resolutions=args.blocker_resolution,
            )
        )
        return 0

    if args.command == "resolve-blocker":
        resolution_evidence = load_json(
            resolve_under_root(root, args.resolution_evidence_json, must_exist=True),
            "blocker resolution evidence",
        )
        _print_result(
            supervisor.record_blocker_resolution(
                root=root,
                session_ref=args.session,
                review_cycle_id=args.review_cycle,
                blocker_id=args.blocker_id,
                resolution=args.resolution,
                evidence=args.evidence,
                affected_artifacts=resolution_evidence.get("affected_artifacts", []),
                applied_changes=resolution_evidence.get("applied_changes", []),
                validation_evidence=resolution_evidence.get("validation_evidence", []),
                operator_rationale=str(resolution_evidence.get("operator_rationale") or ""),
                accepted_risk_rationale=resolution_evidence.get("accepted_risk_rationale"),
            )
        )
        return 0

    if args.command == "launch":
        _print_result(
            supervisor.launch_scaffold(
                root=root,
                session_ref=args.session,
                workflow_file=args.workflow_file,
                run_name=args.run_name,
                primary_job_inputs=args.primary_job_input,
                reference_context=args.reference_context,
                review_bundles=args.review_bundle,
                input_binding_file=args.input_binding_file,
                stage_id=args.stage,
                skip_token_count=args.skip_token_count,
                wait=args.wait,
            )
        )
        return 0

    if args.command == "rerun-archived":
        _print_result(
            supervisor.rerun_archived_stage(
                root=root,
                session_ref=args.session,
                archive_manifest=args.archive_manifest,
                workflow_file=args.workflow_file,
                primary_job_inputs=args.primary_job_input,
                reference_context=args.reference_context,
                review_bundles=args.review_bundle,
                input_binding_file=args.input_binding_file,
                wait=args.wait,
            )
        )
        return 0

    if args.command == "monitor":
        _print_result(
            supervisor.monitor_stage(
                root=root,
                session_ref=args.session,
                run_dir=args.run_dir,
                stage_id=args.stage,
                stale_after_seconds=args.stale_after_seconds,
            )
        )
        return 0

    if args.command == "classify":
        _print_result(
            supervisor.classify_stage(
                root=root,
                session_ref=args.session,
                run_dir=args.run_dir,
                stage_id=args.stage,
                output=args.output,
            )
        )
        return 0

    if args.command == "archive-attempt":
        _print_result(
            supervisor.archive_attempt(
                root=root,
                session_ref=args.session,
                run_dir=args.run_dir,
                stage_id=args.stage,
                reason=args.reason,
            )
        )
        return 0

    if args.command == "create-bundle":
        _print_result(
            supervisor.create_approved_review_bundle(
                root=root,
                session_ref=args.session,
                output_path=args.output,
                workflow_id=args.workflow_id,
                source_stage_id=args.source_stage_id,
                source_run_id=args.source_run_id,
                primary_artifact_markdown=args.primary_artifact_markdown,
                response_artifact_json=args.response_artifact_json,
                reviewer_notes=args.reviewer_notes,
                acceptance_record=args.acceptance_record,
                approved_handoff_markdown=args.approved_handoff_markdown,
                structured_artifact_json=args.structured_artifact_json,
            )
        )
        return 0

    if args.command == "finalize-bundle":
        _print_result(
            supervisor.finalize_bundle(
                root=root,
                session_ref=args.session,
                packet_json=args.packet_json,
                output=args.output,
            )
        )
        return 0

    if args.command == "usage-report":
        _print_result(
            telemetry.write_supervisor_usage_report(
                root=root,
                session_ref=args.session,
            )
        )
        return 0

    _print_result(supervisor.validate_session(root=root, session_ref=args.session))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
