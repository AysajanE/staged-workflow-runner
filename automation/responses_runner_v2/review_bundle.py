from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from .contracts import (
    REVIEW_BUNDLE_SCHEMA_VERSION,
    AttachmentEntry,
    relpath,
    repo_root,
    resolve_under_root,
    runner_now,
    sha256_file,
    write_json,
)


def create_review_bundle(
    *,
    root: Path | None,
    output_path: str | Path,
    workflow_id: str,
    source_stage_id: str,
    source_run_id: str,
    primary_artifact_markdown: str | Path,
    response_artifact_json: str | Path,
    reviewer_notes: str | Path,
    structured_artifact_json: str | Path | None = None,
    locked_decisions: Sequence[str] = (),
    open_dependencies: Sequence[str] = (),
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    root = root or repo_root()
    output = resolve_under_root(root, output_path, must_exist=False)
    primary_md = resolve_under_root(root, primary_artifact_markdown, must_exist=True)
    response_json = resolve_under_root(root, response_artifact_json, must_exist=True)
    reviewer_notes_path = resolve_under_root(root, reviewer_notes, must_exist=True)
    structured_json_path = (
        resolve_under_root(root, structured_artifact_json, must_exist=True)
        if structured_artifact_json is not None
        else None
    )
    payload: dict[str, Any] = {
        "schema_version": REVIEW_BUNDLE_SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "source_stage_id": source_stage_id,
        "source_run_id": source_run_id,
        "created_at": runner_now().isoformat(),
        "review_status": "approved",
        "primary_artifact_markdown": relpath(root, primary_md),
        "response_artifact_json": relpath(root, response_json),
        "reviewer_notes": relpath(root, reviewer_notes_path),
        "artifact_hashes": {
            "primary_artifact_markdown_sha256": sha256_file(primary_md),
            "response_artifact_json_sha256": sha256_file(response_json),
            "reviewer_notes_sha256": sha256_file(reviewer_notes_path),
        },
        "locked_decisions": [item for item in locked_decisions if item],
        "open_dependencies": [item for item in open_dependencies if item],
    }
    if structured_json_path is not None:
        payload["structured_artifact_json"] = relpath(root, structured_json_path)
        payload["artifact_hashes"]["structured_artifact_json_sha256"] = sha256_file(
            structured_json_path
        )
    if notes:
        payload["notes"] = [item for item in notes if item]
    write_json(output, payload)
    payload["bundle_path"] = relpath(root, output)
    return payload


def load_review_bundle(
    *,
    root: Path | None,
    bundle_path: str | Path,
) -> dict[str, Any]:
    root = root or repo_root()
    resolved_bundle = resolve_under_root(root, bundle_path, must_exist=True)
    payload = json.loads(resolved_bundle.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"review bundle must be a JSON object: {resolved_bundle}")
    required = [
        "schema_version",
        "workflow_id",
        "source_stage_id",
        "source_run_id",
        "created_at",
        "review_status",
        "primary_artifact_markdown",
        "response_artifact_json",
        "reviewer_notes",
        "artifact_hashes",
        "locked_decisions",
        "open_dependencies",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        raise SystemExit(
            f"review bundle missing required keys: {', '.join(sorted(missing))}"
        )
    if payload.get("schema_version") != REVIEW_BUNDLE_SCHEMA_VERSION:
        raise SystemExit(
            f"Unexpected review bundle schema_version in {resolved_bundle}: {payload.get('schema_version')!r}"
        )
    resolved = dict(payload)
    resolved["bundle_path"] = relpath(root, resolved_bundle)
    resolved["_bundle_path_resolved"] = resolved_bundle
    for key in ("primary_artifact_markdown", "response_artifact_json", "reviewer_notes"):
        path = resolve_under_root(root, str(payload[key]), must_exist=True)
        resolved[f"_{key}_resolved"] = path
    if payload.get("structured_artifact_json") is not None:
        resolved["_structured_artifact_json_resolved"] = resolve_under_root(
            root,
            str(payload["structured_artifact_json"]),
            must_exist=True,
        )
    hashes = payload["artifact_hashes"]
    if not isinstance(hashes, dict):
        raise SystemExit("review bundle artifact_hashes must be an object.")
    if sha256_file(resolved["_primary_artifact_markdown_resolved"]) != hashes.get(
        "primary_artifact_markdown_sha256"
    ):
        raise SystemExit("review bundle primary_artifact_markdown hash mismatch.")
    if sha256_file(resolved["_response_artifact_json_resolved"]) != hashes.get(
        "response_artifact_json_sha256"
    ):
        raise SystemExit("review bundle response_artifact_json hash mismatch.")
    if sha256_file(resolved["_reviewer_notes_resolved"]) != hashes.get(
        "reviewer_notes_sha256"
    ):
        raise SystemExit("review bundle reviewer_notes hash mismatch.")
    if payload.get("structured_artifact_json") is not None:
        if sha256_file(resolved["_structured_artifact_json_resolved"]) != hashes.get(
            "structured_artifact_json_sha256"
        ):
            raise SystemExit("review bundle structured_artifact_json hash mismatch.")
    return resolved


def validate_review_bundle_for_stage(
    bundle: dict[str, Any],
    *,
    workflow_id: str,
    expected_source_stage_id: str,
    expected_source_run_id: str,
    root: Path | None = None,
    source_stage_summary: dict[str, Any] | None = None,
) -> None:
    if bundle.get("review_status") != "approved":
        raise SystemExit("review bundle must have review_status=approved.")
    if bundle.get("workflow_id") != workflow_id:
        raise SystemExit(
            f"review bundle workflow_id mismatch: expected {workflow_id!r}, got {bundle.get('workflow_id')!r}"
        )
    if bundle.get("source_stage_id") != expected_source_stage_id:
        raise SystemExit(
            "review bundle source_stage_id mismatch: "
            f"expected {expected_source_stage_id!r}, got {bundle.get('source_stage_id')!r}"
        )
    if bundle.get("source_run_id") != expected_source_run_id:
        raise SystemExit(
            "review bundle source_run_id mismatch: "
            f"expected {expected_source_run_id!r}, got {bundle.get('source_run_id')!r}"
        )
    if source_stage_summary is not None:
        validation_root = root or repo_root()
        _validate_bundle_artifact_matches_stage_summary(
            bundle=bundle,
            root=validation_root,
            source_stage_summary=source_stage_summary,
            bundle_path_key="primary_artifact_markdown",
            bundle_hash_key="primary_artifact_markdown_sha256",
            source_summary_path_key="response_markdown_path",
            source_summary_hash_key="response_markdown_sha256",
            label="primary_artifact_markdown",
        )
        _validate_bundle_artifact_matches_stage_summary(
            bundle=bundle,
            root=validation_root,
            source_stage_summary=source_stage_summary,
            bundle_path_key="response_artifact_json",
            bundle_hash_key="response_artifact_json_sha256",
            source_summary_path_key="response_json_path",
            source_summary_hash_key="response_json_sha256",
            label="response_artifact_json",
        )
        if bundle.get("structured_artifact_json") is not None:
            _validate_bundle_artifact_matches_stage_summary(
                bundle=bundle,
                root=validation_root,
                source_stage_summary=source_stage_summary,
                bundle_path_key="structured_artifact_json",
                bundle_hash_key="structured_artifact_json_sha256",
                source_summary_path_key="structured_output_path",
                source_summary_hash_key="structured_output_sha256",
                label="structured_artifact_json",
            )


def _validate_bundle_artifact_matches_stage_summary(
    *,
    bundle: dict[str, Any],
    root: Path,
    source_stage_summary: dict[str, Any],
    bundle_path_key: str,
    bundle_hash_key: str,
    source_summary_path_key: str,
    source_summary_hash_key: str,
    label: str,
) -> None:
    source_path = source_stage_summary.get(source_summary_path_key)
    if not isinstance(source_path, str) or not source_path:
        raise SystemExit(
            f"review bundle {label} cannot be validated: source stage summary is missing "
            f"{source_summary_path_key}."
        )
    if str(bundle.get(bundle_path_key)) != source_path:
        raise SystemExit(
            f"review bundle {label} must match the recorded source stage artifact path."
        )
    recorded_path = resolve_under_root(root, source_path, must_exist=True)
    recorded_hash = source_stage_summary.get(source_summary_hash_key)
    if not isinstance(recorded_hash, str) or not recorded_hash:
        recorded_hash = sha256_file(recorded_path)
    if bundle.get("artifact_hashes", {}).get(bundle_hash_key) != recorded_hash:
        raise SystemExit(
            f"review bundle {label} must match the recorded source stage artifact hash."
        )


def expand_review_bundle_inputs(bundle: dict[str, Any]) -> list[AttachmentEntry]:
    entries = [
        AttachmentEntry(
            path=str(bundle["bundle_path"]),
            kind="file",
            notes="review bundle contract",
        ),
        AttachmentEntry(
            path=str(bundle["primary_artifact_markdown"]),
            kind="file",
            notes=f"approved markdown from stage {bundle['source_stage_id']}",
        ),
        AttachmentEntry(
            path=str(bundle["response_artifact_json"]),
            kind="file",
            notes=f"approved raw response JSON from stage {bundle['source_stage_id']}",
        ),
    ]
    if bundle.get("structured_artifact_json") is not None:
        entries.append(
            AttachmentEntry(
                path=str(bundle["structured_artifact_json"]),
                kind="file",
                notes=f"approved structured output from stage {bundle['source_stage_id']}",
            )
        )
    entries.append(
        AttachmentEntry(
            path=str(bundle["reviewer_notes"]),
            kind="file",
            notes="reviewer notes for approved handoff",
        )
    )
    return entries
