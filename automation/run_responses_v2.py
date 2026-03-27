#!/usr/bin/env python3
"""Generic CLI entrypoint for Responses Runner v2."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from automation.responses_runner_v2.contracts import (
    DEFAULT_MAX_WAIT_SECONDS,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_POLL_INTERVAL,
    RuntimeOptions,
    repo_root,
)
from automation.responses_runner_v2.openai_client import OpenAIClient
from automation.responses_runner_v2.workflow import refresh_stage, resume_stage, run_workflow


def _path_argument(value: str) -> Path:
    return Path(value)


def _add_root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=_path_argument,
        help=(
            "Exact workspace root to resolve workflow, artifact, review-bundle, and output paths "
            "against. If omitted, RESPONSES_RUNNER_V2_ROOT is used when set; otherwise the current "
            "working directory is used as-is."
        ),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Responses Runner v2 workflow engine against an exact workspace root."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Launch the next eligible stage or continue an existing workflow run.",
    )
    _add_root_argument(run_parser)
    run_parser.add_argument("--workflow-file", required=True, type=_path_argument)
    run_parser.add_argument("--run-name")
    run_parser.add_argument("--run-dir", type=_path_argument)
    run_parser.add_argument("--stage")
    run_parser.add_argument("--primary-job-input", action="append", default=[])
    run_parser.add_argument("--reference-context", action="append", default=[])
    run_parser.add_argument("--review-bundle", action="append", default=[])
    run_parser.add_argument("--output-root", type=_path_argument, default=Path(DEFAULT_OUTPUT_ROOT))
    run_parser.add_argument("--max-input-tokens", type=int)
    run_parser.add_argument("--skip-token-count", action="store_true")
    run_parser.add_argument("--max-output-tokens", type=int)
    run_parser.add_argument("--file-expires-after")
    run_parser.add_argument("--delete-uploaded-files-on-complete", action="store_true")
    run_parser.add_argument("--primary-model")
    run_parser.add_argument("--structural-model")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--wait", action="store_true")
    run_parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    run_parser.add_argument("--max-wait-seconds", type=float, default=DEFAULT_MAX_WAIT_SECONDS)
    run_parser.add_argument("--service-tier", choices=["auto", "default", "flex", "priority", "scale"])
    run_parser.add_argument("--safety-identifier")

    resume_parser = subparsers.add_parser(
        "resume",
        help="Resume a nonterminal stage using the stored response_id.",
    )
    _add_root_argument(resume_parser)
    resume_parser.add_argument("--run-dir", required=True, type=_path_argument)
    resume_parser.add_argument("--stage", required=True)
    resume_parser.add_argument("--wait", action="store_true")
    resume_parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    resume_parser.add_argument("--max-wait-seconds", type=float, default=DEFAULT_MAX_WAIT_SECONDS)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Refresh remote stage status without resubmitting work.",
    )
    _add_root_argument(refresh_parser)
    refresh_parser.add_argument("--run-dir", required=True, type=_path_argument)
    refresh_parser.add_argument("--stage", required=True)

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root(getattr(args, "root", None))

    if args.command == "run":
        runtime = RuntimeOptions(
            run_name=args.run_name,
            run_dir=args.run_dir,
            stage_id=args.stage,
            primary_job_inputs=list(args.primary_job_input),
            reference_context=list(args.reference_context),
            review_bundles=list(args.review_bundle),
            output_root=args.output_root,
            max_input_tokens=args.max_input_tokens,
            skip_token_count=args.skip_token_count,
            max_output_tokens=args.max_output_tokens,
            file_expires_after=args.file_expires_after,
            delete_uploaded_files_on_complete=(
                True if args.delete_uploaded_files_on_complete else None
            ),
            primary_model=args.primary_model,
            structural_model=args.structural_model,
            dry_run=args.dry_run,
            wait=args.wait,
            poll_interval=args.poll_interval,
            max_wait_seconds=args.max_wait_seconds,
            service_tier=args.service_tier,
            safety_identifier=args.safety_identifier,
        )
        client = None if args.dry_run else OpenAIClient.from_env(root=root)
        result = run_workflow(
            workflow_file=args.workflow_file,
            runtime=runtime,
            client=client,
            root=root,
        )
        print(result["run_manifest_path"])
        return 0

    client = OpenAIClient.from_env(root=root)
    if args.command == "resume":
        result = resume_stage(
            run_dir=args.run_dir,
            stage_id=args.stage,
            wait=args.wait,
            poll_interval=args.poll_interval,
            max_wait_seconds=args.max_wait_seconds,
            client=client,
            root=root,
        )
        print(result["run_manifest_path"])
        return 0

    result = refresh_stage(
        run_dir=args.run_dir,
        stage_id=args.stage,
        client=client,
        root=root,
    )
    print(result["run_manifest_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
