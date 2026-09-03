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
from automation.responses_runner_v2.pack_loader import (
    load_runtime_input_bindings,
    load_workflow_definition,
)
from automation.responses_runner_v2.workflow import (
    cancel_stage,
    recover_uploads,
    refresh_stage,
    resume_stage,
    run_workflow,
)


def _path_argument(value: str) -> Path:
    return Path(value)


def _add_root_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=_path_argument,
        help=(
            "Exact workspace root to resolve workflow, artifact, handoff-note, and output paths "
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
    run_parser.add_argument(
        "--input-binding-file",
        type=_path_argument,
        help="Versioned stage-scoped runtime input bindings; legacy input flags remain workflow-scoped.",
    )
    run_parser.add_argument("--output-root", type=_path_argument, default=Path(DEFAULT_OUTPUT_ROOT))
    run_parser.add_argument("--max-input-tokens", type=int)
    run_parser.add_argument("--skip-token-count", action="store_true")
    run_parser.add_argument("--max-output-tokens", type=int)
    run_parser.add_argument("--file-expires-after")
    run_parser.add_argument("--delete-uploaded-files-on-complete", action="store_true")
    run_parser.add_argument("--primary-model")
    run_parser.add_argument("--structural-model")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument(
        "--handoff-note",
        type=_path_argument,
        help=(
            "Approve the stage waiting at a human gate (or a blocked reviewed gate) with this "
            "markdown note; the note is attached to the next stage as a reviewed handoff input."
        ),
    )
    run_parser.add_argument(
        "--reviewer",
        choices=["codex", "claude", "none"],
        help="Override the workflow's reviewer for reviewed gates in this invocation.",
    )
    run_parser.add_argument(
        "--wait",
        dest="wait",
        action="store_true",
        default=True,
        help="Wait in-process until the stage reaches a terminal state (default).",
    )
    run_parser.add_argument(
        "--no-wait",
        dest="wait",
        action="store_false",
        help="Return right after submission; finish the stage later with resume.",
    )
    run_parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    run_parser.add_argument("--max-wait-seconds", type=float, default=DEFAULT_MAX_WAIT_SECONDS)
    run_parser.add_argument("--service-tier", choices=["auto", "default", "flex", "priority", "scale"])
    run_parser.add_argument("--safety-identifier")
    run_parser.add_argument(
        "--prompt-cache-key-strategy",
        choices=["legacy_stage_v1", "stable_lane_v1"],
        default="stable_lane_v1",
        help="Stable compatible-lane keys are the default; select legacy_stage_v1 for paired A/B comparison.",
    )

    resume_parser = subparsers.add_parser(
        "resume",
        help="Resume a nonterminal stage using the stored response_id.",
    )
    _add_root_argument(resume_parser)
    resume_parser.add_argument("--run-dir", required=True, type=_path_argument)
    resume_parser.add_argument("--stage", required=True)
    resume_parser.add_argument(
        "--wait",
        dest="wait",
        action="store_true",
        default=True,
        help="Wait in-process until the stage reaches a terminal state (default).",
    )
    resume_parser.add_argument(
        "--no-wait",
        dest="wait",
        action="store_false",
        help="Record the current remote status and return.",
    )
    resume_parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    resume_parser.add_argument("--max-wait-seconds", type=float, default=DEFAULT_MAX_WAIT_SECONDS)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Refresh remote stage status without resubmitting work.",
    )
    _add_root_argument(refresh_parser)
    refresh_parser.add_argument("--run-dir", required=True, type=_path_argument)
    refresh_parser.add_argument("--stage", required=True)

    cancel_parser = subparsers.add_parser(
        "cancel",
        help="Idempotently cancel a known live response and finalize its local evidence.",
    )
    _add_root_argument(cancel_parser)
    cancel_parser.add_argument("--run-dir", required=True, type=_path_argument)
    cancel_parser.add_argument("--stage", required=True)

    recover_parser = subparsers.add_parser(
        "recover-uploads",
        help="Resume idempotent cleanup of uploads recorded for one stage attempt.",
    )
    _add_root_argument(recover_parser)
    recover_parser.add_argument("--run-dir", required=True, type=_path_argument)
    recover_parser.add_argument("--stage", required=True)
    recover_parser.add_argument("--attempt", type=int)


    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root(getattr(args, "root", None))

    if args.command == "run":
        workflow_for_bindings = load_workflow_definition(
            args.workflow_file,
            root=root,
            primary_model_override=args.primary_model,
            structural_model_override=args.structural_model,
        )
        input_bindings = (
            load_runtime_input_bindings(
                args.input_binding_file,
                workflow=workflow_for_bindings,
                root=root,
            )
            if args.input_binding_file
            else []
        )
        runtime = RuntimeOptions(
            run_name=args.run_name,
            run_dir=args.run_dir,
            stage_id=args.stage,
            primary_job_inputs=list(args.primary_job_input),
            reference_context=list(args.reference_context),
            input_bindings=input_bindings,
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
            handoff_note=str(args.handoff_note) if args.handoff_note else None,
            reviewer_override=args.reviewer,
            service_tier=args.service_tier,
            safety_identifier=args.safety_identifier,
            prompt_cache_key_strategy=args.prompt_cache_key_strategy,
        )
        client = None if args.dry_run else OpenAIClient.from_env(root=root)
        result = run_workflow(
            workflow_file=args.workflow_file,
            runtime=runtime,
            client=client,
            root=root,
        )
        for warning in result.get("warnings", []):
            print(
                f"WARNING [{warning['code']}] {warning['message']} "
                f"({warning['diagnostics_path']})",
                file=sys.stderr,
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

    if args.command == "cancel":
        result = cancel_stage(
            run_dir=args.run_dir,
            stage_id=args.stage,
            client=client,
            root=root,
        )
        print(result["run_manifest_path"])
        return 0

    if args.command == "recover-uploads":
        result = recover_uploads(
            run_dir=args.run_dir,
            stage_id=args.stage,
            attempt_number=args.attempt,
            client=client,
            root=root,
        )
        print(result["uploads_path"])
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
