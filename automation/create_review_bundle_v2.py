#!/usr/bin/env python3
"""Create a Responses Runner v2 review bundle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from automation.responses_runner_v2.contracts import repo_root
from automation.responses_runner_v2.review_bundle import create_review_bundle


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an approved Responses Runner v2 review bundle under one exact workspace root."
    )
    parser.add_argument(
        "--root",
        type=Path,
        help=(
            "Exact workspace root to resolve bundle inputs and output against. If omitted, "
            "RESPONSES_RUNNER_V2_ROOT is used when set; otherwise the current working directory "
            "is used as-is."
        ),
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--source-stage-id", required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--primary-artifact-markdown", required=True, type=Path)
    parser.add_argument("--response-artifact-json", required=True, type=Path)
    parser.add_argument("--reviewer-notes", required=True, type=Path)
    parser.add_argument("--structured-artifact-json", type=Path)
    parser.add_argument("--locked-decision", action="append", default=[])
    parser.add_argument("--open-dependency", action="append", default=[])
    parser.add_argument("--note", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = repo_root(args.root)
    payload = create_review_bundle(
        root=root,
        output_path=args.output,
        workflow_id=args.workflow_id,
        source_stage_id=args.source_stage_id,
        source_run_id=args.source_run_id,
        primary_artifact_markdown=args.primary_artifact_markdown,
        response_artifact_json=args.response_artifact_json,
        reviewer_notes=args.reviewer_notes,
        structured_artifact_json=args.structured_artifact_json,
        locked_decisions=args.locked_decision,
        open_dependencies=args.open_dependency,
        notes=args.note,
    )
    print(payload["bundle_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
