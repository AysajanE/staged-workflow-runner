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

from automation.responses_runner_v2.contracts import repo_root
from automation.responses_runner_v2 import supervisor


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

    dry_parser = subparsers.add_parser("dry-run-scaffold", help="Dry-run a scaffold workflow.")
    _add_root_argument(dry_parser)
    _add_session_argument(dry_parser)
    dry_parser.add_argument("--workflow-file", required=True, type=_path_argument)
    dry_parser.add_argument("--run-name", default="supervisor-scaffold-dry-run")

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

    consolidate_parser = subparsers.add_parser("consolidate", help="Consolidate reviews.")
    _add_root_argument(consolidate_parser)
    _add_session_argument(consolidate_parser)
    consolidate_parser.add_argument("--review-cycle", required=True)
    consolidate_parser.add_argument("--codex-review", required=True, type=_path_argument)
    consolidate_parser.add_argument("--claude-review", required=True, type=_path_argument)
    consolidate_parser.add_argument("--operator-review", type=_path_argument)
    consolidate_parser.add_argument("--output", required=True, type=_path_argument)

    accept_parser = subparsers.add_parser("accept", help="Create operator acceptance record.")
    _add_root_argument(accept_parser)
    _add_session_argument(accept_parser)
    accept_parser.add_argument("--review-cycle", required=True)
    accept_parser.add_argument("--consolidated-review", required=True, type=_path_argument)
    accept_parser.add_argument("--accept-recommendation", action="append", default=[])
    accept_parser.add_argument(
        "--applied-change-evidence",
        type=_path_argument,
        help=(
            "JSON file containing operator-applied change evidence for accepted recommendations. "
            "Selected recommendations without matching evidence are rejected, not fabricated."
        ),
    )
    accept_parser.add_argument("--output", required=True, type=_path_argument)

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
    bundle_parser.add_argument("--output", required=True, type=_path_argument)
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
    final_parser.add_argument("--output", required=True, type=_path_argument)

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

    if args.command == "dry-run-scaffold":
        _print_result(
            supervisor.dry_run_scaffold(
                root=root,
                session_ref=args.session,
                workflow_file=args.workflow_file,
                run_name=args.run_name,
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

    _print_result(supervisor.validate_session(root=root, session_ref=args.session))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
