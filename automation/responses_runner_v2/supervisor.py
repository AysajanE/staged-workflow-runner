from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .contracts import (
    DEFAULT_PRIMARY_MODEL,
    DEFAULT_STRUCTURAL_MODEL,
    FINAL_IMPLEMENTATION_BUNDLE_SCHEMA_VERSION,
    REVIEW_DECISION_SCHEMA_VERSION,
    SUPERVISOR_SESSION_SCHEMA_VERSION,
    RuntimeOptions,
    load_json,
    normalize_slug,
    relpath,
    resolve_under_root,
    runner_now,
    sha256_file,
    sha256_text,
    write_json,
    write_text,
)
from . import attachments
from .pack_loader import (
    load_input_manifest,
    load_runtime_input_bindings,
    load_schema_json,
    load_tool_profile,
    load_workflow_definition,
)
from .openai_client import OpenAIClient
from .review_bundle import create_review_bundle
from .run_contract import load_and_verify_run_contract
from .workflow import run_workflow
from .validators import validate_commonmark_fences
from . import artifacts as runner_artifacts
from . import supervisor_agents
from . import supervisor_artifacts
from . import supervisor_policies

OPERATOR_BOUNDARY = "headless_discrete_codex_exec_with_deterministic_supervisor_cli"
REVIEW_KINDS_REQUIRING_OPERATOR_PROVISIONAL = {"scaffold", "stage_output", "final_packet", "recovery"}
UNSUPPORTED_OUTPUT_SCHEMA_KEYWORDS = {
    "if",
    "then",
    "else",
    "allOf",
    "oneOf",
    "dependentRequired",
    "dependentSchemas",
    "not",
}

REVIEW_SUBJECT_SCHEMA_VERSION = "responses_runner_v2.review_subject.v1"
REVIEW_INPUT_SCHEMA_VERSION = "responses_runner_v2.review_input.v2"
REVIEW_QUORUM_SCHEMA_VERSION = "responses_runner_v2.review_quorum.v1"
ACCEPTANCE_BINDING_SCHEMA_VERSION = "responses_runner_v2.operator_acceptance_binding.v1"
REVIEW_BUNDLE_BINDING_SCHEMA_VERSION = "responses_runner_v2.review_bundle_binding.v1"
FINAL_BUNDLE_BINDING_SCHEMA_VERSION = "responses_runner_v2.final_bundle_binding.v1"
REVISION_DIRECTIVE_SCHEMA_VERSION = "responses_runner_v2.revision_directive.v1"
REVISION_RESULT_SCHEMA_VERSION = "responses_runner_v2.revision_result.v1"
REVIEW_ROLES = ("operator_codex", "codex_review_agent", "claude_review_agent")
READ_ONLY_REVIEW_ROLES = {"codex_review_agent", "claude_review_agent"}
CLEARING_RESOLUTIONS = {"resolved", "accepted_risk", "superseded"}
_SESSION_MUTATION_LOCK_STATE = threading.local()


def _load_session_and_path(root: Path, session_ref: str | Path) -> tuple[dict[str, Any], Path]:
    path = supervisor_artifacts.session_dir(root, session_ref)
    return supervisor_artifacts.load_session(root, path), path


def _write_session(root: Path, session_path: Path, session: dict[str, Any]) -> dict[str, Any]:
    with _session_mutation_lock(session_path):
        return supervisor_artifacts.write_session(root, session_path, session)


def _canonical_sha256(payload: Any) -> str:
    return sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _supervisor_input_bindings(
    *,
    root: Path,
    workflow_file: str | Path,
    input_binding_file: str | Path | None,
) -> list[Any]:
    if input_binding_file is None:
        return []
    workflow = load_workflow_definition(workflow_file, root=root)
    return load_runtime_input_bindings(input_binding_file, workflow=workflow, root=root)


def _cycle_dir(session_path: Path, review_cycle_id: str) -> Path:
    if normalize_slug(review_cycle_id) != review_cycle_id:
        raise SystemExit("review_cycle_id must already be a normalized slug.")
    return session_path / "review_cycles" / review_cycle_id


def _cycle_paths(root: Path, session_path: Path, review_cycle_id: str) -> dict[str, str]:
    base = _cycle_dir(session_path, review_cycle_id)
    paths = {
        "cycle_dir": base,
        "subject_dir": base / "subject",
        "frozen_job": base / "subject" / "frozen_job.json",
        "reviewed_artifact_manifest": base / "subject" / "reviewed_artifacts.manifest.json",
        "review_input": base / "subject" / "review_input.json",
        "review_subject": base / "subject" / "review_subject.json",
        "operator_dir": base / "operator",
        "reviewers_dir": base / "reviewers",
        "codex_reviewer_dir": base / "reviewers" / "codex",
        "claude_reviewer_dir": base / "reviewers" / "claude",
        "consolidation_json": base / "consolidation" / "consolidated_review.json",
        "consolidation_md": base / "consolidation" / "consolidated_review.md",
        "quorum": base / "consolidation" / "review_quorum.json",
        "acceptance_json": base / "acceptance" / "operator_acceptance.json",
        "acceptance_md": base / "acceptance" / "operator_acceptance.md",
        "acceptance_binding": base / "acceptance" / "acceptance_binding.json",
        "review_bundle": base / "bundles" / "approved_review_bundle.json",
        "review_bundle_binding": base / "bundles" / "approved_review_bundle.binding.json",
        "resolutions_dir": base / "resolutions",
        "revision_dir": base / "revision",
        "revision_directive": base / "revision" / "revision_directive.json",
        "revision_job": base / "revision" / "revision_job.json",
        "revision_operator_dir": base / "revision" / "operator",
        "revision_result": base / "revision" / "revision_result.json",
        "revised_review_job": base / "revision" / "revised_review_job.json",
    }
    return {key: relpath(root, value) for key, value in paths.items()}


def _require_derived_path(root: Path, supplied: str | Path | None, expected: str, label: str) -> Path:
    expected_path = resolve_under_root(root, expected, must_exist=False)
    if supplied is not None:
        supplied_path = resolve_under_root(root, supplied, must_exist=False)
        if supplied_path != expected_path:
            raise SystemExit(f"{label} is supervisor-derived and must be {expected}.")
    return expected_path


def _write_once_json(root: Path, path: str | Path, payload: dict[str, Any], schema: str | None = None, label: str = "artifact") -> str:
    resolved = resolve_under_root(root, path, must_exist=False)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    lock_path = resolved.with_name(f".{resolved.name}.create.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if resolved.exists():
            existing = load_json(resolved, label)
            if _canonical_sha256(existing) != _canonical_sha256(payload):
                raise SystemExit(f"Refusing to mutate immutable {label}: {relpath(root, resolved)}")
            return relpath(root, resolved)
        if schema:
            supervisor_artifacts.write_json_validated(resolved, payload, schema, label)
        else:
            supervisor_artifacts.write_json_artifact(root, resolved, payload)
        return relpath(root, resolved)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _write_once_text(root: Path, path: str | Path, content: str, label: str = "artifact") -> str:
    resolved = resolve_under_root(root, path, must_exist=False)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    lock_path = resolved.with_name(f".{resolved.name}.create.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if resolved.exists():
            if resolved.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Refusing to mutate immutable {label}: {relpath(root, resolved)}")
            return relpath(root, resolved)
        write_text(resolved, content)
        return relpath(root, resolved)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _job_payload(root: Path, job: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(job, dict):
        payload = dict(job)
    else:
        payload = load_json(resolve_under_root(root, job, must_exist=True), "review job")
    if not isinstance(payload, dict):
        raise SystemExit("Review job must be a JSON object.")
    return payload


def _artifact_paths_from_value(value: Any) -> list[str]:
    values: list[Any]
    if isinstance(value, dict):
        values = list(value.values())
    elif isinstance(value, list):
        values = list(value)
    elif value is None:
        values = []
    else:
        values = [value]
    paths: list[str] = []
    for item in values:
        if isinstance(item, str) and item:
            paths.append(item)
        elif isinstance(item, dict):
            path = item.get("path") or item.get("artifact_path")
            if isinstance(path, str) and path:
                paths.append(path)
    return paths


def _artifact_manifest(root: Path, paths: Sequence[str]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for raw in sorted(set(paths)):
        resolved = resolve_under_root(root, raw, must_exist=True)
        if not resolved.is_file():
            raise SystemExit(f"Reviewed artifact must be a file: {raw}")
        records.append(
            {
                "path": relpath(root, resolved),
                "sha256": sha256_file(resolved),
                "bytes": resolved.stat().st_size,
            }
        )
    return {
        "schema_version": "responses_runner_v2.reviewed_artifact_manifest.v1",
        "artifacts": records,
        "aggregate_sha256": _canonical_sha256(records),
    }


def _cycle_review_input(
    *,
    root: Path,
    session: dict[str, Any],
    cycle: dict[str, Any],
    paths: dict[str, str],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    job_path = resolve_under_root(root, paths["frozen_job"], must_exist=True)
    manifest_path = resolve_under_root(root, paths["reviewed_artifact_manifest"], must_exist=True)
    payload = {
        "schema_version": REVIEW_INPUT_SCHEMA_VERSION,
        "created_at": cycle["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "review_cycle_id": cycle["review_cycle_id"],
        "review_kind": cycle["review_kind"],
        "frozen_job_path": relpath(root, job_path),
        "frozen_job_sha256": sha256_file(job_path),
        "frozen_job_bytes": job_path.stat().st_size,
        "job": load_json(job_path, "frozen review job"),
        "reviewed_artifact_manifest_path": relpath(root, manifest_path),
        "reviewed_artifact_manifest_sha256": sha256_file(manifest_path),
        "reviewed_artifact_manifest_bytes": manifest_path.stat().st_size,
        "reviewed_artifacts": manifest["artifacts"],
    }
    _write_once_json(
        root,
        paths["review_input"],
        payload,
        "review_input.v2.schema.json",
        "cycle review input",
    )
    return payload


def _scaffold_subject(root: Path, session: dict[str, Any], job: dict[str, Any]) -> tuple[str | None, str | None]:
    requested = job.get("scaffold_version_id")
    candidates = session.get("scaffold_versions") or []
    selected = next((item for item in candidates if item.get("version_id") == requested), None) if requested else (candidates[-1] if candidates else None)
    if not isinstance(selected, dict):
        return None, None
    manifest_path = selected.get("hash_manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path:
        raise SystemExit("Bound scaffold version is missing its hash manifest.")
    resolved = resolve_under_root(root, manifest_path, must_exist=True)
    _verify_hash_manifest(root, resolved)
    return str(selected["version_id"]), sha256_file(resolved)


def _verify_hash_manifest(root: Path, manifest_path: Path) -> None:
    manifest = load_json(manifest_path, "hash manifest")
    for item in manifest.get("files", []):
        resolved = resolve_under_root(root, item["path"], must_exist=True)
        if resolved.stat().st_size != item.get("bytes") or sha256_file(resolved) != item.get("sha256"):
            raise SystemExit(f"Hash-manifest artifact changed: {item['path']}")


def _registered_run_subject(
    root: Path,
    session: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, str]:
    """Derive a live review subject from registered v2 run evidence, never caller hashes."""

    run_id = job.get("run_id")
    stage_id = job.get("stage_id")
    if not isinstance(run_id, str) or not run_id or not isinstance(stage_id, str) or not stage_id:
        raise SystemExit("Stage, final, and recovery reviews require non-null run_id and stage_id.")
    matches = [item for item in session.get("runs", []) if item.get("run_id") == run_id]
    if len(matches) != 1:
        raise SystemExit("Review subject requires one previously registered v2 run.")
    registered = _require_registered_run(root, session, matches[0]["run_dir"], require_v2=True)
    run_dir = resolve_under_root(root, registered["run_dir"], must_exist=True)
    manifest_path = resolve_under_root(root, registered["run_manifest_path"], must_exist=True)
    if manifest_path != run_dir / "run_manifest.json":
        raise SystemExit("Registered run manifest must be the canonical run-local manifest.")
    manifest = runner_artifacts.load_run_manifest(root, run_dir)
    if manifest.get("run_id") != run_id:
        raise SystemExit("Registered run_id no longer matches its v2 run manifest.")
    contract_path = resolve_under_root(root, manifest.get("run_contract_path"), must_exist=True)
    if contract_path != run_dir / "run_contract.json":
        raise SystemExit("Run manifest does not bind the canonical run-local contract.")
    if sha256_file(contract_path) != manifest.get("run_contract_sha256"):
        raise SystemExit("Run-contract hash does not match the registered run manifest.")
    contract = load_and_verify_run_contract(root=root, run_dir=run_dir)
    if contract.get("workflow_id") != manifest.get("workflow_id"):
        raise SystemExit("Run contract workflow_id does not match the run manifest.")
    if contract.get("workflow_asset_set_hash") != manifest.get("workflow_asset_set_hash"):
        raise SystemExit("Run contract workflow asset set does not match the run manifest.")
    workflow_members = [item for item in contract.get("members", []) if item.get("role") == "workflow_manifest"]
    if len(workflow_members) != 1:
        raise SystemExit("Run contract must bind exactly one workflow manifest member.")
    workflow_path = resolve_under_root(root, manifest.get("workflow_manifest_path"), must_exist=True)
    workflow_member = workflow_members[0]
    if resolve_under_root(root, workflow_member.get("path"), must_exist=True) != workflow_path:
        raise SystemExit("Run contract and run manifest bind different workflow manifests.")
    workflow_hash = sha256_file(workflow_path)
    if workflow_hash != workflow_member.get("sha256") or workflow_hash != manifest.get("workflow_manifest_sha256"):
        raise SystemExit("Workflow manifest hash does not match the frozen run evidence.")
    stage_matches = [item for item in manifest.get("stages", []) if item.get("stage_id") == stage_id]
    if len(stage_matches) != 1:
        raise SystemExit("Review stage_id is not uniquely present in the registered run manifest.")
    stage = stage_matches[0]
    attempt_id = stage.get("current_attempt_id")
    attempts = [item for item in stage.get("attempts", []) if item.get("attempt_id") == attempt_id]
    if not isinstance(attempt_id, str) or not attempt_id or len(attempts) != 1:
        raise SystemExit("Review stage has no unique current v2 attempt.")
    attempt = attempts[0]
    attempt_dir = resolve_under_root(root, attempt.get("attempt_dir"), must_exist=True)
    checkpoint_values = [value for value in (attempt.get("checkpoint_path"), stage.get("checkpoint_path")) if value]
    if not checkpoint_values:
        raise SystemExit("Current run attempt has no checkpoint binding.")
    checkpoint_paths = {resolve_under_root(root, value, must_exist=True) for value in checkpoint_values}
    if len(checkpoint_paths) != 1:
        raise SystemExit("Current attempt and stage summary bind different checkpoints.")
    checkpoint_path = checkpoint_paths.pop()
    if checkpoint_path.parent != attempt_dir:
        raise SystemExit("Current checkpoint is not inside the registered attempt directory.")
    checkpoint_sha = sha256_file(checkpoint_path)
    declared_hashes = [value for value in (attempt.get("checkpoint_sha256"), stage.get("checkpoint_sha256")) if value]
    if any(value != checkpoint_sha for value in declared_hashes):
        raise SystemExit("Current checkpoint hash does not match the run manifest.")
    checkpoint = load_json(checkpoint_path, "current stage checkpoint")
    supervisor_artifacts.validate_against_schema(checkpoint, "stage_checkpoint.v2.schema.json", "current stage checkpoint")
    expected_checkpoint = {
        "schema_version": "responses_runner_v2.stage_checkpoint.v2",
        "run_id": run_id,
        "stage_id": stage_id,
        "attempt_id": attempt_id,
        "attempt_dir": relpath(root, attempt_dir),
    }
    mismatches = [key for key, value in expected_checkpoint.items() if checkpoint.get(key) != value]
    if mismatches:
        raise SystemExit(f"Current checkpoint binding mismatch: {', '.join(mismatches)}")
    derived = {
        "workflow_id": str(manifest["workflow_id"]),
        "workflow_asset_sha256": str(contract["workflow_asset_set_hash"]),
        "run_id": run_id,
        "stage_id": stage_id,
        "attempt_id": attempt_id,
        "attempt_dir": relpath(root, attempt_dir),
        "checkpoint_path": relpath(root, checkpoint_path),
        "checkpoint_sha256": checkpoint_sha,
        "run_manifest_path": relpath(root, manifest_path),
        "run_manifest_sha256": sha256_file(manifest_path),
        "run_contract_path": relpath(root, contract_path),
        "run_contract_sha256": sha256_file(contract_path),
    }
    for key in ("workflow_id", "run_id", "stage_id", "attempt_id", "workflow_asset_sha256"):
        if job.get(key) is not None and job.get(key) != derived[key]:
            raise SystemExit(f"Review job {key} does not match registered run evidence.")
    for keys, derived_key in (
        (("checkpoint_path", "stage_checkpoint"), "checkpoint_path"),
        (("run_manifest", "run_manifest_path"), "run_manifest_path"),
        (("run_contract", "run_contract_path"), "run_contract_path"),
    ):
        for key in keys:
            if job.get(key) is not None and resolve_under_root(root, job[key], must_exist=True) != resolve_under_root(root, derived[derived_key], must_exist=True):
                raise SystemExit(f"Review job {key} does not match registered run evidence.")
    return derived


def _freeze_cycle_subject(
    *,
    root: Path,
    session: dict[str, Any],
    session_path: Path,
    cycle: dict[str, Any],
    job: dict[str, Any] | str | Path,
) -> dict[str, Any]:
    payload = _job_payload(root, job)
    paths = cycle["derived_paths"]
    existing_subject_path = resolve_under_root(root, paths["review_subject"], must_exist=False)
    if existing_subject_path.exists():
        existing_job = load_json(resolve_under_root(root, paths["frozen_job"], must_exist=True), "frozen review job")
        if _canonical_sha256(existing_job) != _canonical_sha256(payload):
            raise SystemExit("Review cycle job differs from the already frozen job.")
        subject = load_json(existing_subject_path, "review subject")
        supervisor_artifacts.validate_against_schema(subject, "review_subject.schema.json", "review subject")
        if subject.get("supervisor_session_id") != session["supervisor_session_id"] or subject.get("review_cycle_id") != cycle["review_cycle_id"] or subject.get("review_kind") != cycle["review_kind"]:
            raise SystemExit("Existing review subject identity does not match this cycle.")
        cycle["subject_path"] = paths["review_subject"]
        cycle["subject_sha256"] = sha256_file(existing_subject_path)
        cycle["subject_id"] = subject["subject_id"]
        return subject
    run_subject: dict[str, str] = {}
    if cycle["review_kind"] != "scaffold":
        run_subject = _registered_run_subject(root, session, payload)
    reviewed_paths = _artifact_paths_from_value(payload.get("reviewed_artifacts"))
    reviewed_paths.extend(_artifact_paths_from_value(cycle.get("artifacts_reviewed")))
    for key in ("stage_outcome", "run_manifest", "checkpoint_path", "stage_checkpoint"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            reviewed_paths.append(value)
    reviewed_paths.extend(
        run_subject[key]
        for key in ("run_manifest_path", "run_contract_path", "checkpoint_path")
        if key in run_subject
    )
    final_packet_draft_path: str | None = None
    final_packet_draft_sha256: str | None = None
    if cycle["review_kind"] == "final_packet":
        draft_value = payload.get("final_packet_draft")
        if not isinstance(draft_value, str) or not draft_value:
            raise SystemExit("Final-packet review requires final_packet_draft.")
        draft_path = resolve_under_root(root, draft_value, must_exist=True)
        if not draft_path.is_file():
            raise SystemExit("Final-packet draft must be a JSON file.")
        draft = load_json(draft_path, "final-packet draft")
        if draft.get("schema_version") not in {
            FINAL_IMPLEMENTATION_BUNDLE_SCHEMA_VERSION,
            "responses_runner_v2.final_delivery_bundle.v1",
        }:
            raise SystemExit("Final-packet draft has an unsupported schema_version.")
        forbidden = (
            {"agent_reviews", "consolidation", "operator_acceptance"}
            if draft["schema_version"] == FINAL_IMPLEMENTATION_BUNDLE_SCHEMA_VERSION
            else {"reviews", "operator_acceptance"}
        )
        if forbidden & set(draft):
            raise SystemExit("Final-packet draft must contain only the non-cyclic bundle body.")
        final_packet_draft_path = relpath(root, draft_path)
        final_packet_draft_sha256 = sha256_file(draft_path)
        reviewed_paths.append(final_packet_draft_path)
    manifest = _artifact_manifest(root, reviewed_paths)
    _write_once_json(root, paths["frozen_job"], payload, label="frozen review job")
    _write_once_json(root, paths["reviewed_artifact_manifest"], manifest, label="reviewed artifact manifest")
    _cycle_review_input(
        root=root,
        session=session,
        cycle=cycle,
        paths=paths,
        manifest=manifest,
    )
    scaffold_version_id, scaffold_sha256 = _scaffold_subject(root, session, payload)
    subject_tuple = {
        "supervisor_session_id": session["supervisor_session_id"],
        "review_cycle_id": cycle["review_cycle_id"],
        "review_kind": cycle["review_kind"],
        "scaffold_version_id": scaffold_version_id,
        "scaffold_sha256": scaffold_sha256,
        "workflow_id": run_subject.get("workflow_id"),
        "workflow_asset_sha256": run_subject.get("workflow_asset_sha256"),
        "run_id": run_subject.get("run_id"),
        "stage_id": run_subject.get("stage_id"),
        "attempt_id": run_subject.get("attempt_id"),
        "attempt_dir": run_subject.get("attempt_dir"),
        "checkpoint_path": run_subject.get("checkpoint_path"),
        "checkpoint_sha256": run_subject.get("checkpoint_sha256"),
        "run_manifest_path": run_subject.get("run_manifest_path"),
        "run_manifest_sha256": run_subject.get("run_manifest_sha256"),
        "run_contract_path": run_subject.get("run_contract_path"),
        "run_contract_sha256": run_subject.get("run_contract_sha256"),
        "reviewed_artifact_manifest_path": paths["reviewed_artifact_manifest"],
        "reviewed_artifact_manifest_sha256": sha256_file(resolve_under_root(root, paths["reviewed_artifact_manifest"], must_exist=True)),
        "frozen_job_path": paths["frozen_job"],
        "frozen_job_sha256": sha256_file(resolve_under_root(root, paths["frozen_job"], must_exist=True)),
        "review_input_path": paths["review_input"],
        "review_input_sha256": sha256_file(resolve_under_root(root, paths["review_input"], must_exist=True)),
        "final_packet_draft_path": final_packet_draft_path,
        "final_packet_draft_sha256": final_packet_draft_sha256,
    }
    subject = {
        "schema_version": REVIEW_SUBJECT_SCHEMA_VERSION,
        "created_at": cycle["created_at"],
        "subject_id": _canonical_sha256(subject_tuple),
        **subject_tuple,
    }
    _write_once_json(root, paths["review_subject"], subject, "review_subject.schema.json", "review subject")
    if cycle.get("subject_sha256") and cycle["subject_sha256"] != sha256_file(resolve_under_root(root, paths["review_subject"], must_exist=True)):
        raise SystemExit("Review cycle subject is immutable and cannot be replaced.")
    cycle["subject_path"] = paths["review_subject"]
    cycle["subject_sha256"] = sha256_file(resolve_under_root(root, paths["review_subject"], must_exist=True))
    cycle["subject_id"] = subject["subject_id"]
    return subject


def _load_cycle_subject(root: Path, cycle: dict[str, Any]) -> dict[str, Any]:
    path = cycle.get("subject_path")
    expected = cycle.get("subject_sha256")
    if not isinstance(path, str) or not isinstance(expected, str):
        raise SystemExit("Legacy unbound review cycle is read-only evidence and cannot progress. Start a new bound review cycle.")
    resolved = resolve_under_root(root, path, must_exist=True)
    if sha256_file(resolved) != expected:
        raise SystemExit("Review cycle subject hash mismatch.")
    payload = load_json(resolved, "review subject")
    supervisor_artifacts.validate_against_schema(payload, "review_subject.schema.json", "review subject")
    return payload


def _verify_subject_artifacts(root: Path, subject: dict[str, Any]) -> None:
    for path_key, hash_key, label in (
        ("frozen_job_path", "frozen_job_sha256", "frozen job"),
        ("reviewed_artifact_manifest_path", "reviewed_artifact_manifest_sha256", "reviewed artifact manifest"),
        ("review_input_path", "review_input_sha256", "review input"),
        ("final_packet_draft_path", "final_packet_draft_sha256", "final-packet draft"),
        ("checkpoint_path", "checkpoint_sha256", "checkpoint"),
        ("run_manifest_path", "run_manifest_sha256", "run manifest"),
        ("run_contract_path", "run_contract_sha256", "run contract"),
    ):
        path = subject.get(path_key)
        expected = subject.get(hash_key)
        if path is None and expected is None:
            continue
        if not isinstance(path, str) or not isinstance(expected, str):
            raise SystemExit(f"Review subject has incomplete {label} binding.")
        resolved = resolve_under_root(root, path, must_exist=True)
        if sha256_file(resolved) != expected:
            raise SystemExit(f"Review subject {label} hash mismatch.")
    manifest = load_json(resolve_under_root(root, subject["reviewed_artifact_manifest_path"], must_exist=True), "reviewed artifact manifest")
    for artifact in manifest.get("artifacts", []):
        resolved = resolve_under_root(root, artifact["path"], must_exist=True)
        if resolved.stat().st_size != artifact["bytes"] or sha256_file(resolved) != artifact["sha256"]:
            raise SystemExit(f"Reviewed artifact changed after subject freeze: {artifact['path']}")


def _default_policy() -> dict[str, Any]:
    return {
        "review_agents": {
            "operator_codex": {"command": "codex exec", "required": True},
            "codex_review_agent": {"command": "codex exec", "required": True, "read_only": True},
            "claude_review_agent": {"command": "claude -p", "required": True, "read_only": True},
        },
        "retry_budgets": {
            "failed_no_artifact": 1,
            "incomplete_output_limit": 0,
            "blocked_token_preflight": 0,
            "long_running_monitoring_anomaly_duplicate_submit": 0,
        },
        "monitoring": {
            "poll_interval_seconds": 300,
            "stale_after_seconds": 21600,
            "max_refresh_attempts": 288,
            "max_resume_attempts": 3,
            "no_duplicate_submit": True,
        },
        "human_pause_rules": {
            "post_clarification_pauses_are_exception_only": True,
            "require_trigger_artifact_decision_safe_continuation": True,
        },
        "read_only_enforcement_method": "workspace_snapshot_excluding_local_artifacts",
    }


def _default_model_defaults() -> dict[str, Any]:
    return {
        "primary": DEFAULT_PRIMARY_MODEL,
        "structural": DEFAULT_STRUCTURAL_MODEL,
        "primary_reasoning_mode": "pro",
        "prompt_cache_mode": "implicit",
        "prompt_cache_ttl": "30m",
        "max_output_tokens": 128000,
        "primary_reasoning_effort": "xhigh",
        "structural_reasoning_effort": "high",
        "primary_verbosity": "high",
        "structural_verbosity": "medium",
    }


def create_session(*, root: Path, clarified_task_brief: str | Path, summary: str, session_id: str | None = None) -> dict[str, Any]:
    """Create a supervisor session from an accepted clarified task brief."""

    brief_path = resolve_under_root(root, clarified_task_brief, must_exist=True)
    created_id, session_path = supervisor_artifacts.create_session_dir(root, session_id)
    now = runner_now().isoformat()
    session = {
        "schema_version": SUPERVISOR_SESSION_SCHEMA_VERSION,
        "supervisor_session_id": created_id,
        "created_at": now,
        "updated_at": now,
        "workspace_root": str(root.resolve()),
        "operator_boundary": OPERATOR_BOUNDARY,
        "status": "clarified",
        "current_phase": "scaffold",
        "clarified_task_brief": {
            "path": relpath(root, brief_path),
            "sha256": sha256_file(brief_path),
            "accepted_at": now,
            "summary": summary,
        },
        "policy": _default_policy(),
        "model_defaults": _default_model_defaults(),
        "retry_budget": {"failed_no_artifact": 1, "incomplete_output_limit_auto_progress": 0},
        "monitoring_policy": _default_policy()["monitoring"],
        "scaffold_versions": [],
        "dry_run_validations": [],
        "launch_reservations": [],
        "rerun_reservations": [],
        "runs": [],
        "stage_outcomes": [],
        "monitoring_events": [],
        "review_cycles": [],
        "review_agent_invocations": [],
        "consolidations": [],
        "operator_acceptance_records": [],
        "human_pauses": [],
        "archives": [],
        "approved_review_bundles": [],
        "final_bundle": None,
        "validation_results": [],
        "command_log": [],
        "errors": [],
    }
    return _write_session(root, session_path, session)


def stage_scaffold(*, root: Path, session_ref: str | Path, scaffold_path: str | Path, created_by: str = "operator_codex") -> dict[str, Any]:
    """Copy a scaffold into the supervisor session and hash its contents."""

    session, session_path = _load_session_and_path(root, session_ref)
    version_id = normalize_slug(f"scaffold_{len(session['scaffold_versions']) + 1:03d}")
    destination = session_path / "scaffolds" / version_id / "source"
    staged_path = supervisor_artifacts.copy_into_scaffold_version(root, scaffold_path, destination)
    hash_manifest_path = supervisor_artifacts.hash_manifest(root, destination, session_path / "scaffolds" / version_id / "hash_manifest.json")
    record = {
        "version_id": version_id,
        "path": staged_path,
        "hash_manifest_path": hash_manifest_path,
        "created_at": runner_now().isoformat(),
        "created_by": created_by,
        "dry_run_artifacts": [],
        "approval_status": "staged",
    }
    session["scaffold_versions"].append(record)
    session["status"] = "scaffold_staged"
    session["current_phase"] = "scaffold_review"
    _write_session(root, session_path, session)
    return record


def _render_scaffold_examination_markdown(examination: dict[str, Any]) -> str:
    lines = [
        "# Scaffold Examination",
        "",
        f"- examination_id: {examination['examination_id']}",
        f"- status: {examination['status']}",
        f"- workflow_id: {examination.get('workflow_id')}",
        f"- stage_count: {len(examination.get('stages', []))}",
        "",
        "## Summary",
        "",
        examination["summary"],
        "",
        "## Blocking Issues",
        "",
    ]
    blocking = examination.get("blocking_issues", [])
    if not blocking:
        lines.append("None.")
    for issue in blocking:
        lines.append(f"- {issue['issue_id']}: {issue['description']}")
    lines.extend(["", "## Non-Blocking Findings", ""])
    findings = examination.get("non_blocking_findings", [])
    if not findings:
        lines.append("None.")
    for finding in findings:
        lines.append(f"- {finding['finding_id']}: {finding['description']}")
    lines.extend(["", "## Stages", ""])
    for stage in examination.get("stages", []):
        lines.append(
            f"- {stage['stage_number']}. {stage['stage_id']} "
            f"({stage['gate']}, tools={stage['tool_profile'].get('tool_count', 0)}, "
            f"attachments={stage['input_manifest'].get('aggregate_file_count', 0)})"
        )
    return "\n".join(lines).rstrip() + "\n"


def _schema_keyword_hits(value: Any, *, path: str = "$") -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            child_path = f"{path}.{key}"
            if key in UNSUPPORTED_OUTPUT_SCHEMA_KEYWORDS:
                hits.append({"path": child_path, "keyword": key})
            hits.extend(_schema_keyword_hits(nested, path=child_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            hits.extend(_schema_keyword_hits(nested, path=f"{path}[{index}]"))
    return hits


def _entry_count_and_bytes(entries: list[dict[str, Any]]) -> tuple[int, int]:
    count = 0
    byte_count = 0
    for entry in entries:
        resolved = entry.get("resolved") if isinstance(entry, dict) else None
        if not isinstance(resolved, dict):
            continue
        count += int(resolved.get("aggregate_file_count") or 0)
        byte_count += int(resolved.get("aggregate_bytes") or 0)
    return count, byte_count


def _append_issue(
    issues: list[dict[str, Any]],
    *,
    issue_id: str,
    severity: str,
    description: str,
    evidence: list[str],
    affected_artifacts: list[str],
) -> None:
    issues.append(
        {
            "issue_id": normalize_slug(issue_id),
            "severity": severity,
            "description": description,
            "evidence": evidence,
            "affected_artifacts": affected_artifacts,
        }
    )


def _append_finding(
    findings: list[dict[str, Any]],
    *,
    finding_id: str,
    description: str,
    evidence: list[str],
    affected_artifacts: list[str],
) -> None:
    findings.append(
        {
            "finding_id": normalize_slug(finding_id),
            "description": description,
            "evidence": evidence,
            "affected_artifacts": affected_artifacts,
        }
    )


def _profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    tools = profile.get("tools")
    if not isinstance(tools, list):
        tools = []
    tool_types = []
    for tool in tools:
        if isinstance(tool, dict):
            tool_types.append(str(tool.get("type") or "unknown"))
        else:
            tool_types.append(type(tool).__name__)
    return {
        "tool_count": len(tools),
        "tool_types": tool_types,
        "parallel_tool_calls": profile.get("parallel_tool_calls"),
        "max_tool_calls": profile.get("max_tool_calls"),
    }


def examine_scaffold(
    *,
    root: Path,
    session_ref: str | Path,
    workflow_file: str | Path,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Statically examine a staged workflow scaffold before any executable Stage 1 dry-run."""

    session, session_path = _load_session_and_path(root, session_ref)
    examination_id = normalize_slug(f"scaffold_examination_{len(session['validation_results']) + 1:03d}")
    output_json_path = resolve_under_root(
        root,
        output or (session_path / "examinations" / f"{examination_id}.json"),
        must_exist=False,
    )
    output_md_path = output_json_path.with_suffix(".md")
    command = [
        "python3",
        "automation/run_responses_supervisor_v2.py",
        "examine-scaffold",
        "--root",
        str(root),
        "--session",
        str(session_ref),
        "--workflow-file",
        str(workflow_file),
    ]
    if output is not None:
        command.extend(["--output", str(output)])
    blocking_issues: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    workflow_summary: dict[str, Any] = {}
    workflow_id: str | None = None
    started_at = runner_now().isoformat()
    linted_markdown_paths: set[str] = set()

    def lint_markdown(path: Path, *, issue_scope: str) -> None:
        if path.suffix.lower() not in {".md", ".markdown"}:
            return
        relative = relpath(root, path)
        if relative in linted_markdown_paths:
            return
        linted_markdown_paths.add(relative)
        for violation in validate_commonmark_fences(
            path.read_text(encoding="utf-8", errors="replace")
        ):
            _append_issue(
                blocking_issues,
                issue_id=normalize_slug(
                    f"{issue_scope}_commonmark_{violation['rule_id']}_{violation.get('line') or 0}"
                ),
                severity="blocking",
                description="Markdown contains an unclosed CommonMark fenced code block.",
                evidence=[str(violation["message"])],
                affected_artifacts=[relative],
            )

    try:
        workflow_path = resolve_under_root(root, workflow_file, must_exist=True)
        workflow_payload = load_json(workflow_path, "workflow manifest")
        try:
            workflow_schema = "workflow_manifest.v2.schema.json" if workflow_payload.get("schema_version") == "responses_runner_v2.workflow_manifest.v2" else "workflow_manifest.schema.json"
            supervisor_artifacts.validate_against_schema(workflow_payload, workflow_schema, "workflow manifest")
        except supervisor_artifacts.SchemaValidationError as exc:
            _append_issue(
                blocking_issues,
                issue_id="workflow_manifest_schema_validation_failed",
                severity="blocking",
                description="Workflow manifest does not conform to the committed runner schema.",
                evidence=[str(exc)],
                affected_artifacts=[relpath(root, workflow_path)],
            )
        workflow = load_workflow_definition(workflow_file, root=root)
        workflow_id = workflow.workflow_id
        workflow_summary = {
            "workflow_id": workflow.workflow_id,
            "workflow_name": workflow.workflow_name,
            "workflow_mode": workflow.workflow_mode,
            "assurance_profile": workflow.assurance_profile,
            "workflow_manifest_path": relpath(root, workflow.workflow_file),
            "operator_requirements": workflow.operator_requirements,
            "shared_instructions_path": relpath(root, workflow.shared_instructions_path),
            "model_roles": {
                role_name: {
                    "model": profile.model,
                    "reasoning_effort": profile.reasoning_effort,
                    "reasoning_mode": profile.reasoning_mode,
                    "verbosity": profile.verbosity,
                    "prompt_cache_retention": profile.prompt_cache_retention,
                    "prompt_cache_mode": profile.prompt_cache_mode,
                    "prompt_cache_ttl": profile.prompt_cache_ttl,
                }
                for role_name, profile in workflow.model_roles.items()
            },
        }
        lint_markdown(workflow.shared_instructions_path, issue_scope="shared_instructions")

        expected_models = _default_model_defaults()
        primary = workflow.model_roles.get("primary_generation")
        structural = workflow.model_roles.get("structural_processing")
        primary_mismatches = [] if primary is not None else ["missing primary_generation role"]
        if primary is not None:
            for field_name, actual, expected in (
                ("model", primary.model, expected_models["primary"]),
                ("reasoning_effort", primary.reasoning_effort, expected_models["primary_reasoning_effort"]),
                ("reasoning_mode", primary.reasoning_mode, expected_models["primary_reasoning_mode"]),
                ("verbosity", primary.verbosity, expected_models["primary_verbosity"]),
                ("prompt_cache_mode", primary.prompt_cache_mode, expected_models["prompt_cache_mode"]),
                ("prompt_cache_ttl", primary.prompt_cache_ttl, expected_models["prompt_cache_ttl"]),
            ):
                if actual != expected:
                    primary_mismatches.append(f"{field_name}: expected {expected!r}, got {actual!r}")
        if primary_mismatches:
            _append_issue(
                blocking_issues,
                issue_id="primary_model_posture_mismatch",
                severity="blocking",
                description="Workflow primary_generation model does not match supervisor model posture.",
                evidence=primary_mismatches,
                affected_artifacts=[relpath(root, workflow.workflow_file)],
            )
        structural_mismatches = [] if structural is not None else ["missing structural_processing role"]
        if structural is not None:
            for field_name, actual, expected in (
                ("model", structural.model, expected_models["structural"]),
                ("reasoning_mode", structural.reasoning_mode, "standard"),
                ("verbosity", structural.verbosity, expected_models["structural_verbosity"]),
                ("prompt_cache_mode", structural.prompt_cache_mode, expected_models["prompt_cache_mode"]),
                ("prompt_cache_ttl", structural.prompt_cache_ttl, expected_models["prompt_cache_ttl"]),
            ):
                if actual != expected:
                    structural_mismatches.append(f"{field_name}: expected {expected!r}, got {actual!r}")
            if structural.reasoning_effort not in {"high", "medium"}:
                structural_mismatches.append(
                    "reasoning_effort: expected 'high' or 'medium', "
                    f"got {structural.reasoning_effort!r}"
                )
        if structural_mismatches:
            _append_issue(
                blocking_issues,
                issue_id="structural_model_posture_mismatch",
                severity="blocking",
                description="Workflow structural_processing model does not match supervisor model posture.",
                evidence=structural_mismatches,
                affected_artifacts=[relpath(root, workflow.workflow_file)],
            )

        terminal_stages = [stage for stage in workflow.stages if stage.gate.value == "terminal"]
        if len(terminal_stages) != 1 or terminal_stages[0] != workflow.stages[-1]:
            _append_issue(
                blocking_issues,
                issue_id="terminal_stage_shape_invalid",
                severity="blocking",
                description="A supervised scaffold should have exactly one terminal stage and it should be the final stage.",
                evidence=[f"terminal_stage_ids={[stage.stage_id for stage in terminal_stages]}"],
                affected_artifacts=[relpath(root, workflow.workflow_file)],
            )

        for stage in workflow.stages[:-1]:
            if stage.gate.value != "review_required":
                _append_issue(
                    blocking_issues,
                    issue_id=f"{stage.stage_id}_missing_review_gate",
                    severity="blocking",
                    description="Non-terminal stages must be review-gated before downstream progression.",
                    evidence=[f"Stage {stage.stage_id} gate is {stage.gate.value}."],
                    affected_artifacts=[relpath(root, workflow.workflow_file)],
                )

        for index, stage in enumerate(workflow.stages):
            task_text = stage.task_path.read_text(encoding="utf-8")
            lint_markdown(stage.task_path, issue_scope=f"{stage.stage_id}_task")
            if stage.stage_instructions_path is not None:
                lint_markdown(
                    stage.stage_instructions_path,
                    issue_scope=f"{stage.stage_id}_instructions",
                )
            word_count = len(task_text.split())
            if word_count < 80:
                _append_finding(
                    findings,
                    finding_id=f"{stage.stage_id}_short_prompt",
                    description="Stage prompt is unusually short for a high-stakes scaffold and should be reviewed for substantive completeness.",
                    evidence=[f"Stage {stage.stage_id} prompt has {word_count} words."],
                    affected_artifacts=[relpath(root, stage.task_path)],
                )

            raw_manifest = load_json(stage.input_manifest_path, "input manifest")
            try:
                supervisor_artifacts.validate_against_schema(raw_manifest, "input_manifest.schema.json", f"{stage.stage_id} input manifest")
            except supervisor_artifacts.SchemaValidationError as exc:
                _append_issue(
                    blocking_issues,
                    issue_id=f"{stage.stage_id}_input_manifest_schema_validation_failed",
                    severity="blocking",
                    description="Stage input manifest does not conform to the committed runner schema.",
                    evidence=[str(exc)],
                    affected_artifacts=[relpath(root, stage.input_manifest_path)],
                )
            static_manifest = load_input_manifest(stage.input_manifest_path, root=root)
            resolved_manifest = attachments.resolve_stage_input_manifest(
                root=root,
                workflow_id=workflow.workflow_id,
                stage_id=stage.stage_id,
                run_id=f"{examination_id}_{stage.stage_id}",
                manifest_id=f"{workflow.workflow_id}.{stage.stage_id}.scaffold_examination",
                description=str(static_manifest.get("description") or ""),
                primary_job_inputs=static_manifest["primary_job_inputs"],
                reviewed_handoff_inputs=static_manifest["reviewed_handoff_inputs"],
                attached_repository_files=static_manifest["attached_repository_files"],
                reference_context=static_manifest["reference_context"],
            )
            for duplicate_index, duplicate in enumerate(
                attachments.detect_authority_duplicates(resolved_manifest),
                start=1,
            ):
                _append_issue(
                    blocking_issues,
                    issue_id=f"{stage.stage_id}_authority_duplicate_{duplicate_index}",
                    severity="blocking",
                    description=(
                        "The same attachment is present under more than one authority role "
                        "without an explicit precedence contract."
                    ),
                    evidence=[
                        f"duplicate_by={duplicate['duplicate_by']}",
                        f"authorities={','.join(duplicate['authorities'])}",
                    ],
                    affected_artifacts=sorted(
                        {
                            str(item["path"])
                            for item in duplicate["occurrences"]
                            if isinstance(item, dict) and item.get("path")
                        }
                    ),
                )
            for field_name in (
                "primary_job_inputs",
                "reviewed_handoff_inputs",
                "attached_repository_files",
                "reference_context",
            ):
                for entry in resolved_manifest[field_name]:
                    for expanded in entry.get("resolved", {}).get("expanded_paths", []):
                        expanded_path = expanded.get("path")
                        if isinstance(expanded_path, str):
                            lint_markdown(
                                resolve_under_root(root, expanded_path, must_exist=True),
                                issue_scope=f"{stage.stage_id}_input",
                            )
            role_counts: dict[str, dict[str, int]] = {}
            aggregate_file_count = 0
            aggregate_bytes = 0
            for field_name in ("primary_job_inputs", "reviewed_handoff_inputs", "attached_repository_files", "reference_context"):
                count, byte_count = _entry_count_and_bytes(resolved_manifest[field_name])
                role_counts[field_name] = {"file_count": count, "bytes": byte_count}
                aggregate_file_count += count
                aggregate_bytes += byte_count

            profile = load_tool_profile(stage.tool_profile_path, root=root) if stage.tool_profile_path else {}
            sidecar_summary = None
            if stage.output.sidecar is not None:
                sidecar_schema = load_schema_json(stage.output.sidecar.schema_path, root=root)
                keyword_hits = _schema_keyword_hits(sidecar_schema)
                if keyword_hits:
                    _append_issue(
                        blocking_issues,
                        issue_id=f"{stage.stage_id}_sidecar_schema_unsupported_keywords",
                        severity="blocking",
                        description="Sidecar schema uses unsupported conservative keywords for this runner lane.",
                        evidence=[f"{hit['path']} uses {hit['keyword']}" for hit in keyword_hits],
                        affected_artifacts=[relpath(root, stage.output.sidecar.schema_path)],
                    )
                sidecar_summary = {
                    "schema_file": stage.output.sidecar.schema_file,
                    "schema_name": stage.output.sidecar.schema_name,
                    "schema_path": relpath(root, stage.output.sidecar.schema_path),
                    "unsupported_keyword_hits": keyword_hits,
                }
            elif stage.output.schema_path is not None:
                direct_schema = load_schema_json(stage.output.schema_path, root=root)
                keyword_hits = _schema_keyword_hits(direct_schema)
                if keyword_hits:
                    _append_issue(
                        blocking_issues,
                        issue_id=f"{stage.stage_id}_output_schema_unsupported_keywords",
                        severity="blocking",
                        description="Direct output schema uses unsupported conservative keywords for this runner lane.",
                        evidence=[f"{hit['path']} uses {hit['keyword']}" for hit in keyword_hits],
                        affected_artifacts=[relpath(root, stage.output.schema_path)],
                    )

            if index > 0 and not (
                stage.carry_forward.review_bundle_from_stage_id
                or stage.carry_forward.reference_context_from_stage_ids
            ):
                _append_finding(
                    findings,
                    finding_id=f"{stage.stage_id}_no_carry_forward",
                    description="Later stage has no carry-forward dependency; verify this is intentional and not a disconnected stage.",
                    evidence=[f"Stage {stage.stage_id} has no carry_forward review bundle or reference_context source."],
                    affected_artifacts=[relpath(root, workflow.workflow_file)],
                )

            stages.append(
                {
                    "stage_id": stage.stage_id,
                    "stage_number": stage.stage_number,
                    "title": stage.title,
                    "gate": stage.gate.value,
                    "task_file": stage.task_file,
                    "task_path": relpath(root, stage.task_path),
                    "prompt_word_count": word_count,
                    "input_manifest_file": stage.input_manifest_file,
                    "input_manifest_path": relpath(root, stage.input_manifest_path),
                    "input_manifest": {
                        "role_counts": role_counts,
                        "aggregate_file_count": aggregate_file_count,
                        "aggregate_bytes": aggregate_bytes,
                    },
                    "tool_profile_file": stage.tool_profile_file,
                    "tool_profile_path": relpath(root, stage.tool_profile_path) if stage.tool_profile_path else None,
                    "tool_profile": _profile_summary(profile),
                    "model_role": stage.model_role.value,
                    "max_output_tokens": stage.max_output_tokens,
                    "carry_forward": {
                        "reference_context_from_stage_ids": list(stage.carry_forward.reference_context_from_stage_ids),
                        "review_bundle_from_stage_id": stage.carry_forward.review_bundle_from_stage_id,
                        "review_bundle_include_response_artifact_json": stage.carry_forward.review_bundle_include_response_artifact_json,
                    },
                    "output": {
                        "primary_format": stage.output.primary_format,
                        "schema_file": stage.output.schema_file,
                        "schema_name": stage.output.schema_name,
                        "sidecar": sidecar_summary,
                    },
                }
            )

        pack_root = workflow_path.parent.parent if workflow_path.parent.name == "workflows" else workflow_path.parent
        tools_root = pack_root / "tools"
        if tools_root.is_dir():
            referenced_tool_paths: set[Path] = set()
            for sibling_workflow in sorted(workflow_path.parent.glob("*.workflow.json")):
                sibling_payload = load_json(sibling_workflow, "workflow manifest for orphan-tool lint")
                for sibling_stage in sibling_payload.get("stages", []):
                    if not isinstance(sibling_stage, dict):
                        continue
                    raw_tool_path = sibling_stage.get("tool_profile_file")
                    if not isinstance(raw_tool_path, str) or not raw_tool_path:
                        continue
                    candidate = Path(raw_tool_path)
                    referenced_tool_paths.add(
                        (candidate if candidate.is_absolute() else sibling_workflow.parent / candidate).resolve()
                    )
            for tool_path in sorted(tools_root.rglob("*.json")):
                if tool_path.resolve() in referenced_tool_paths:
                    continue
                _append_finding(
                    findings,
                    finding_id=normalize_slug(f"orphan_tool_profile_{relpath(root, tool_path)}"),
                    description=(
                        "Tool profile is not referenced by any workflow in this pack; "
                        "confirm whether it should be archived."
                    ),
                    evidence=["Orphan-tool lint is advisory and never deletes files."],
                    affected_artifacts=[relpath(root, tool_path)],
                )
    except (SystemExit, supervisor_artifacts.SchemaValidationError) as exc:
        _append_issue(
            blocking_issues,
            issue_id="scaffold_static_load_failed",
            severity="blocking",
            description="Static scaffold examination could not load or validate the workflow scaffold.",
            evidence=[str(exc)],
            affected_artifacts=[str(workflow_file)],
        )

    status = "failed" if blocking_issues else "passed"
    summary = (
        "Static scaffold examination passed without constructing a Stage 1 request."
        if status == "passed"
        else "Static scaffold examination found blocking issues before Stage 1 request construction."
    )
    examination = {
        "schema_version": "responses_runner_v2.scaffold_examination.v1",
        "examination_id": examination_id,
        "created_at": runner_now().isoformat(),
        "started_at": started_at,
        "supervisor_session_id": session["supervisor_session_id"],
        "workflow_id": workflow_id,
        "status": status,
        "summary": summary,
        "workflow": workflow_summary,
        "stages": stages,
        "blocking_issues": blocking_issues,
        "non_blocking_findings": findings,
        "runtime_input_contract": workflow_summary.get("operator_requirements", {}),
        "constructed_stage_request": False,
        "requires_primary_job_input_for_examination": False,
        "command": command,
        "json_report_path": relpath(root, output_json_path),
        "markdown_report_path": relpath(root, output_md_path),
    }
    supervisor_artifacts.write_json_validated(
        output_json_path,
        examination,
        "scaffold_examination.schema.json",
        "scaffold examination",
    )
    write_text(output_md_path, _render_scaffold_examination_markdown(examination))

    validation_record = {
        "check_id": examination_id,
        "command_or_method": "static_scaffold_examination",
        "phase": "scaffold_review",
        "expected_result": "Task-pack scaffold loads, static stage inputs resolve, and stage design is reviewable before Stage 1 request construction.",
        "actual_result": summary,
        "status": status,
        "artifact_path": relpath(root, output_json_path),
        "markdown_report_path": relpath(root, output_md_path),
        "blocking_issue_count": len(blocking_issues),
        "non_blocking_finding_count": len(findings),
    }
    session["validation_results"].append(validation_record)
    if session["scaffold_versions"]:
        latest = session["scaffold_versions"][-1]
        latest.setdefault("examination_artifacts", []).append(validation_record)
        latest["approval_status"] = "blocked" if status == "failed" else "reviewing"
    session["status"] = "blocked" if status == "failed" else "scaffold_reviewing"
    session["current_phase"] = "scaffold_review"
    if status == "failed":
        session["errors"].append(
            {
                "error_id": examination_id,
                "severity": "blocking",
                "message": summary,
                "related_artifact": relpath(root, output_json_path),
                "recovery_action": "repair_scaffold",
            }
        )
    _write_session(root, session_path, session)
    return examination


def dry_run_scaffold(
    *,
    root: Path,
    session_ref: str | Path,
    workflow_file: str | Path,
    run_name: str = "supervisor-scaffold-dry-run",
    primary_job_inputs: Sequence[str] | None = None,
    reference_context: Sequence[str] | None = None,
    review_bundles: Sequence[str] | None = None,
    input_binding_file: str | Path | None = None,
    stage_id: str | None = None,
) -> dict[str, Any]:
    """Run the staged scaffold in executable dry-run mode and record validation."""

    primary_job_inputs = list(primary_job_inputs or [])
    reference_context = list(reference_context or [])
    review_bundles = list(review_bundles or [])
    session, session_path = _load_session_and_path(root, session_ref)
    output_root = Path(relpath(root, session_path / "dry_runs"))
    command = ["python3", "automation/run_responses_v2.py", "run", "--root", str(root), "--workflow-file", str(workflow_file)]
    for value in primary_job_inputs:
        command.extend(["--primary-job-input", value])
    for value in reference_context:
        command.extend(["--reference-context", value])
    for value in review_bundles:
        command.extend(["--review-bundle", value])
    if input_binding_file is not None:
        command.extend(["--input-binding-file", str(input_binding_file)])
    if stage_id:
        command.extend(["--stage", stage_id])
    command.append("--dry-run")
    started_at = runner_now().isoformat()
    status = "passed"
    exit_code = 0
    result: dict[str, Any] = {}
    error_message = None
    try:
        result = run_workflow(
            workflow_file=workflow_file,
            runtime=RuntimeOptions(
                run_name=run_name,
                output_root=output_root,
                dry_run=True,
                primary_job_inputs=primary_job_inputs,
                reference_context=reference_context,
                review_bundles=review_bundles,
                input_bindings=_supervisor_input_bindings(
                    root=root,
                    workflow_file=workflow_file,
                    input_binding_file=input_binding_file,
                ),
                stage_id=stage_id,
            ),
            root=root,
        )
    except SystemExit as exc:
        status = "failed"
        exit_code = 1
        error_message = str(exc)
    record = {
        "validation_id": normalize_slug(f"dry_run_{len(session['dry_run_validations']) + 1:03d}"),
        "command": command,
        "started_at": started_at,
        "completed_at": runner_now().isoformat(),
        "exit_code": exit_code,
        "status": status,
        "result": result,
        "error_message": error_message,
    }
    session["dry_run_validations"].append(record)
    if session["scaffold_versions"]:
        session["scaffold_versions"][-1]["dry_run_artifacts"].append(record)
        if status == "passed":
            session["scaffold_versions"][-1]["approval_status"] = "dry_run_passed"
    session["status"] = "scaffold_reviewing" if status == "passed" else "blocked"
    session["current_phase"] = "scaffold_review"
    if status != "passed":
        session["errors"].append(
            {
                "error_id": record["validation_id"],
                "severity": "blocking",
                "message": error_message or "Dry-run validation failed.",
                "related_artifact": str(workflow_file),
                "recovery_action": "repair_scaffold",
            }
        )
    _write_session(root, session_path, session)
    return record


def _new_review_cycle_record(
    *,
    root: Path,
    session_path: Path,
    review_cycle_id: str,
    review_kind: str,
    artifacts_reviewed: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if review_kind not in REVIEW_KINDS_REQUIRING_OPERATOR_PROVISIONAL:
        raise SystemExit(f"Unsupported review kind: {review_kind}")
    paths = _cycle_paths(root, session_path, review_cycle_id)
    return {
        "review_cycle_id": review_cycle_id,
        "review_kind": review_kind,
        "artifacts_reviewed": artifacts_reviewed or [],
        "operator_provisional_record": None,
        "review_agent_outputs": {},
        "review_gates": {},
        "consolidation": None,
        "quorum": None,
        "acceptance_record": None,
        "acceptance_binding": None,
        "blocker_resolutions": [],
        "subject_path": None,
        "subject_sha256": None,
        "subject_id": None,
        "derived_paths": paths,
        "acceptance_status": "pending",
        "created_at": runner_now().isoformat(),
    }


def _active_revision_reservation_owner(session: dict[str, Any], review_cycle_id: str) -> str | None:
    for source_cycle in session.get("review_cycles", []):
        revision = source_cycle.get("revision")
        reservation = (source_cycle.get("invocation_reservations") or {}).get("operator_revision")
        if not isinstance(revision, dict) or not isinstance(reservation, dict):
            continue
        reserved_id = reservation.get("intent", {}).get("recovery_context", {}).get("new_review_cycle_id")
        if reserved_id == review_cycle_id or revision.get("new_review_cycle_id") == review_cycle_id:
            return str(source_cycle["review_cycle_id"])
    return None


def create_review_cycle(*, root: Path, session_ref: str | Path, review_cycle_id: str, review_kind: str, artifacts_reviewed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Create an unreserved supervisor review cycle."""

    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _session_mutation_lock(session_path):
        session, session_path = _load_session_and_path(root, session_ref)
        owner = _active_revision_reservation_owner(session, review_cycle_id)
        if owner is not None:
            raise SystemExit(f"Review cycle ID {review_cycle_id} is reserved by active revision {owner}.")
        if any(cycle["review_cycle_id"] == review_cycle_id for cycle in session["review_cycles"]):
            raise SystemExit(f"Review cycle already exists: {review_cycle_id}")
        cycle = _new_review_cycle_record(
            root=root,
            session_path=session_path,
            review_cycle_id=review_cycle_id,
            review_kind=review_kind,
            artifacts_reviewed=artifacts_reviewed,
        )
        session["review_cycles"].append(cycle)
        _write_session(root, session_path, session)
        return cycle


def _find_cycle(session: dict[str, Any], review_cycle_id: str) -> dict[str, Any]:
    for cycle in session["review_cycles"]:
        if cycle["review_cycle_id"] == review_cycle_id:
            return cycle
    raise SystemExit(f"Unknown review cycle: {review_cycle_id}")


def _agent_job_sha256(payload: dict[str, Any]) -> str:
    return sha256_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def _validate_decision_binding(
    *,
    root: Path,
    decision_path: str | Path,
    session_id: str,
    cycle: dict[str, Any],
    actor_role: str,
    review_kind: str,
) -> dict[str, Any]:
    decision = _load_decision(root, decision_path, f"{actor_role} review")
    expected = {
        "supervisor_session_id": session_id,
        "review_cycle_id": cycle["review_cycle_id"],
        "actor_role": actor_role,
        "review_kind": review_kind,
    }
    mismatches = [key for key, value in expected.items() if decision.get(key) != value]
    if mismatches:
        raise SystemExit(f"Review decision identity mismatch for {actor_role}: {', '.join(mismatches)}")
    subject = _load_cycle_subject(root, cycle)
    if decision.get("status") == "succeeded":
        for key in ("workflow_id", "run_id", "stage_id"):
            if decision.get(key) != subject.get(key):
                raise SystemExit(f"Review decision {key} does not match the immutable cycle subject.")
    return decision


def _require_successful_operator_provisional(
    *,
    root: Path,
    session: dict[str, Any],
    cycle: dict[str, Any],
) -> dict[str, Any]:
    value = cycle.get("operator_provisional_record")
    gate = cycle.get("review_gates", {}).get("operator_codex")
    if not isinstance(value, str) or not value or not isinstance(gate, dict):
        raise SystemExit("Review cycle cannot progress without a successful operator provisional gate.")
    decision_path = resolve_under_root(root, value, must_exist=True)
    operator_dir = resolve_under_root(root, cycle["derived_paths"]["operator_dir"], must_exist=True)
    if not decision_path.is_relative_to(operator_dir):
        raise SystemExit("Operator provisional decision is outside its supervisor-derived directory.")
    gate_path = gate.get("decision_path")
    if not isinstance(gate_path, str) or resolve_under_root(root, gate_path, must_exist=True) != decision_path:
        raise SystemExit("Operator provisional path does not match its recorded gate.")
    if sha256_file(decision_path) != gate.get("decision_sha256"):
        raise SystemExit("Operator provisional decision hash changed after gate evaluation.")
    subject = _load_cycle_subject(root, cycle)
    expected_gate = {
        "gate_status": "passed",
        "transport_status": "passed",
        "schema_status": "passed",
        "subject_id": subject["subject_id"],
        "subject_sha256": cycle["subject_sha256"],
        "frozen_job_sha256": subject["frozen_job_sha256"],
        "job_sha256": subject["frozen_job_sha256"],
        "review_input_sha256": subject["review_input_sha256"],
        "reviewed_artifact_manifest_sha256": subject["reviewed_artifact_manifest_sha256"],
    }
    mismatches = [key for key, expected in expected_gate.items() if gate.get(key) != expected]
    if mismatches:
        raise SystemExit(f"Operator provisional gate binding mismatch: {', '.join(mismatches)}")
    decision = _validate_decision_binding(
        root=root,
        decision_path=decision_path,
        session_id=session["supervisor_session_id"],
        cycle=cycle,
        actor_role="operator_codex",
        review_kind=cycle["review_kind"],
    )
    if decision.get("status") != "succeeded":
        raise SystemExit("Operator provisional did not complete successfully.")
    return decision


def _verified_cycle_review_decisions(
    *,
    root: Path,
    session: dict[str, Any],
    cycle: dict[str, Any],
    subject: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Load only cycle-recorded reviewer outputs that still match their gates."""

    stored = cycle.get("review_agent_outputs") or {}
    expected_paths = {
        "operator_codex": cycle.get("operator_provisional_record"),
        "codex_review_agent": stored.get("codex_review_agent"),
        "claude_review_agent": stored.get("claude_review_agent"),
    }
    gates = cycle.get("review_gates") or {}
    decisions: dict[str, dict[str, Any]] = {}
    for role in REVIEW_ROLES:
        expected_path = expected_paths[role]
        gate = gates.get(role)
        if not isinstance(expected_path, str) or not expected_path or not isinstance(gate, dict):
            raise SystemExit(f"Review cycle is missing the supervisor-recorded {role} decision or gate.")
        if gate.get("actor_role") != role:
            raise SystemExit(f"{role} review gate has the wrong actor identity.")
        if gate.get("subject_id") != subject["subject_id"] or gate.get("subject_sha256") != cycle.get("subject_sha256"):
            raise SystemExit(f"{role} review gate does not match the immutable cycle subject.")

        decision_path = resolve_under_root(root, expected_path, must_exist=True)
        gate_decision_path = resolve_under_root(root, gate.get("decision_path"), must_exist=True)
        if decision_path != gate_decision_path:
            raise SystemExit(f"{role} cycle decision path does not match its review gate.")
        if sha256_file(decision_path) != gate.get("decision_sha256"):
            raise SystemExit(f"{role} review decision hash mismatch after invocation.")

        markdown_path = resolve_under_root(root, gate.get("markdown_path"), must_exist=True)
        if sha256_file(markdown_path) != gate.get("markdown_sha256"):
            raise SystemExit(f"{role} review markdown hash mismatch after invocation.")

        invocations = [
            item
            for item in session.get("review_agent_invocations", [])
            if isinstance(item, dict)
            and item.get("actor_role") == role
            and item.get("subject_id") == subject["subject_id"]
        ]
        if len(invocations) != 1:
            raise SystemExit(f"{role} review does not have one exact cycle invocation record.")
        invocation = invocations[0]
        for key in (
            "review_input_path",
            "review_input_sha256",
            "reviewed_artifact_manifest_sha256",
            "job_sha256",
        ):
            if invocation.get(key) != gate.get(key):
                raise SystemExit(f"{role} review invocation and gate disagree on {key}.")

        decision = _validate_decision_binding(
            root=root,
            decision_path=decision_path,
            session_id=session["supervisor_session_id"],
            cycle=cycle,
            actor_role=role,
            review_kind=cycle["review_kind"],
        )
        if decision.get("json_report_path") != relpath(root, decision_path):
            raise SystemExit(f"{role} review decision self-reports a different JSON path.")
        if decision.get("markdown_report_path") != relpath(root, markdown_path):
            raise SystemExit(f"{role} review decision self-reports a different markdown path.")
        if decision.get("agent_command_id") != invocation.get("command_id"):
            raise SystemExit(f"{role} review decision does not match its invocation command id.")
        decisions[role] = decision
    return decisions


def _invocation_gate(root: Path, result: Any, decision: dict[str, Any], subject: dict[str, Any], subject_sha256: str) -> dict[str, Any]:
    role = result.actor_role
    transport = "passed" if int(result.command.get("exit_code", 1)) == 0 else "failed"
    schema = "passed" if decision.get("status") == "succeeded" else "failed"
    expected_job_sha = subject["frozen_job_sha256"]
    job_binding = "passed" if result.command.get("job_sha256") == expected_job_sha else "failed"
    review_input_binding = "passed" if (
        result.command.get("review_input_sha256") == subject["review_input_sha256"]
        and result.command.get("reviewed_artifact_manifest_sha256")
        == subject["reviewed_artifact_manifest_sha256"]
    ) else "failed"
    if role in READ_ONLY_REVIEW_ROLES:
        read_only = "passed" if isinstance(result.read_only_check, dict) and result.read_only_check.get("status") == "passed" else "failed"
    else:
        read_only = "not_applicable"
    gate_status = "passed" if transport == "passed" and schema == "passed" and job_binding == "passed" and review_input_binding == "passed" and read_only in {"passed", "not_applicable"} else "blocked"
    resolved = resolve_under_root(root, result.decision_path, must_exist=True)
    markdown = resolve_under_root(root, result.markdown_path, must_exist=True)
    return {
        "actor_role": role,
        "decision_path": relpath(root, resolved),
        "decision_sha256": sha256_file(resolved),
        "markdown_path": relpath(root, markdown),
        "markdown_sha256": sha256_file(markdown),
        "transport_status": transport,
        "schema_status": schema,
        "job_binding_status": job_binding,
        "review_input_binding_status": review_input_binding,
        "read_only_status": read_only,
        "gate_status": gate_status,
        "subject_id": subject["subject_id"],
        "subject_sha256": subject_sha256,
        "frozen_job_sha256": subject["frozen_job_sha256"],
        "job_sha256": result.command.get("job_sha256"),
        "review_input_path": result.command.get("review_input_path"),
        "review_input_sha256": result.command.get("review_input_sha256"),
        "reviewed_artifact_manifest_sha256": result.command.get("reviewed_artifact_manifest_sha256"),
    }


def _append_invocation_record(root: Path, session: dict[str, Any], result: Any, subject_id: str) -> None:
    record = {
        "command_id": result.command_id,
        "actor_role": result.actor_role,
        "argv": result.command["argv"],
        "cwd": result.command["cwd"],
        "started_at": result.command["started_at"],
        "completed_at": result.command["completed_at"],
        "exit_code": result.command["exit_code"],
        "stdout_path": result.stdout_path,
        "stderr_path": result.stderr_path,
        "read_only_result": result.read_only_check,
        "subject_id": subject_id,
        "job_sha256": result.command.get("job_sha256"),
        "review_input_path": result.command.get("review_input_path"),
        "review_input_sha256": result.command.get("review_input_sha256"),
        "reviewed_artifact_manifest_sha256": result.command.get("reviewed_artifact_manifest_sha256"),
    }
    usage_path = getattr(result, "usage_attempt_path", None)
    if isinstance(usage_path, str) and usage_path:
        resolved = resolve_under_root(root, usage_path, must_exist=True)
        record["usage_attempt_path"] = relpath(root, resolved)
        record["usage_attempt_sha256"] = sha256_file(resolved)
    session["review_agent_invocations"].append(record)


@contextmanager
def _cycle_invocation_lock(session_path: Path, review_cycle_id: str, operation: str) -> Iterator[None]:
    """Hold fail-fast process ownership for one cycle invocation operation."""

    cycle_dir = _cycle_dir(session_path, review_cycle_id)
    cycle_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(cycle_dir, 0o700)
    lock_path = cycle_dir / f".{normalize_slug(operation)}.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit(
                f"Review cycle {review_cycle_id} {operation} invocation is already owned by another process."
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def _cycle_transition_lock(session_path: Path, review_cycle_id: str) -> Iterator[None]:
    """Serialize mutually exclusive state transitions for one review cycle."""

    with _cycle_invocation_lock(session_path, review_cycle_id, "transition"):
        yield


@contextmanager
def _session_mutation_lock(session_path: Path) -> Iterator[None]:
    """Serialize session-derived mutations while allowing same-thread nesting."""

    lock_path = session_path / ".mutation.lock"
    held = getattr(_SESSION_MUTATION_LOCK_STATE, "held", None)
    if held is None:
        held = {}
        _SESSION_MUTATION_LOCK_STATE.held = held
    lock_key = str(lock_path.resolve())
    existing = held.get(lock_key)
    if existing is not None:
        existing["depth"] += 1
        try:
            yield
        finally:
            existing["depth"] -= 1
        return
    session_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(session_path, 0o700)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("Supervisor session mutation is already owned by another process.") from exc
        held[lock_key] = {"descriptor": descriptor, "depth": 1}
        yield
    finally:
        held.pop(lock_key, None)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def _session_finalization_lock(session_path: Path) -> Iterator[None]:
    """Serialize finalization with every other session-derived mutation."""

    with _session_mutation_lock(session_path):
        yield


def _invocation_reservation_intent(
    *,
    cycle: dict[str, Any],
    subject: dict[str, Any],
    operation: str,
    roles_and_outputs: dict[str, str],
    job_path: str,
    job_sha256: str,
    invocation_review_kind: str | None = None,
    bind_subject_review_input: bool = True,
    recovery_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    intent = {
        "operation": operation,
        "review_cycle_id": cycle["review_cycle_id"],
        "review_kind": invocation_review_kind or cycle["review_kind"],
        "subject_id": subject["subject_id"],
        "subject_sha256": cycle["subject_sha256"],
        "job_path": job_path,
        "job_sha256": job_sha256,
        "review_input_path": subject.get("review_input_path") if bind_subject_review_input else None,
        "review_input_sha256": subject.get("review_input_sha256") if bind_subject_review_input else None,
        "roles_and_outputs": roles_and_outputs,
        "recovery_context": recovery_context or {},
    }
    return _canonical_sha256(intent), intent


def _reserve_cycle_invocation(
    *,
    root: Path,
    session: dict[str, Any],
    session_path: Path,
    cycle: dict[str, Any],
    subject: dict[str, Any],
    operation: str,
    roles_and_outputs: dict[str, str],
    job_path: str,
    job_sha256: str,
    invocation_review_kind: str | None = None,
    bind_subject_review_input: bool = True,
    recovery_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    reservation_id, intent = _invocation_reservation_intent(
        cycle=cycle,
        subject=subject,
        operation=operation,
        roles_and_outputs=roles_and_outputs,
        job_path=job_path,
        job_sha256=job_sha256,
        invocation_review_kind=invocation_review_kind,
        bind_subject_review_input=bind_subject_review_input,
        recovery_context=recovery_context,
    )
    reservations = cycle.setdefault("invocation_reservations", {})
    existing = reservations.get(operation)
    if existing is not None:
        if not isinstance(existing, dict) or existing.get("reservation_id") != reservation_id or existing.get("intent") != intent:
            raise SystemExit(f"Review cycle {operation} invocation reservation identity mismatch.")
        if existing.get("status") == "completed":
            for artifact in existing.get("completed_artifacts", []):
                path = resolve_under_root(root, artifact.get("path"), must_exist=True)
                if sha256_file(path) != artifact.get("sha256"):
                    raise SystemExit(f"Completed {operation} invocation artifact hash mismatch: {artifact.get('path')}")
            raise SystemExit(f"Review cycle {operation} invocation is already completed; it will not be repeated.")
        if existing.get("status") != "reserved":
            raise SystemExit(f"Review cycle {operation} invocation reservation has an invalid status.")
        return existing, True
    now = runner_now().isoformat()
    reservation = {
        "reservation_id": reservation_id,
        "intent": intent,
        "status": "reserved",
        "recovery_count": 0,
        "created_at": now,
        "updated_at": now,
        "completed_artifacts": [],
    }
    reservations[operation] = reservation
    _write_session(root, session_path, session)
    return reservation, False


def _recover_reserved_agent_result(
    *,
    root: Path,
    session: dict[str, Any],
    cycle: dict[str, Any],
    subject: dict[str, Any],
    reservation: dict[str, Any],
    actor_role: str,
) -> supervisor_agents.AgentRunResult:
    output_value = reservation["intent"]["roles_and_outputs"].get(actor_role)
    output_dir = resolve_under_root(root, output_value, must_exist=True)
    candidates: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(output_dir.glob("*.json")):
        if path.name.endswith(".reviewer_usage_attempt.json") or path.name.endswith(".review_input.json"):
            continue
        try:
            payload = load_json(path, f"reserved {actor_role} decision candidate")
        except SystemExit:
            continue
        if payload.get("schema_version") == REVIEW_DECISION_SCHEMA_VERSION:
            candidates.append((path, payload))
    if len(candidates) != 1:
        raise SystemExit(
            f"Reserved {actor_role} invocation has {len(candidates)} decision candidates; "
            "explicit recovery is required and the agent will not be reinvoked."
        )
    decision_path, decision = candidates[0]
    decision = _validate_decision_binding(
        root=root,
        decision_path=decision_path,
        session_id=session["supervisor_session_id"],
        cycle=cycle,
        actor_role=actor_role,
        review_kind=reservation["intent"]["review_kind"],
    )
    command = decision.get("command")
    command_id = decision.get("agent_command_id")
    if not isinstance(command, dict) or not isinstance(command_id, str) or command.get("command_id") != command_id:
        raise SystemExit(f"Reserved {actor_role} decision lacks exact invocation command evidence.")
    if decision_path.name != f"{command_id}.json" or decision.get("json_report_path") != relpath(root, decision_path):
        raise SystemExit(f"Reserved {actor_role} decision path does not match its command id.")
    if command.get("job_sha256") != reservation["intent"]["job_sha256"]:
        raise SystemExit(f"Reserved {actor_role} decision job hash does not match its invocation reservation.")
    expected_review_input = reservation["intent"].get("review_input_sha256")
    if expected_review_input is not None and command.get("review_input_sha256") != expected_review_input:
        raise SystemExit(f"Reserved {actor_role} decision review-input hash does not match its reservation.")
    markdown_path = resolve_under_root(root, decision.get("markdown_report_path"), must_exist=True)
    if markdown_path.parent != output_dir or markdown_path.name != f"{command_id}.md":
        raise SystemExit(f"Reserved {actor_role} markdown is outside its expected output directory.")
    stdout_path = resolve_under_root(root, command.get("stdout_path"), must_exist=True)
    stderr_path = resolve_under_root(root, command.get("stderr_path"), must_exist=True)
    if stdout_path.parent != output_dir or stderr_path.parent != output_dir:
        raise SystemExit(f"Reserved {actor_role} transport evidence is outside its expected output directory.")
    prompt_path = resolve_under_root(root, command.get("composed_prompt_path"), must_exist=True)
    if prompt_path.parent != output_dir or sha256_file(prompt_path) != command.get("composed_prompt_sha256"):
        raise SystemExit(f"Reserved {actor_role} composed-prompt evidence hash mismatch.")
    usage_path = output_dir / f"{command_id}.reviewer_usage_attempt.json"
    usage = load_json(usage_path, f"reserved {actor_role} usage attempt")
    if usage.get("attempt_id") != command_id or usage.get("lane") != "reviewer":
        raise SystemExit(f"Reserved {actor_role} usage attempt identity mismatch.")
    return supervisor_agents.AgentRunResult(
        command_id=command_id,
        actor_role=actor_role,
        status=str(decision["status"]),
        approval_decision=str(decision["approval_decision"]),
        decision_path=relpath(root, decision_path),
        markdown_path=relpath(root, markdown_path),
        stdout_path=relpath(root, stdout_path),
        stderr_path=relpath(root, stderr_path),
        command=command,
        read_only_check=decision.get("read_only_check"),
        fallback_used=bool(command.get("fallback_used", False)),
        usage_attempt_path=relpath(root, usage_path),
    )


def _complete_cycle_invocation_reservation(
    *,
    root: Path,
    reservation: dict[str, Any],
    results: Sequence[supervisor_agents.AgentRunResult],
    recovered: bool,
) -> None:
    artifacts: list[dict[str, str]] = []
    for result in results:
        for value in (result.decision_path, result.markdown_path, getattr(result, "usage_attempt_path", None)):
            if isinstance(value, str) and value:
                path = resolve_under_root(root, value, must_exist=True)
                artifacts.append({"path": relpath(root, path), "sha256": sha256_file(path)})
    reservation["status"] = "completed"
    reservation["updated_at"] = runner_now().isoformat()
    reservation["completed_at"] = reservation["updated_at"]
    reservation["completed_artifacts"] = artifacts
    if recovered:
        reservation["recovery_count"] = int(reservation.get("recovery_count", 0)) + 1


def invoke_operator(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    review_kind: str,
    job_json: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Invoke the accountable operator Codex lane and record its provisional review."""

    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _cycle_invocation_lock(session_path, review_cycle_id, "operator_provisional"):
        session, session_path = _load_session_and_path(root, session_ref)
        if not any(cycle["review_cycle_id"] == review_cycle_id for cycle in session["review_cycles"]):
            create_review_cycle(root=root, session_ref=session_ref, review_cycle_id=review_cycle_id, review_kind=review_kind)
            session, session_path = _load_session_and_path(root, session_ref)
        cycle = _find_cycle(session, review_cycle_id)
        if cycle.get("review_kind") != review_kind:
            raise SystemExit("Review kind does not match the existing review cycle.")
        if cycle.get("operator_provisional_record") and "operator_provisional" not in cycle.get("invocation_reservations", {}):
            raise SystemExit("Operator provisional review is immutable once recorded.")
        subject = _freeze_cycle_subject(root=root, session=session, session_path=session_path, cycle=cycle, job=job_json)
        operator_output = _require_derived_path(root, output_dir, cycle["derived_paths"]["operator_dir"], "operator output directory")
        reservation, recovering = _reserve_cycle_invocation(
            root=root,
            session=session,
            session_path=session_path,
            cycle=cycle,
            subject=subject,
            operation="operator_provisional",
            roles_and_outputs={"operator_codex": relpath(root, operator_output)},
            job_path=subject["frozen_job_path"],
            job_sha256=subject["frozen_job_sha256"],
        )
        if recovering:
            try:
                result = _recover_reserved_agent_result(
                    root=root,
                    session=session,
                    cycle=cycle,
                    subject=subject,
                    reservation=reservation,
                    actor_role="operator_codex",
                )
            except SystemExit as exc:
                raise SystemExit(
                    f"Reserved operator provisional invocation requires explicit recovery; "
                    f"the operator will not be reinvoked. {exc}"
                ) from exc
        else:
            result = supervisor_agents.invoke_operator_codex(
                root=root,
                review_kind=review_kind,
                review_cycle_id=review_cycle_id,
                supervisor_session_id=session["supervisor_session_id"],
                job=cycle["derived_paths"]["frozen_job"],
                review_input=subject["review_input_path"],
                output_dir=operator_output,
            )
        decision = _validate_decision_binding(
            root=root,
            decision_path=result.decision_path,
            session_id=session["supervisor_session_id"],
            cycle=cycle,
            actor_role="operator_codex",
            review_kind=review_kind,
        )
        cycle["operator_provisional_record"] = result.decision_path
        cycle["review_gates"]["operator_codex"] = _invocation_gate(root, result, decision, subject, cycle["subject_sha256"])
        _append_invocation_record(root, session, result, subject["subject_id"])
        _complete_cycle_invocation_reservation(root=root, reservation=reservation, results=[result], recovered=recovering)
        _write_session(root, session_path, session)
        return {"operator_review": result.decision_path}


def invoke_reviewers(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    review_kind: str,
    job_json: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Invoke independent read-only Codex and Claude review agents for a review cycle."""

    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _cycle_invocation_lock(session_path, review_cycle_id, "independent_reviewers"):
        session, session_path = _load_session_and_path(root, session_ref)
        cycle = _find_cycle(session, review_cycle_id)
        if cycle.get("review_kind") != review_kind:
            raise SystemExit("Review kind does not match the existing review cycle.")
        if review_kind in REVIEW_KINDS_REQUIRING_OPERATOR_PROVISIONAL:
            _require_successful_operator_provisional(root=root, session=session, cycle=cycle)
        subject = _load_cycle_subject(root, cycle)
        _verify_subject_artifacts(root, subject)
        supplied_job = _job_payload(root, job_json)
        if _canonical_sha256(supplied_job) != _canonical_sha256(load_json(resolve_under_root(root, subject["frozen_job_path"], must_exist=True), "frozen review job")):
            raise SystemExit("Reviewer job differs from the operator-frozen review job.")
        if cycle.get("review_agent_outputs") and "independent_reviewers" not in cycle.get("invocation_reservations", {}):
            raise SystemExit("Independent reviewer outputs are immutable once recorded.")
        _require_derived_path(root, output_dir, cycle["derived_paths"]["reviewers_dir"], "reviewer output directory")
        codex_output = resolve_under_root(root, cycle["derived_paths"]["codex_reviewer_dir"], must_exist=False)
        claude_output = resolve_under_root(root, cycle["derived_paths"]["claude_reviewer_dir"], must_exist=False)
        reservation, recovering = _reserve_cycle_invocation(
            root=root,
            session=session,
            session_path=session_path,
            cycle=cycle,
            subject=subject,
            operation="independent_reviewers",
            roles_and_outputs={
                "codex_review_agent": relpath(root, codex_output),
                "claude_review_agent": relpath(root, claude_output),
            },
            job_path=subject["frozen_job_path"],
            job_sha256=subject["frozen_job_sha256"],
        )
        if recovering:
            try:
                codex = _recover_reserved_agent_result(root=root, session=session, cycle=cycle, subject=subject, reservation=reservation, actor_role="codex_review_agent")
                claude = _recover_reserved_agent_result(root=root, session=session, cycle=cycle, subject=subject, reservation=reservation, actor_role="claude_review_agent")
            except SystemExit as exc:
                raise SystemExit(
                    f"Reserved independent reviewer invocation requires explicit recovery; "
                    f"reviewers will not be reinvoked. {exc}"
                ) from exc
        else:
            common = {
                "root": root,
                "review_kind": review_kind,
                "review_cycle_id": review_cycle_id,
                "supervisor_session_id": session["supervisor_session_id"],
                "job": subject["frozen_job_path"],
                "review_input": subject["review_input_path"],
            }
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="supervisor-review") as executor:
                codex_future = executor.submit(supervisor_agents.invoke_codex_review_agent, **common, output_dir=codex_output)
                claude_future = executor.submit(supervisor_agents.invoke_claude_review_agent, **common, output_dir=claude_output)
                codex = codex_future.result()
                claude = claude_future.result()
        cycle["review_agent_outputs"]["codex_review_agent"] = codex.decision_path
        cycle["review_agent_outputs"]["claude_review_agent"] = claude.decision_path
        for result in (codex, claude):
            decision = _validate_decision_binding(
                root=root,
                decision_path=result.decision_path,
                session_id=session["supervisor_session_id"],
                cycle=cycle,
                actor_role=result.actor_role,
                review_kind=review_kind,
            )
            cycle["review_gates"][result.actor_role] = _invocation_gate(root, result, decision, subject, cycle["subject_sha256"])
            _append_invocation_record(root, session, result, subject["subject_id"])
        _complete_cycle_invocation_reservation(root=root, reservation=reservation, results=[codex, claude], recovered=recovering)
        _write_session(root, session_path, session)
        return {"codex_review": codex.decision_path, "claude_review": claude.decision_path}


def run_review_cycle(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    review_kind: str,
    job_json: str | Path,
) -> dict[str, Any]:
    """Run operator then parallel reviewers and deterministic consolidation only."""

    operator = invoke_operator(
        root=root,
        session_ref=session_ref,
        review_cycle_id=review_cycle_id,
        review_kind=review_kind,
        job_json=job_json,
    )
    reviewers = invoke_reviewers(
        root=root,
        session_ref=session_ref,
        review_cycle_id=review_cycle_id,
        review_kind=review_kind,
        job_json=job_json,
    )
    consolidation = consolidate_reviews(
        root=root,
        session_ref=session_ref,
        review_cycle_id=review_cycle_id,
    )
    return {
        "operator_review": operator["operator_review"],
        "codex_review": reviewers["codex_review"],
        "claude_review": reviewers["claude_review"],
        "consolidation": consolidation["json_report_path"],
        "acceptance_status": "pending",
    }


def _resume_review_cycle(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    review_kind: str,
    job_json: str | Path,
) -> dict[str, Any]:
    """Continue only the missing durable phases of an existing review cycle."""

    session, _session_path = _load_session_and_path(root, session_ref)
    cycle = _find_cycle(session, review_cycle_id)
    if cycle.get("review_kind") != review_kind:
        raise SystemExit("Review kind does not match the resumable review cycle.")
    if not cycle.get("operator_provisional_record"):
        invoke_operator(
            root=root,
            session_ref=session_ref,
            review_cycle_id=review_cycle_id,
            review_kind=review_kind,
            job_json=job_json,
        )
        session, _session_path = _load_session_and_path(root, session_ref)
        cycle = _find_cycle(session, review_cycle_id)
    outputs = cycle.get("review_agent_outputs") or {}
    if not outputs.get("codex_review_agent") or not outputs.get("claude_review_agent"):
        invoke_reviewers(
            root=root,
            session_ref=session_ref,
            review_cycle_id=review_cycle_id,
            review_kind=review_kind,
            job_json=job_json,
        )
        session, _session_path = _load_session_and_path(root, session_ref)
        cycle = _find_cycle(session, review_cycle_id)
    if not cycle.get("consolidation"):
        consolidate_reviews(
            root=root,
            session_ref=session_ref,
            review_cycle_id=review_cycle_id,
        )
        session, _session_path = _load_session_and_path(root, session_ref)
        cycle = _find_cycle(session, review_cycle_id)
    subject = _load_cycle_subject(root, cycle)
    _verified_cycle_review_decisions(
        root=root,
        session=session,
        cycle=cycle,
        subject=subject,
    )
    consolidation_path = resolve_under_root(root, cycle["consolidation"], must_exist=True)
    if sha256_file(consolidation_path) != cycle.get("consolidation_sha256"):
        raise SystemExit("Resumable review consolidation hash mismatch.")
    outputs = cycle["review_agent_outputs"]
    return {
        "operator_review": cycle["operator_provisional_record"],
        "codex_review": outputs["codex_review_agent"],
        "claude_review": outputs["claude_review_agent"],
        "consolidation": cycle["consolidation"],
        "acceptance_status": cycle["acceptance_status"],
    }


def _directive_recommendation(rec: dict[str, Any]) -> dict[str, Any]:
    evidence = rec.get("evidence") if isinstance(rec.get("evidence"), list) else []
    if not evidence:
        raise SystemExit(f"Revision recommendation lacks evidence: {rec.get('recommendation_id')}")
    directive = {
        "recommendation_id": str(rec["recommendation_id"]),
        "source_agent": str(rec.get("source_agent") or "unknown"),
        "severity": str(rec.get("severity") or "medium"),
        "recommendation": str(rec.get("recommendation") or ""),
        "evidence": evidence,
        "affected_artifacts": rec.get("affected_artifacts") if isinstance(rec.get("affected_artifacts"), list) else [],
    }
    for key in ("exact_change_needed", "rationale_for_no_change"):
        if isinstance(rec.get(key), str) and rec[key].strip():
            directive[key] = rec[key]
    return directive


def create_revision_directive(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    accepted_recommendation_ids: Sequence[str],
    rejected_recommendations: dict[str, str],
    revised_artifacts: Sequence[str | Path],
    revision_scaffold_path: str | Path | None = None,
) -> dict[str, Any]:
    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _session_mutation_lock(session_path):
        with _cycle_transition_lock(session_path, review_cycle_id):
            return _create_revision_directive_locked(
                root=root,
                session_ref=session_ref,
                review_cycle_id=review_cycle_id,
                accepted_recommendation_ids=accepted_recommendation_ids,
                rejected_recommendations=rejected_recommendations,
                revised_artifacts=revised_artifacts,
                revision_scaffold_path=revision_scaffold_path,
            )


def _create_revision_directive_locked(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    accepted_recommendation_ids: Sequence[str],
    rejected_recommendations: dict[str, str],
    revised_artifacts: Sequence[str | Path],
    revision_scaffold_path: str | Path | None = None,
) -> dict[str, Any]:
    """Freeze the operator's evidence-supported revision selection before any edit."""

    session, session_path = _load_session_and_path(root, session_ref)
    cycle = _find_cycle(session, review_cycle_id)
    if cycle.get("acceptance_status") != "pending" or cycle.get("revision"):
        raise SystemExit("Revision directive requires one pending, unrevised review cycle.")
    subject = _load_cycle_subject(root, cycle)
    _verify_subject_artifacts(root, subject)
    consolidation_ref = cycle.get("consolidation")
    if not isinstance(consolidation_ref, str):
        raise SystemExit("Revision directive requires the supervisor-created consolidation.")
    consolidation_path = resolve_under_root(root, consolidation_ref, must_exist=True)
    if sha256_file(consolidation_path) != cycle.get("consolidation_sha256"):
        raise SystemExit("Consolidation changed before revision selection.")
    consolidation = _load_decision(root, consolidation_path, "consolidated review")
    recommendations = {
        str(rec["recommendation_id"]): rec
        for rec in consolidation.get("recommendations", [])
        if isinstance(rec, dict) and rec.get("recommendation_id")
    }
    accepted = set(accepted_recommendation_ids)
    rejected = set(rejected_recommendations)
    if not accepted:
        raise SystemExit("Revision directive requires at least one accepted recommendation.")
    if accepted & rejected:
        raise SystemExit("A recommendation cannot be both accepted and rejected.")
    if accepted | rejected != set(recommendations):
        missing = sorted(set(recommendations) - accepted - rejected)
        unknown = sorted((accepted | rejected) - set(recommendations))
        raise SystemExit(f"Revision selection must partition every consolidation recommendation; missing={missing}, unknown={unknown}")
    if any(not str(rejected_recommendations[rec_id]).strip() for rec_id in rejected):
        raise SystemExit("Every rejected recommendation requires a non-empty rationale.")
    targets: list[str] = []
    for raw in revised_artifacts:
        resolved = resolve_under_root(root, raw, must_exist=False)
        if resolved.exists() and not resolved.is_file():
            raise SystemExit(f"Revised artifact target must be a file: {raw}")
        targets.append(relpath(root, resolved))
    if not targets or len(targets) != len(set(targets)):
        raise SystemExit("Revision directive requires unique revised artifact file paths.")
    scaffold_rel = None
    if cycle.get("review_kind") == "scaffold":
        if revision_scaffold_path is None:
            raise SystemExit("Scaffold revision requires --revision-scaffold-path for a new staged version.")
        scaffold = resolve_under_root(root, revision_scaffold_path, must_exist=True)
        if not scaffold.is_dir() or any(not resolve_under_root(root, item, must_exist=False).is_relative_to(scaffold) for item in targets):
            raise SystemExit("Every scaffold revision target must be inside revision_scaffold_path.")
        scaffold_rel = relpath(root, scaffold)
    directive = {
        "schema_version": REVISION_DIRECTIVE_SCHEMA_VERSION,
        "directive_id": normalize_slug(f"revision_{review_cycle_id}"),
        "created_at": cycle["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "source_review_cycle_id": review_cycle_id,
        "source_subject_id": subject["subject_id"],
        "source_subject_sha256": cycle["subject_sha256"],
        "consolidation_path": relpath(root, consolidation_path),
        "consolidation_sha256": sha256_file(consolidation_path),
        "accepted_recommendations": [_directive_recommendation(recommendations[rec_id]) for rec_id in sorted(accepted)],
        "rejected_recommendations": [
            {**_directive_recommendation(recommendations[rec_id]), "rejection_rationale": str(rejected_recommendations[rec_id]).strip()}
            for rec_id in sorted(rejected)
        ],
        "mandatory_blockers": consolidation.get("blocking_issues", []),
        "revised_artifacts": targets,
        "revision_scaffold_path": scaffold_rel,
    }
    directive_path = cycle["derived_paths"]["revision_directive"]
    _write_once_json(root, directive_path, directive, "revision_directive.schema.json", "revision directive")
    source_manifest = load_json(resolve_under_root(root, subject["reviewed_artifact_manifest_path"], must_exist=True), "reviewed artifact manifest")
    before = []
    for target in targets:
        resolved = resolve_under_root(root, target, must_exist=False)
        before.append({"path": target, "sha256": sha256_file(resolved) if resolved.exists() else None, "bytes": resolved.stat().st_size if resolved.exists() else None})
    revision_job = {
        "review_job_id": normalize_slug(f"operator_revision_{review_cycle_id}"),
        "review_kind": "recovery",
        "objective": "Apply only the accepted revision recommendations, preserve rejected recommendations as rejected, and emit evidence for actual changes and validation.",
        "source_review_cycle_id": review_cycle_id,
        "source_subject_id": subject["subject_id"],
        "revision_directive": directive_path,
        "revision_directive_sha256": sha256_file(resolve_under_root(root, directive_path, must_exist=True)),
        "reviewed_artifacts": [item["path"] for item in source_manifest.get("artifacts", [])] + [relpath(root, consolidation_path), directive_path],
        "revised_artifacts": before,
        "allowed_write_paths": targets,
        "required_checks": [
            "Every accepted recommendation must have changes_applied and validation_evidence.",
            "Every rejected recommendation must remain rejected with its recorded rationale.",
            "Do not edit independent reviewer outputs or paths outside allowed_write_paths.",
        ],
        "workflow_id": subject.get("workflow_id"),
        "run_id": subject.get("run_id"),
        "stage_id": subject.get("stage_id"),
    }
    job_path = cycle["derived_paths"]["revision_job"]
    _write_once_json(root, job_path, revision_job, label="operator revision job")
    cycle["revision"] = {
        "status": "directive_ready",
        "directive_path": directive_path,
        "directive_sha256": sha256_file(resolve_under_root(root, directive_path, must_exist=True)),
        "job_path": job_path,
        "job_sha256": sha256_file(resolve_under_root(root, job_path, must_exist=True)),
    }
    _write_session(root, session_path, session)
    return directive


def _validate_operator_revision_decision(decision: dict[str, Any], directive: dict[str, Any]) -> None:
    if decision.get("status") != "succeeded" or decision.get("approval_decision") not in {"approve", "approve_with_conditions"}:
        raise SystemExit("Operator revision job did not complete successfully.")
    by_id = {str(rec.get("recommendation_id")): rec for rec in decision.get("recommendations", []) if isinstance(rec, dict)}
    for expected in directive["accepted_recommendations"]:
        rec = by_id.get(expected["recommendation_id"])
        if not isinstance(rec, dict) or rec.get("operator_decision") != "accepted" or not rec.get("changes_applied") or not rec.get("validation_evidence"):
            raise SystemExit(f"Operator revision lacks applied-change trace for {expected['recommendation_id']}.")
    for expected in directive["rejected_recommendations"]:
        rec = by_id.get(expected["recommendation_id"])
        if not isinstance(rec, dict) or rec.get("operator_decision") != "rejected" or not str(rec.get("rejected_reason") or "").strip():
            raise SystemExit(f"Operator revision lacks rejection trace for {expected['recommendation_id']}.")


def run_revision_and_review(
    *,
    root: Path,
    session_ref: str | Path,
    source_review_cycle_id: str,
    new_review_cycle_id: str,
) -> dict[str, Any]:
    """Serialize one operator revision and its fresh review for the source cycle."""

    if new_review_cycle_id == source_review_cycle_id:
        raise SystemExit("Revised review cycle ID must differ from its source review cycle ID.")
    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _session_mutation_lock(session_path):
        with _cycle_transition_lock(session_path, source_review_cycle_id):
            return _run_revision_and_review_locked(
                root=root,
                session_ref=session_ref,
                source_review_cycle_id=source_review_cycle_id,
                new_review_cycle_id=new_review_cycle_id,
            )


def _run_revision_and_review_locked(
    *,
    root: Path,
    session_ref: str | Path,
    source_review_cycle_id: str,
    new_review_cycle_id: str,
) -> dict[str, Any]:
    """Run the declared operator revision, freeze its outputs, then perform a fresh full review."""

    if new_review_cycle_id == source_review_cycle_id:
        raise SystemExit("Revised review cycle ID must differ from its source review cycle ID.")
    session, session_path = _load_session_and_path(root, session_ref)
    source_cycle = _find_cycle(session, source_review_cycle_id)
    revision = source_cycle.get("revision")
    if not isinstance(revision, dict):
        raise SystemExit("Revision job requires one prepared immutable revision directive.")
    if revision.get("status") != "directive_ready":
        return _continue_revision_after_operator(
            root=root,
            session_ref=session_ref,
            source_review_cycle_id=source_review_cycle_id,
            new_review_cycle_id=new_review_cycle_id,
        )
    if any(cycle.get("review_cycle_id") == new_review_cycle_id for cycle in session["review_cycles"]):
        raise SystemExit(f"New review cycle already exists: {new_review_cycle_id}")
    subject = _load_cycle_subject(root, source_cycle)
    existing_revision_reservation = (source_cycle.get("invocation_reservations") or {}).get("operator_revision")
    recovering_existing_revision = isinstance(existing_revision_reservation, dict) and existing_revision_reservation.get("status") == "reserved"
    if not recovering_existing_revision:
        _verify_subject_artifacts(root, subject)
    source_final_packet_draft: str | None = None
    source_final_packet_artifacts: list[str] = []
    if source_cycle.get("review_kind") == "final_packet":
        source_final_packet_draft = subject.get("final_packet_draft_path")
        if not isinstance(source_final_packet_draft, str) or not source_final_packet_draft:
            raise SystemExit("Final-packet revision source is missing its reviewed draft binding.")
        source_manifest = load_json(
            resolve_under_root(root, subject["reviewed_artifact_manifest_path"], must_exist=True),
            "source reviewed artifact manifest",
        )
        source_final_packet_artifacts = [
            str(item["path"])
            for item in source_manifest.get("artifacts", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
    directive_path = resolve_under_root(root, revision["directive_path"], must_exist=True)
    job_path = resolve_under_root(root, revision["job_path"], must_exist=True)
    if sha256_file(directive_path) != revision["directive_sha256"] or sha256_file(job_path) != revision["job_sha256"]:
        raise SystemExit("Revision directive or operator job changed before execution.")
    directive = load_json(directive_path, "revision directive")
    supervisor_artifacts.validate_against_schema(directive, "revision_directive.schema.json", "revision directive")
    job = load_json(job_path, "operator revision job")
    revision_output = resolve_under_root(root, source_cycle["derived_paths"]["revision_operator_dir"], must_exist=False)
    if recovering_existing_revision:
        recovery_context = existing_revision_reservation.get("intent", {}).get("recovery_context")
        if not isinstance(recovery_context, dict):
            raise SystemExit("Reserved operator revision lacks its pre-invocation recovery context.")
        if recovery_context.get("new_review_cycle_id") != new_review_cycle_id:
            raise SystemExit("Reserved operator revision new review-cycle identity mismatch.")
        snapshot_path = resolve_under_root(root, recovery_context.get("before_snapshot_path"), must_exist=True)
        if sha256_file(snapshot_path) != recovery_context.get("before_snapshot_sha256"):
            raise SystemExit("Reserved operator revision pre-invocation snapshot hash mismatch.")
        snapshot_payload = load_json(snapshot_path, "operator revision pre-invocation snapshot")
        before_snapshot = snapshot_payload.get("workspace_snapshot")
        if not isinstance(before_snapshot, dict):
            raise SystemExit("Reserved operator revision pre-invocation snapshot is malformed.")
        if recovery_context.get("allowed_write_paths") != directive["revised_artifacts"]:
            raise SystemExit("Reserved operator revision allowed-write paths changed after reservation.")
    else:
        before_snapshot = supervisor_artifacts.snapshot_workspace(root, include_paths=directive["revised_artifacts"])
        snapshot_path = revision_output.parent / "pre_invocation_workspace_snapshot.json"
        snapshot_payload = {
            "schema_version": "responses_runner_v2.operator_revision_snapshot.v1",
            "created_at": directive["created_at"],
            "workspace_snapshot": before_snapshot,
        }
        _write_once_json(root, snapshot_path, snapshot_payload, label="operator revision pre-invocation snapshot")
        recovery_context = {
            "before_snapshot_path": relpath(root, snapshot_path),
            "before_snapshot_sha256": sha256_file(snapshot_path),
            "allowed_write_paths": list(directive["revised_artifacts"]),
            "operator_output_dir": relpath(root, revision_output),
            "new_review_cycle_id": new_review_cycle_id,
        }
    recorded_new_cycle_id = revision.get("new_review_cycle_id")
    if recorded_new_cycle_id not in {None, new_review_cycle_id}:
        raise SystemExit("Operator revision new review-cycle identity changed before reservation.")
    revision["new_review_cycle_id"] = new_review_cycle_id
    reservation, recovering_revision = _reserve_cycle_invocation(
        root=root,
        session=session,
        session_path=session_path,
        cycle=source_cycle,
        subject=subject,
        operation="operator_revision",
        roles_and_outputs={"operator_codex": relpath(root, revision_output)},
        job_path=relpath(root, job_path),
        job_sha256=_agent_job_sha256(job),
        invocation_review_kind="recovery",
        bind_subject_review_input=False,
        recovery_context=recovery_context,
    )
    if recovering_revision:
        try:
            result = _recover_reserved_agent_result(
                root=root,
                session=session,
                cycle=source_cycle,
                subject=subject,
                reservation=reservation,
                actor_role="operator_codex",
            )
        except SystemExit as exc:
            raise SystemExit(
                f"Reserved operator revision requires explicit recovery; the operator will not be reinvoked. {exc}"
            ) from exc
    else:
        result = supervisor_agents.invoke_operator_codex(
            root=root,
            review_kind="recovery",
            review_cycle_id=source_review_cycle_id,
            supervisor_session_id=session["supervisor_session_id"],
            job=relpath(root, job_path),
            output_dir=revision_output,
        )
    if result.command.get("job_sha256") != _agent_job_sha256(job):
        raise SystemExit("Operator revision invocation was not bound to the immutable revision job.")
    decision = _load_decision(root, result.decision_path, "operator revision decision")
    expected_identity = {
        "supervisor_session_id": session["supervisor_session_id"],
        "review_cycle_id": source_review_cycle_id,
        "review_kind": "recovery",
        "actor_role": "operator_codex",
    }
    if any(decision.get(key) != value for key, value in expected_identity.items()):
        raise SystemExit("Operator revision decision identity mismatch.")
    for key in ("workflow_id", "run_id", "stage_id"):
        if decision.get(key) != subject.get(key):
            raise SystemExit(f"Operator revision decision {key} does not match the source subject.")
    _validate_operator_revision_decision(decision, directive)
    revised_records: list[dict[str, Any]] = []
    changed_count = 0
    before_by_path = {item["path"]: item for item in job["revised_artifacts"]}
    for target in directive["revised_artifacts"]:
        resolved = resolve_under_root(root, target, must_exist=True)
        if not resolved.is_file():
            raise SystemExit(f"Operator revision did not emit a file: {target}")
        before_sha = before_by_path[target].get("sha256")
        after_sha = sha256_file(resolved)
        changed_count += int(before_sha != after_sha)
        revised_records.append({"path": target, "before_sha256": before_sha, "after_sha256": after_sha, "bytes": resolved.stat().st_size})
    if changed_count == 0:
        raise SystemExit("Operator revision produced no declared artifact change.")
    after_snapshot = supervisor_artifacts.snapshot_workspace(root, include_paths=directive["revised_artifacts"])
    changed_paths = {item["path"] for item in supervisor_artifacts.diff_snapshots(before_snapshot, after_snapshot)}
    allowed = set(directive["revised_artifacts"])
    operator_output_dir = resolve_under_root(root, source_cycle["derived_paths"]["revision_operator_dir"], must_exist=True)
    supervisor_transition_paths = {
        relpath(root, snapshot_path),
        relpath(root, snapshot_path.with_name(f".{snapshot_path.name}.create.lock")),
        relpath(root, supervisor_artifacts.session_manifest_path(session_path)),
    }
    unexpected = sorted(
        path
        for path in changed_paths - allowed
        if path not in supervisor_transition_paths
        and not resolve_under_root(root, path, must_exist=False).is_relative_to(operator_output_dir)
    )
    if unexpected:
        raise SystemExit(f"Operator revision changed undeclared paths: {', '.join(unexpected)}")
    _append_invocation_record(root, session, result, subject["subject_id"])
    revision["operator_decision_path"] = result.decision_path
    revision["operator_decision_sha256"] = sha256_file(resolve_under_root(root, result.decision_path, must_exist=True))
    revision["revised_artifacts"] = revised_records
    revision["status"] = "operator_completed"
    _complete_cycle_invocation_reservation(root=root, reservation=reservation, results=[result], recovered=recovering_revision)
    _write_session(root, session_path, session)
    return _continue_revision_after_operator(
        root=root,
        session_ref=session_ref,
        source_review_cycle_id=source_review_cycle_id,
        new_review_cycle_id=new_review_cycle_id,
    )


def _continue_revision_after_operator(
    *,
    root: Path,
    session_ref: str | Path,
    source_review_cycle_id: str,
    new_review_cycle_id: str,
) -> dict[str, Any]:
    """Reconcile and continue every durable phase after operator completion."""

    if new_review_cycle_id == source_review_cycle_id:
        raise SystemExit("Revised review cycle ID must differ from its source review cycle ID.")
    session, session_path = _load_session_and_path(root, session_ref)
    source_cycle = _find_cycle(session, source_review_cycle_id)
    revision = source_cycle.get("revision")
    if not isinstance(revision, dict) or revision.get("status") not in {
        "operator_completed",
        "result_ready",
        "superseded_by_fresh_review",
    }:
        raise SystemExit("Revision continuation requires a completed operator revision.")
    reservation = (source_cycle.get("invocation_reservations") or {}).get("operator_revision")
    reserved_new_cycle = (
        reservation.get("intent", {}).get("recovery_context", {}).get("new_review_cycle_id")
        if isinstance(reservation, dict)
        else None
    )
    if reserved_new_cycle != new_review_cycle_id:
        raise SystemExit("Revision continuation new review-cycle identity does not match its invocation reservation.")
    recorded_new_cycle = revision.get("new_review_cycle_id")
    if recorded_new_cycle is not None and recorded_new_cycle != new_review_cycle_id:
        raise SystemExit("Revision continuation new review-cycle identity mismatch.")
    subject = _load_cycle_subject(root, source_cycle)
    directive_path = resolve_under_root(root, revision["directive_path"], must_exist=True)
    job_path = resolve_under_root(root, revision["job_path"], must_exist=True)
    if sha256_file(directive_path) != revision["directive_sha256"] or sha256_file(job_path) != revision["job_sha256"]:
        raise SystemExit("Revision directive or operator job changed during continuation.")
    directive = load_json(directive_path, "revision directive")
    supervisor_artifacts.validate_against_schema(directive, "revision_directive.schema.json", "revision directive")
    job = load_json(job_path, "operator revision job")
    if not isinstance(reservation, dict) or reservation.get("status") != "completed":
        raise SystemExit("Revision continuation requires the completed operator invocation reservation.")
    for artifact in reservation.get("completed_artifacts", []):
        path = resolve_under_root(root, artifact.get("path"), must_exist=True)
        if sha256_file(path) != artifact.get("sha256"):
            raise SystemExit(f"Completed operator revision artifact hash mismatch: {artifact.get('path')}")
    decision_path = resolve_under_root(root, revision.get("operator_decision_path"), must_exist=True)
    if sha256_file(decision_path) != revision.get("operator_decision_sha256"):
        raise SystemExit("Operator revision decision changed during continuation.")
    decision = _load_decision(root, decision_path, "operator revision decision")
    expected_identity = {
        "supervisor_session_id": session["supervisor_session_id"],
        "review_cycle_id": source_review_cycle_id,
        "review_kind": "recovery",
        "actor_role": "operator_codex",
    }
    if any(decision.get(key) != value for key, value in expected_identity.items()):
        raise SystemExit("Operator revision decision identity mismatch during continuation.")
    for key in ("workflow_id", "run_id", "stage_id"):
        if decision.get(key) != subject.get(key):
            raise SystemExit(f"Operator revision decision {key} does not match the source subject.")
    _validate_operator_revision_decision(decision, directive)
    before_by_path = {item["path"]: item for item in job["revised_artifacts"]}
    revised_records: list[dict[str, Any]] = []
    changed_count = 0
    for target in directive["revised_artifacts"]:
        resolved = resolve_under_root(root, target, must_exist=True)
        if not resolved.is_file():
            raise SystemExit(f"Operator revision did not emit a file: {target}")
        before_sha = before_by_path[target].get("sha256")
        after_sha = sha256_file(resolved)
        changed_count += int(before_sha != after_sha)
        revised_records.append({"path": target, "before_sha256": before_sha, "after_sha256": after_sha, "bytes": resolved.stat().st_size})
    if changed_count == 0:
        raise SystemExit("Operator revision produced no declared artifact change.")
    if revision.get("revised_artifacts") != revised_records:
        raise SystemExit("Operator revision artifacts changed after the operator-completed commit.")

    revised_review_paths = list(directive["revised_artifacts"])
    if source_cycle.get("review_kind") == "scaffold":
        marker = f"operator_revision:{source_review_cycle_id}"
        staged_id = revision.get("staged_scaffold_version_id")
        candidates = [item for item in session.get("scaffold_versions", []) if item.get("created_by") == marker]
        if staged_id is not None:
            candidates = [item for item in session.get("scaffold_versions", []) if item.get("version_id") == staged_id]
        if len(candidates) > 1:
            raise SystemExit("Revision continuation found multiple staged scaffold versions.")
        if not candidates:
            stage_scaffold(
                root=root,
                session_ref=session_ref,
                scaffold_path=directive["revision_scaffold_path"],
                created_by=marker,
            )
            session, session_path = _load_session_and_path(root, session_ref)
            source_cycle = _find_cycle(session, source_review_cycle_id)
            revision = source_cycle["revision"]
            candidates = [item for item in session.get("scaffold_versions", []) if item.get("created_by") == marker]
        if len(candidates) != 1:
            raise SystemExit("Revision continuation could not identify its staged scaffold version.")
        staged = candidates[0]
        if revision.get("staged_scaffold_version_id") not in {None, staged["version_id"]}:
            raise SystemExit("Revision continuation staged scaffold identity mismatch.")
        if revision.get("staged_scaffold_version_id") is None:
            revision["staged_scaffold_version_id"] = staged["version_id"]
            _write_session(root, session_path, session)
        staged_root = resolve_under_root(root, staged["path"], must_exist=True)
        working_root = resolve_under_root(root, directive["revision_scaffold_path"], must_exist=True)
        revised_review_paths = [
            relpath(root, staged_root / resolve_under_root(root, path, must_exist=True).relative_to(working_root))
            for path in directive["revised_artifacts"]
        ]

    if source_cycle.get("review_kind") == "final_packet":
        source_final_packet_draft = subject.get("final_packet_draft_path")
        if not isinstance(source_final_packet_draft, str) or not source_final_packet_draft:
            raise SystemExit("Final-packet revision source is missing its reviewed draft binding.")
        source_manifest = load_json(
            resolve_under_root(root, subject["reviewed_artifact_manifest_path"], must_exist=True),
            "source reviewed artifact manifest",
        )
        source_artifacts = [
            str(item["path"])
            for item in source_manifest.get("artifacts", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
        revised_review_paths = list(dict.fromkeys(source_artifacts + revised_review_paths + [source_final_packet_draft]))
    else:
        source_final_packet_draft = None

    fresh_job = {
        "review_job_id": normalize_slug(f"revised_review_{new_review_cycle_id}"),
        "review_kind": source_cycle["review_kind"],
        "objective": "Fresh full review of the operator-revised artifacts. Do not rely on the superseded review verdict.",
        "reviewed_artifacts": list(dict.fromkeys(revised_review_paths + [revision["directive_path"], revision["operator_decision_path"]])),
        "revision_lineage": {
            "source_review_cycle_id": source_review_cycle_id,
            "source_subject_id": subject["subject_id"],
            "revision_directive_path": revision["directive_path"],
            "revision_directive_sha256": revision["directive_sha256"],
            "accepted_recommendation_ids": [item["recommendation_id"] for item in directive["accepted_recommendations"]],
            "rejected_recommendation_ids": [item["recommendation_id"] for item in directive["rejected_recommendations"]],
        },
        "workflow_id": subject.get("workflow_id"),
        "run_id": subject.get("run_id"),
        "stage_id": subject.get("stage_id"),
        "attempt_id": subject.get("attempt_id"),
    }
    if source_final_packet_draft is not None:
        fresh_job["final_packet_draft"] = source_final_packet_draft
    fresh_job_path = source_cycle["derived_paths"]["revised_review_job"]
    _write_once_json(root, fresh_job_path, fresh_job, label="revised review job")
    revision_result = {
        "schema_version": REVISION_RESULT_SCHEMA_VERSION,
        "result_id": normalize_slug(f"revision_result_{source_review_cycle_id}"),
        "created_at": directive["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "source_review_cycle_id": source_review_cycle_id,
        "source_subject_id": subject["subject_id"],
        "directive_path": revision["directive_path"],
        "directive_sha256": revision["directive_sha256"],
        "revision_job_path": revision["job_path"],
        "revision_job_sha256": revision["job_sha256"],
        "operator_decision_path": revision["operator_decision_path"],
        "operator_decision_sha256": revision["operator_decision_sha256"],
        "revised_artifacts": revised_records,
        "accepted_recommendation_ids": fresh_job["revision_lineage"]["accepted_recommendation_ids"],
        "rejected_recommendation_ids": fresh_job["revision_lineage"]["rejected_recommendation_ids"],
        "new_review_cycle_id": new_review_cycle_id,
        "revised_review_job_path": fresh_job_path,
        "revised_review_job_sha256": sha256_file(resolve_under_root(root, fresh_job_path, must_exist=True)),
    }
    result_path = source_cycle["derived_paths"]["revision_result"]
    _write_once_json(root, result_path, revision_result, "revision_result.schema.json", "revision result")
    result_sha256 = sha256_file(resolve_under_root(root, result_path, must_exist=True))
    if revision.get("status") == "operator_completed":
        revision.update({
            "status": "result_ready",
            "result_path": result_path,
            "result_sha256": result_sha256,
            "new_review_cycle_id": new_review_cycle_id,
        })
        _write_session(root, session_path, session)
    else:
        expected_result = {
            "result_path": result_path,
            "result_sha256": result_sha256,
            "new_review_cycle_id": new_review_cycle_id,
        }
        if any(revision.get(key) != value for key, value in expected_result.items()):
            raise SystemExit("Revision continuation result binding mismatch.")
    session, session_path = _load_session_and_path(root, session_ref)
    source_cycle = _find_cycle(session, source_review_cycle_id)
    revision = source_cycle["revision"]
    if revision.get("status") == "result_ready":
        source_cycle["acceptance_status"] = "superseded"
        revision["status"] = "superseded_by_fresh_review"
        _write_session(root, session_path, session)
    elif revision.get("status") != "superseded_by_fresh_review" or source_cycle.get("acceptance_status") != "superseded":
        raise SystemExit("Revision continuation supersede state is inconsistent.")

    session, session_path = _load_session_and_path(root, session_ref)
    source_cycle = _find_cycle(session, source_review_cycle_id)
    expected_lineage = {
        "source_review_cycle_id": source_review_cycle_id,
        "revision_result_path": result_path,
        "revision_result_sha256": result_sha256,
    }
    matches = [cycle for cycle in session["review_cycles"] if cycle.get("review_cycle_id") == new_review_cycle_id]
    if not matches:
        fresh_cycle = _new_review_cycle_record(
            root=root,
            session_path=session_path,
            review_cycle_id=new_review_cycle_id,
            review_kind=source_cycle["review_kind"],
        )
        fresh_cycle["revision_lineage"] = expected_lineage
        session["review_cycles"].append(fresh_cycle)
        _write_session(root, session_path, session)
        matches = [fresh_cycle]
    if len(matches) != 1 or matches[0].get("review_kind") != source_cycle["review_kind"]:
        raise SystemExit("Revision continuation fresh review-cycle identity mismatch.")
    fresh_cycle = matches[0]
    if fresh_cycle.get("revision_lineage") != expected_lineage:
        raise SystemExit("Revision continuation fresh review lineage mismatch.")
    subject_fields = (fresh_cycle.get("subject_path"), fresh_cycle.get("subject_sha256"), fresh_cycle.get("subject_id"))
    populated = bool(
        fresh_cycle.get("operator_provisional_record")
        or fresh_cycle.get("review_agent_outputs")
        or fresh_cycle.get("review_gates")
        or fresh_cycle.get("consolidation")
        or fresh_cycle.get("quorum")
        or fresh_cycle.get("acceptance_record")
    )
    if any(subject_fields):
        if not all(subject_fields):
            raise SystemExit("Revision-owned fresh review cycle has a partial frozen subject binding.")
        fresh_subject = _load_cycle_subject(root, fresh_cycle)
        if fresh_subject.get("review_cycle_id") != new_review_cycle_id or fresh_subject.get("review_kind") != source_cycle["review_kind"]:
            raise SystemExit("Revision-owned fresh review subject identity mismatch.")
        frozen_job = load_json(resolve_under_root(root, fresh_subject["frozen_job_path"], must_exist=True), "revision-owned fresh review job")
        if _canonical_sha256(frozen_job) != _canonical_sha256(fresh_job):
            raise SystemExit("Revision-owned fresh review subject is not bound to the expected revision job.")
        _verify_subject_artifacts(root, fresh_subject)
    elif populated:
        raise SystemExit("Revision continuation refuses a populated cycle without its exact frozen subject binding.")
    review = _resume_review_cycle(
        root=root,
        session_ref=session_ref,
        review_cycle_id=new_review_cycle_id,
        review_kind=source_cycle["review_kind"],
        job_json=fresh_job_path,
    )
    return {"revision_result": result_path, "new_review_cycle_id": new_review_cycle_id, **review}


def _load_decision(root: Path, path: str | Path, label: str) -> dict[str, Any]:
    payload = load_json(resolve_under_root(root, path, must_exist=True), label)
    supervisor_agents.validate_review_decision(payload)
    return payload


def _recommendation_key(recommendation: dict[str, Any]) -> str:
    normalized_text = " ".join(
        re.sub(
            r"[^\w\s]",
            " ",
            str(recommendation.get("recommendation") or "").casefold(),
        ).split()
    )
    affected = recommendation.get("affected_artifacts") if isinstance(recommendation.get("affected_artifacts"), list) else []
    return json.dumps(
        {
            "recommendation": normalized_text,
            "affected_artifacts": sorted(
                {
                    " ".join(_artifact_item_to_issue_string(item).split())
                    for item in affected
                    if _artifact_item_to_issue_string(item).strip()
                }
            ),
        },
        sort_keys=True,
    )


def _quorum_blocker(role: str, gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "issue_id": normalize_slug(f"{role}_review_gate_blocked"),
        "severity": "blocking",
        "description": f"Required {role} transport, schema, or read-only gate did not pass.",
        "evidence": [
            f"transport_status={gate.get('transport_status')}",
            f"schema_status={gate.get('schema_status')}",
            f"read_only_status={gate.get('read_only_status')}",
        ],
        "affected_artifacts": [str(gate.get("decision_path") or "missing_review_decision")],
    }


def _create_quorum(root: Path, session: dict[str, Any], cycle: dict[str, Any], subject: dict[str, Any]) -> dict[str, Any]:
    gates = cycle.get("review_gates") or {}
    missing = [role for role in REVIEW_ROLES if role not in gates]
    if missing:
        raise SystemExit(f"Review quorum is incomplete; missing invocation gates: {', '.join(missing)}")
    roles = {role: gates[role] for role in REVIEW_ROLES}
    quorum = {
        "schema_version": REVIEW_QUORUM_SCHEMA_VERSION,
        "created_at": cycle["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "review_cycle_id": cycle["review_cycle_id"],
        "subject_id": subject["subject_id"],
        "subject_sha256": cycle["subject_sha256"],
        "roles": roles,
        "quorum_status": "passed" if all(gate["gate_status"] == "passed" for gate in roles.values()) else "blocked",
    }
    path = cycle["derived_paths"]["quorum"]
    _write_once_json(root, path, quorum, "review_quorum.schema.json", "review quorum")
    cycle["quorum"] = {"path": path, "sha256": sha256_file(resolve_under_root(root, path, must_exist=True)), "status": quorum["quorum_status"]}
    return quorum


def consolidate_reviews(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    codex_review: str | Path | None = None,
    claude_review: str | Path | None = None,
    output: str | Path | None = None,
    operator_review: str | Path | None = None,
) -> dict[str, Any]:
    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _session_mutation_lock(session_path):
        with _cycle_transition_lock(session_path, review_cycle_id):
            return _consolidate_reviews_locked(
                root=root,
                session_ref=session_ref,
                review_cycle_id=review_cycle_id,
                codex_review=codex_review,
                claude_review=claude_review,
                output=output,
                operator_review=operator_review,
            )


def _consolidate_reviews_locked(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    codex_review: str | Path | None = None,
    claude_review: str | Path | None = None,
    output: str | Path | None = None,
    operator_review: str | Path | None = None,
) -> dict[str, Any]:
    """Deterministically consolidate operator and independent reviewer findings."""

    session, session_path = _load_session_and_path(root, session_ref)
    cycle = _find_cycle(session, review_cycle_id)
    if cycle.get("consolidation"):
        raise SystemExit("Review cycle consolidation is immutable once recorded.")
    if cycle.get("acceptance_status") == "superseded":
        raise SystemExit("Superseded review cycles cannot be consolidated again.")
    subject = _load_cycle_subject(root, cycle)
    _verify_subject_artifacts(root, subject)
    stored = cycle.get("review_agent_outputs") or {}
    operator_review = operator_review or cycle.get("operator_provisional_record")
    codex_review = codex_review or stored.get("codex_review_agent")
    claude_review = claude_review or stored.get("claude_review_agent")
    expected_paths = {
        "operator_codex": cycle.get("operator_provisional_record"),
        "codex_review_agent": stored.get("codex_review_agent"),
        "claude_review_agent": stored.get("claude_review_agent"),
    }
    supplied_paths = {
        "operator_codex": operator_review,
        "codex_review_agent": codex_review,
        "claude_review_agent": claude_review,
    }
    for role in REVIEW_ROLES:
        if not isinstance(expected_paths[role], str) or not expected_paths[role]:
            raise SystemExit(f"Review cycle is missing the supervisor-recorded {role} decision.")
        if resolve_under_root(root, supplied_paths[role], must_exist=True) != resolve_under_root(root, expected_paths[role], must_exist=True):
            raise SystemExit(f"Arbitrary {role} decision paths are rejected; use the cycle-recorded decision.")
    verified = _verified_cycle_review_decisions(root=root, session=session, cycle=cycle, subject=subject)
    if cycle.get("review_kind") in REVIEW_KINDS_REQUIRING_OPERATOR_PROVISIONAL:
        _require_successful_operator_provisional(root=root, session=session, cycle=cycle)
    operator = verified["operator_codex"]
    codex = verified["codex_review_agent"]
    claude = verified["claude_review_agent"]
    quorum = _create_quorum(root, session, cycle, subject)

    output_json_path = _require_derived_path(root, output, cycle["derived_paths"]["consolidation_json"], "consolidation output")
    output_md_path = output_json_path.with_suffix(".md")
    consolidated_recommendations: list[dict[str, Any]] = []
    recommendation_groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for source in (operator, codex, claude):
        source_agent = str(source["actor_role"])
        for original in source.get("recommendations", []):
            if isinstance(original, dict):
                recommendation_groups.setdefault(_recommendation_key(original), []).append((source_agent, dict(original)))
    recommendation_provenance: list[dict[str, Any]] = []
    for key in sorted(recommendation_groups):
        grouped = recommendation_groups[key]
        sources = sorted({role for role, _rec in grouped})
        originals = sorted(str(rec.get("recommendation_id") or "") for _role, rec in grouped)
        representative = dict(grouped[0][1])
        all_evidence = [item for _role, rec in grouped for item in rec.get("evidence", []) if isinstance(item, dict)]
        representative["recommendation_id"] = normalize_slug(f"group_{sha256_text(key)[:16]}")
        representative["source_agent"] = ",".join(sources)
        representative["evidence"] = all_evidence
        representative["consolidation_recommendation"] = "accepted_for_operator_review" if all_evidence else "needs_operator_judgment"
        representative.pop("operator_decision", None)
        consolidated_recommendations.append(representative)
        severities = sorted({str(rec.get("severity") or "") for _role, rec in grouped})
        recommendation_provenance.append(
            {
                "group_id": representative["recommendation_id"],
                "source_agents": sources,
                "source_recommendation_ids": originals,
                "source_recommendations": [
                    {
                        "source_agent": role,
                        "recommendation_id": str(rec.get("recommendation_id") or ""),
                        "severity": str(rec.get("severity") or ""),
                    }
                    for role, rec in sorted(
                        grouped,
                        key=lambda item: (
                            item[0],
                            str(item[1].get("recommendation_id") or ""),
                        ),
                    )
                ],
                "severity_values": severities,
                "severity_disagreement": len(severities) > 1,
                "normalized_key": key,
            }
        )

    groups_by_artifacts: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for provenance in recommendation_provenance:
        normalized = json.loads(provenance["normalized_key"])
        artifacts_key = tuple(normalized.get("affected_artifacts") or [])
        if artifacts_key:
            groups_by_artifacts.setdefault(artifacts_key, []).append(provenance)
    for related in groups_by_artifacts.values():
        if len(related) < 2:
            continue
        group_ids = sorted(str(item["group_id"]) for item in related)
        for provenance in related:
            provenance["possibly_related_group_ids"] = [
                group_id for group_id in group_ids if group_id != provenance["group_id"]
            ]

    sources = [item for item in (operator, codex, claude) if item is not None]
    blocking_issues: list[Any] = []
    non_blocking: list[Any] = []
    unsupported_claims: list[Any] = []
    evidence: list[Any] = []
    reviewed_artifacts: list[Any] = []
    missing_artifacts: list[Any] = []
    blocker_provenance: list[dict[str, Any]] = []
    for source in sources:
        source_role = str(source.get("actor_role"))
        for issue in source.get("blocking_issues", []):
            if isinstance(issue, dict):
                preserved = dict(issue)
                preserved["issue_id"] = normalize_slug(f"{source_role}_{issue.get('issue_id') or 'blocker'}")
                blocking_issues.append(preserved)
                blocker_provenance.append({"issue_id": preserved["issue_id"], "source_agent": source_role, "source_decision_path": source.get("json_report_path"), "source_issue_id": issue.get("issue_id")})
        if source.get("approval_decision") in {"do_not_approve", "blocked"} and not source.get("blocking_issues"):
            preserved = {
                "issue_id": normalize_slug(f"{source_role}_decision_blocks_progression"),
                "severity": "blocking",
                "description": f"{source_role} returned {source.get('approval_decision')} without a structured blocker.",
                "evidence": [str(source.get("summary") or "Reviewer did not approve progression.")],
                "affected_artifacts": [str(source.get("json_report_path") or "review_decision")],
            }
            blocking_issues.append(preserved)
            blocker_provenance.append({"issue_id": preserved["issue_id"], "source_agent": source_role, "source_decision_path": source.get("json_report_path"), "source_issue_id": None})
        non_blocking.extend(source.get("non_blocking_improvements", []))
        unsupported_claims.extend(source.get("unsupported_claims", []))
        evidence.extend(source.get("evidence", []))
        reviewed_artifacts.extend(source.get("reviewed_artifacts", []))
        missing_artifacts.extend(source.get("missing_artifacts", []))
    for role, gate in quorum["roles"].items():
        if gate["gate_status"] != "passed":
            blocker = _quorum_blocker(role, gate)
            blocking_issues.append(blocker)
            blocker_provenance.append({"issue_id": blocker["issue_id"], "source_agent": "supervisor", "source_decision_path": gate["decision_path"]})

    decision = {
        "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
        "decision_id": f"consolidation_{review_cycle_id}",
        "created_at": cycle["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "workflow_id": subject.get("workflow_id"),
        "run_id": subject.get("run_id"),
        "stage_id": subject.get("stage_id"),
        "review_cycle_id": review_cycle_id,
        "review_kind": "consolidation",
        "actor_role": "consolidation_pass",
        "agent_command_id": None,
        "status": "succeeded",
        "approval_decision": "do_not_approve" if blocking_issues else "approve_with_conditions",
        "summary": "Deterministic consolidation of independent review findings. This is not final operator acceptance.",
        "markdown_report_path": relpath(root, output_md_path),
        "json_report_path": relpath(root, output_json_path),
        "reviewed_artifacts": reviewed_artifacts,
        "missing_artifacts": missing_artifacts,
        "blocking_issues": blocking_issues,
        "non_blocking_improvements": non_blocking,
        "recommendations": consolidated_recommendations,
        "unsupported_claims": unsupported_claims,
        "evidence": evidence,
        "command": None,
        "read_only_check": None,
        "validation_errors": [],
        "next_action": "proceed_to_operator_acceptance",
    }
    supervisor_agents.validate_review_decision(decision)
    _write_once_json(root, output_json_path, decision, label="consolidated review")
    provenance_path = output_json_path.with_name("consolidation.provenance.json")
    _write_once_json(
        root,
        provenance_path,
        {
            "schema_version": "responses_runner_v2.consolidation_provenance.v1",
            "review_cycle_id": review_cycle_id,
            "subject_id": subject["subject_id"],
            "recommendation_groups": recommendation_provenance,
            "blockers": blocker_provenance,
            "source_decisions": {role: {"path": gate["decision_path"], "sha256": gate["decision_sha256"]} for role, gate in quorum["roles"].items()},
        },
        label="consolidation provenance",
    )
    lines = [
        "# Consolidated Review",
        "",
        "This report preserves reviewer provenance and does not create final operator acceptance.",
        "",
        f"- review_cycle_id: {review_cycle_id}",
        f"- recommendation_count: {len(consolidated_recommendations)}",
        f"- blocking_issue_count: {len(blocking_issues)}",
        "",
        "## Recommendations",
        "",
    ]
    if not consolidated_recommendations:
        lines.append("None.")
    for rec in consolidated_recommendations:
        lines.append(f"- {rec.get('recommendation_id')}: {rec.get('consolidation_recommendation')} — {rec.get('recommendation')}")
    _write_once_text(root, output_md_path, "\n".join(lines).rstrip() + "\n", "consolidated review markdown")

    cycle["consolidation"] = relpath(root, output_json_path)
    cycle["consolidation_sha256"] = sha256_file(output_json_path)
    cycle["consolidation_markdown_sha256"] = sha256_file(output_md_path)
    cycle["consolidation_provenance"] = {"path": relpath(root, provenance_path), "sha256": sha256_file(provenance_path)}
    session["consolidations"].append(
        {
            "review_cycle_id": review_cycle_id,
            "consolidated_review_json": relpath(root, output_json_path),
            "consolidated_review_md": relpath(root, output_md_path),
            "recommendation_counts": {
                "total": len(consolidated_recommendations),
                "accepted_for_operator_review": sum(1 for rec in consolidated_recommendations if rec.get("consolidation_recommendation") == "accepted_for_operator_review"),
                "needs_operator_judgment": sum(1 for rec in consolidated_recommendations if rec.get("consolidation_recommendation") == "needs_operator_judgment"),
                "duplicate": sum(1 for rec in consolidated_recommendations if rec.get("consolidation_recommendation") == "duplicate"),
            },
        }
    )
    _write_session(root, session_path, session)
    return decision


def _load_applied_change_evidence(root: Path, evidence_path: str | Path | None) -> dict[str, Any]:
    if evidence_path is None:
        return {"recommendations": {}}
    payload = load_json(resolve_under_root(root, evidence_path, must_exist=True), "applied change evidence")
    recs = payload.get("recommendations")
    if not isinstance(recs, dict):
        raise SystemExit("applied change evidence must contain recommendations object keyed by recommendation id.")
    return payload


def _accepted_change_payload(evidence_payload: dict[str, Any], rec_id: str) -> dict[str, Any] | None:
    recs = evidence_payload.get("recommendations")
    if not isinstance(recs, dict):
        return None
    item = recs.get(rec_id)
    if not isinstance(item, dict):
        return None
    changes = item.get("changes_applied")
    validation = item.get("validation_evidence")
    rationale = item.get("operator_rationale")
    if not isinstance(changes, list) or not changes:
        return None
    if not isinstance(validation, list) or not validation:
        return None
    if not isinstance(rationale, str) or not rationale.strip():
        return None
    return item


def _evidence_item_to_issue_string(item: Any) -> str:
    if isinstance(item, str) and item.strip():
        return item.strip()
    if isinstance(item, dict):
        source = str(item.get("artifact_path") or item.get("source") or "").strip()
        summary = str(item.get("quote_or_summary") or item.get("summary") or item.get("evidence") or "").strip()
        if source and summary:
            return f"{source}: {summary}"
        if summary:
            return summary
        if source:
            return source
    rendered = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
    return rendered.strip() or "Recommendation was rejected during operator acceptance."


def _artifact_item_to_issue_string(item: Any) -> str:
    if isinstance(item, str) and item.strip():
        return item.strip()
    if isinstance(item, dict):
        for key in ("artifact_path", "path", "source", "name"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    rendered = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
    return rendered.strip() or "unknown_artifact"


def _rejected_blocking_recommendation_issue(rec: dict[str, Any]) -> dict[str, Any]:
    rec_id = str(rec.get("recommendation_id") or "recommendation")
    evidence_items = rec.get("evidence") if isinstance(rec.get("evidence"), list) else []
    artifact_items = rec.get("affected_artifacts") if isinstance(rec.get("affected_artifacts"), list) else []
    evidence = [_evidence_item_to_issue_string(item) for item in evidence_items]
    if rec.get("rejected_reason"):
        evidence.append(str(rec["rejected_reason"]))
    return {
        "issue_id": normalize_slug(f"{rec_id}_rejected_blocking_recommendation"),
        "severity": rec.get("severity") if rec.get("severity") in {"critical", "blocking", "high", "medium", "low"} else "blocking",
        "description": str(rec.get("recommendation") or "Blocking recommendation was rejected during operator acceptance."),
        "evidence": evidence or ["Blocking recommendation was not accepted by the operator acceptance pass."],
        "affected_artifacts": [_artifact_item_to_issue_string(item) for item in artifact_items] or ["unknown_artifact"],
        "source_recommendation_id": rec_id,
    }


def _load_blocker_resolutions(
    *,
    root: Path,
    session: dict[str, Any],
    cycle: dict[str, Any],
    subject: dict[str, Any],
    consolidation_path: Path,
    resolution_paths: Sequence[str | Path],
) -> tuple[set[str], list[dict[str, Any]]]:
    cleared: set[str] = set()
    records: list[dict[str, Any]] = []
    recorded_by_path = {
        str(item["path"]): item
        for item in cycle.get("blocker_resolutions", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    consolidation_rel = relpath(root, consolidation_path)
    consolidation_sha = sha256_file(consolidation_path)
    for raw in resolution_paths:
        path = resolve_under_root(root, raw, must_exist=True)
        if not path.is_relative_to(resolve_under_root(root, cycle["derived_paths"]["resolutions_dir"], must_exist=False)):
            raise SystemExit("Blocker resolution must be stored in the cycle-derived resolutions directory.")
        path_rel = relpath(root, path)
        recorded = recorded_by_path.get(path_rel)
        if not isinstance(recorded, dict) or sha256_file(path) != recorded.get("sha256"):
            raise SystemExit("Blocker resolution does not match its cycle-recorded immutable hash.")
        payload = load_json(path, "blocker resolution")
        supervisor_artifacts.validate_against_schema(payload, "blocker_resolution.v2.schema.json", "blocker resolution")
        expected = {
            "supervisor_session_id": session["supervisor_session_id"],
            "review_cycle_id": cycle["review_cycle_id"],
            "subject_id": subject["subject_id"],
            "subject_sha256": cycle["subject_sha256"],
            "source_decision_path": consolidation_rel,
            "source_decision_sha256": consolidation_sha,
        }
        mismatches = [key for key, value in expected.items() if payload.get(key) != value]
        if mismatches:
            raise SystemExit(f"Blocker resolution binding mismatch: {', '.join(mismatches)}")
        _verify_blocker_resolution_files(root, payload)
        blocker_id = str(payload["blocker_id"])
        if blocker_id != recorded.get("blocker_id"):
            raise SystemExit("Blocker resolution blocker_id does not match its cycle record.")
        if blocker_id in {item["blocker_id"] for item in records}:
            raise SystemExit(f"Duplicate blocker resolution: {blocker_id}")
        if payload["resolution"] in CLEARING_RESOLUTIONS:
            cleared.add(blocker_id)
        records.append({"path": path_rel, "sha256": recorded["sha256"], "blocker_id": blocker_id, "resolution": payload["resolution"]})
    return cleared, records


def _verify_blocker_resolution_files(root: Path, payload: dict[str, Any]) -> None:
    affected_paths: set[str] = set()
    for artifact in payload.get("affected_artifacts", []):
        path = resolve_under_root(root, artifact["path"], must_exist=True)
        if not path.is_file() or sha256_file(path) != artifact["sha256"]:
            raise SystemExit(f"Blocker resolution affected-artifact hash mismatch: {artifact['path']}")
        affected_paths.add(relpath(root, path))
    for change in payload.get("applied_changes", []):
        path = resolve_under_root(root, change["path"], must_exist=True)
        if relpath(root, path) not in affected_paths:
            raise SystemExit("Applied blocker-resolution change must target a declared affected artifact.")
        if sha256_file(path) != change["after_sha256"]:
            raise SystemExit(f"Blocker resolution applied-change hash mismatch: {change['path']}")
        if change.get("before_sha256") == change.get("after_sha256"):
            raise SystemExit("Applied blocker-resolution change must record an actual hash change.")
    for validation in payload.get("validation_evidence", []):
        path_value = validation.get("artifact_path")
        hash_value = validation.get("artifact_sha256")
        if (path_value is None) != (hash_value is None):
            raise SystemExit("Validation evidence artifact path and hash must be supplied together.")
        if path_value is not None:
            path = resolve_under_root(root, path_value, must_exist=True)
            if not path.is_file() or sha256_file(path) != hash_value:
                raise SystemExit(f"Blocker resolution validation-evidence hash mismatch: {path_value}")


def record_blocker_resolution(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    blocker_id: str,
    resolution: str,
    evidence: Sequence[str],
    affected_artifacts: Sequence[dict[str, str]],
    applied_changes: Sequence[dict[str, Any]],
    validation_evidence: Sequence[dict[str, Any]],
    operator_rationale: str,
    accepted_risk_rationale: str | None = None,
) -> dict[str, Any]:
    """Create an immutable cycle-local resolution bound to the exact consolidation."""

    if resolution not in {"resolved", "accepted_risk", "superseded", "still_blocking"}:
        raise SystemExit(f"Unsupported blocker resolution: {resolution}")
    if not evidence or any(not str(item).strip() for item in evidence):
        raise SystemExit("Blocker resolution requires non-empty evidence.")
    if not operator_rationale.strip() or not affected_artifacts or not validation_evidence:
        raise SystemExit("Blocker resolution requires affected artifact hashes, validation evidence, and operator rationale.")
    if resolution in {"resolved", "superseded"} and not applied_changes:
        raise SystemExit("Resolved or superseded blockers require at least one applied change record.")
    if resolution == "accepted_risk" and not str(accepted_risk_rationale or "").strip():
        raise SystemExit("Accepted-risk blocker resolution requires an explicit accepted-risk rationale.")
    session, session_path = _load_session_and_path(root, session_ref)
    cycle = _find_cycle(session, review_cycle_id)
    subject = _load_cycle_subject(root, cycle)
    consolidation_ref = cycle.get("consolidation")
    if not isinstance(consolidation_ref, str):
        raise SystemExit("Blocker resolution requires the supervisor-created consolidation.")
    consolidation_path = resolve_under_root(root, consolidation_ref, must_exist=True)
    if sha256_file(consolidation_path) != cycle.get("consolidation_sha256"):
        raise SystemExit("Consolidation changed before blocker resolution.")
    consolidation = _load_decision(root, consolidation_path, "consolidated review")
    blocker_ids = {str(item.get("issue_id")) for item in consolidation.get("blocking_issues", []) if isinstance(item, dict)}
    if blocker_id not in blocker_ids:
        raise SystemExit(f"Unknown consolidation blocker_id: {blocker_id}")
    blocker = next(item for item in consolidation.get("blocking_issues", []) if isinstance(item, dict) and str(item.get("issue_id")) == blocker_id)
    declared_affected = {_artifact_item_to_issue_string(item) for item in blocker.get("affected_artifacts", [])}
    normalized_affected: list[dict[str, str]] = []
    for item in affected_artifacts:
        path = resolve_under_root(root, item.get("path"), must_exist=True)
        path_rel = relpath(root, path)
        if path_rel not in declared_affected:
            raise SystemExit(f"Blocker resolution affected path was not named by the blocker: {path_rel}")
        if sha256_file(path) != item.get("sha256"):
            raise SystemExit(f"Blocker resolution affected-artifact hash mismatch: {path_rel}")
        normalized_affected.append({"path": path_rel, "sha256": str(item["sha256"])})
    normalized_changes: list[dict[str, Any]] = []
    for item in applied_changes:
        path = resolve_under_root(root, item.get("path"), must_exist=True)
        normalized_changes.append({**item, "path": relpath(root, path)})
    normalized_validation: list[dict[str, Any]] = []
    for item in validation_evidence:
        normalized = dict(item)
        if item.get("artifact_path") is not None:
            normalized["artifact_path"] = relpath(root, resolve_under_root(root, item["artifact_path"], must_exist=True))
        normalized_validation.append(normalized)
    output = Path(cycle["derived_paths"]["resolutions_dir"]) / f"{normalize_slug(blocker_id)}.resolution.json"
    payload = {
        "schema_version": "responses_runner_v2.blocker_resolution.v2",
        "resolution_id": normalize_slug(f"resolution_{review_cycle_id}_{blocker_id}"),
        "created_at": cycle["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "review_cycle_id": review_cycle_id,
        "subject_id": subject["subject_id"],
        "subject_sha256": cycle["subject_sha256"],
        "blocker_id": blocker_id,
        "source_decision_path": relpath(root, consolidation_path),
        "source_decision_sha256": sha256_file(consolidation_path),
        "resolution": resolution,
        "affected_artifacts": normalized_affected,
        "applied_changes": normalized_changes,
        "accepted_risk_rationale": str(accepted_risk_rationale).strip() if accepted_risk_rationale else None,
        "validation_evidence": normalized_validation,
        "operator_rationale": operator_rationale.strip(),
        "evidence": [str(item) for item in evidence],
    }
    _verify_blocker_resolution_files(root, payload)
    output_rel = _write_once_json(root, output, payload, "blocker_resolution.v2.schema.json", "blocker resolution")
    cycle.setdefault("blocker_resolutions", []).append({"path": output_rel, "sha256": sha256_file(resolve_under_root(root, output_rel, must_exist=True)), "blocker_id": blocker_id})
    _write_session(root, session_path, session)
    return payload


def accept_consolidated_review(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    consolidated_review: str | Path | None = None,
    accepted_recommendation_ids: Sequence[str],
    output: str | Path | None = None,
    applied_change_evidence: str | Path | None = None,
    blocker_resolutions: Sequence[str | Path] = (),
) -> dict[str, Any]:
    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _session_mutation_lock(session_path):
        with _cycle_transition_lock(session_path, review_cycle_id):
            return _accept_consolidated_review_locked(
                root=root,
                session_ref=session_ref,
                review_cycle_id=review_cycle_id,
                consolidated_review=consolidated_review,
                accepted_recommendation_ids=accepted_recommendation_ids,
                output=output,
                applied_change_evidence=applied_change_evidence,
                blocker_resolutions=blocker_resolutions,
            )


def _accept_consolidated_review_locked(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    consolidated_review: str | Path | None = None,
    accepted_recommendation_ids: Sequence[str],
    output: str | Path | None = None,
    applied_change_evidence: str | Path | None = None,
    blocker_resolutions: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Create the operator selective-acceptance record for consolidated recommendations."""

    session, session_path = _load_session_and_path(root, session_ref)
    cycle = _find_cycle(session, review_cycle_id)
    if cycle.get("acceptance_record"):
        raise SystemExit("Review cycle operator acceptance is immutable once recorded.")
    if isinstance(cycle.get("revision"), dict):
        raise SystemExit("Review cycles with an active revision cannot be accepted; accept only the fresh revised review cycle.")
    if cycle.get("acceptance_status") == "superseded" or cycle.get("revision", {}).get("status") == "superseded_by_fresh_review":
        raise SystemExit("Superseded review cycles cannot be accepted; accept only the fresh revised review cycle.")
    subject = _load_cycle_subject(root, cycle)
    _verify_subject_artifacts(root, subject)
    _verified_cycle_review_decisions(root=root, session=session, cycle=cycle, subject=subject)
    expected_consolidation = cycle.get("consolidation")
    if not isinstance(expected_consolidation, str):
        raise SystemExit("Operator acceptance requires the supervisor-created consolidation.")
    consolidated_review = consolidated_review or expected_consolidation
    consolidation_path = resolve_under_root(root, consolidated_review, must_exist=True)
    if consolidation_path != resolve_under_root(root, expected_consolidation, must_exist=True):
        raise SystemExit("Arbitrary consolidation paths are rejected; use the cycle-recorded consolidation.")
    if sha256_file(consolidation_path) != cycle.get("consolidation_sha256"):
        raise SystemExit("Consolidation changed after it was recorded.")
    consolidated = _load_decision(root, consolidation_path, "consolidated review")
    if consolidated.get("review_cycle_id") != review_cycle_id or consolidated.get("supervisor_session_id") != session["supervisor_session_id"]:
        raise SystemExit("Consolidation identity does not match this session and cycle.")
    for key in ("workflow_id", "run_id", "stage_id"):
        if consolidated.get(key) != subject.get(key):
            raise SystemExit(f"Consolidation {key} does not match the immutable review subject.")
    quorum_ref = cycle.get("quorum")
    if not isinstance(quorum_ref, dict):
        raise SystemExit("Operator acceptance requires an exact three-role review quorum record.")
    quorum_path = resolve_under_root(root, quorum_ref["path"], must_exist=True)
    if sha256_file(quorum_path) != quorum_ref.get("sha256"):
        raise SystemExit("Review quorum record hash mismatch.")
    quorum = load_json(quorum_path, "review quorum")
    supervisor_artifacts.validate_against_schema(quorum, "review_quorum.schema.json", "review quorum")
    recorded_resolution_paths = [item["path"] for item in cycle.get("blocker_resolutions", []) if isinstance(item, dict) and isinstance(item.get("path"), str)]
    resolution_inputs = list(blocker_resolutions) if blocker_resolutions else recorded_resolution_paths
    if blocker_resolutions and {relpath(root, resolve_under_root(root, item, must_exist=True)) for item in blocker_resolutions} != set(recorded_resolution_paths):
        raise SystemExit("Acceptance may use only supervisor-recorded blocker resolutions for this cycle.")
    cleared_blockers, resolution_records = _load_blocker_resolutions(
        root=root,
        session=session,
        cycle=cycle,
        subject=subject,
        consolidation_path=consolidation_path,
        resolution_paths=resolution_inputs,
    )
    accepted = set(accepted_recommendation_ids)
    evidence_payload = _load_applied_change_evidence(root, applied_change_evidence)
    output_json_path = _require_derived_path(root, output, cycle["derived_paths"]["acceptance_json"], "operator acceptance output")
    output_md_path = output_json_path.with_suffix(".md")

    recommendations = []
    for original in consolidated.get("recommendations", []):
        rec = dict(original)
        rec_id = str(rec.get("recommendation_id"))
        reviewer_evidence = rec.get("evidence")
        has_reviewer_evidence = isinstance(reviewer_evidence, list) and len(reviewer_evidence) > 0
        applied_payload = _accepted_change_payload(evidence_payload, rec_id)
        if rec_id in accepted and has_reviewer_evidence and applied_payload is not None:
            rec["operator_decision"] = "accepted"
            rec["decision_rationale"] = str(applied_payload["operator_rationale"])
            rec["changes_applied"] = applied_payload["changes_applied"]
            rec["validation_evidence"] = applied_payload["validation_evidence"]
            rec["rejected_reason"] = ""
        elif rec_id in accepted and not has_reviewer_evidence:
            rec["operator_decision"] = "rejected"
            rec["decision_rationale"] = "Rejected because the recommendation lacks reviewer evidence."
            rec["changes_applied"] = []
            rec["validation_evidence"] = []
            rec["rejected_reason"] = "Unsupported recommendation; operator cannot accept without evidence."
        elif rec_id in accepted and applied_payload is None:
            rec["operator_decision"] = "rejected"
            rec["decision_rationale"] = "Rejected because no concrete applied-change evidence and validation evidence were supplied."
            rec["changes_applied"] = []
            rec["validation_evidence"] = []
            rec["rejected_reason"] = "Missing applied-change evidence; supervisor does not synthesize changes_applied."
        else:
            rec["operator_decision"] = "rejected"
            rec["decision_rationale"] = "Rejected or deferred because it was not selected for evidence-supported acceptance."
            rec["changes_applied"] = []
            rec["validation_evidence"] = []
            rec["rejected_reason"] = "Not accepted by operator selective-acceptance pass."
        recommendations.append(rec)

    rejected_blocking_recommendations = [
        rec
        for rec in recommendations
        if rec.get("operator_decision") == "rejected"
        and rec.get("severity") in {"critical", "blocking"}
        and rec.get("consolidation_recommendation") not in {"duplicate", "already_satisfied", "out_of_scope"}
    ]
    blocking_after_acceptance = [
        dict(issue)
        for issue in consolidated.get("blocking_issues", [])
        if isinstance(issue, dict) and str(issue.get("issue_id")) not in cleared_blockers
    ]
    blocking_after_acceptance.extend(_rejected_blocking_recommendation_issue(rec) for rec in rejected_blocking_recommendations)
    if quorum.get("quorum_status") != "passed":
        blocking_after_acceptance.append(
            {
                "issue_id": "required_review_quorum_not_satisfied",
                "severity": "blocking",
                "description": "Operator acceptance cannot override a failed transport, schema, or read-only reviewer gate.",
                "evidence": [f"quorum_status={quorum.get('quorum_status')}"],
                "affected_artifacts": [quorum_ref["path"]],
            }
        )

    if cycle.get("review_kind") == "scaffold":
        latest = session["scaffold_versions"][-1] if session.get("scaffold_versions") else None
        if not isinstance(latest, dict) or latest.get("version_id") != subject.get("scaffold_version_id"):
            blocking_after_acceptance.append(
                {
                    "issue_id": "stale_scaffold_review_subject",
                    "severity": "blocking",
                    "description": "The reviewed scaffold is not the current staged scaffold version.",
                    "evidence": [f"reviewed={subject.get('scaffold_version_id')}", f"current={latest.get('version_id') if latest else None}"],
                    "affected_artifacts": [subject.get("reviewed_artifact_manifest_path")],
                }
            )

    # Acceptance is the accountable step with full workspace access. Read-only
    # reviewers may report artifacts as missing simply because their sandbox
    # cannot read them (e.g. gitignored run directories); verify each claim
    # against the filesystem here. Verified-present claims are cleared with a
    # note; truly missing required artifacts become blocking issues and yield
    # a graceful do_not_approve record instead of an unwritable approve.
    verified_missing: list[dict[str, Any]] = []
    verified_present_paths: list[str] = []
    for item in consolidated.get("missing_artifacts", []) or []:
        entry = dict(item) if isinstance(item, dict) else {"path": str(item), "required": True, "reason": "Required artifact was missing."}
        path_value = str(entry.get("path") or "")
        candidate = Path(path_value)
        if not candidate.is_absolute():
            candidate = root / path_value
        if path_value and candidate.exists():
            verified_present_paths.append(path_value)
            continue
        verified_missing.append(entry)
        if entry.get("required", True):
            blocking_after_acceptance.append(
                {
                    "issue_id": f"missing-artifact-{len(blocking_after_acceptance) + 1}",
                    "severity": "blocking",
                    "description": f"Required artifact is missing at acceptance time: {path_value}",
                    "evidence": [entry.get("reason") or "Required artifact was missing."],
                    "affected_artifacts": [path_value],
                }
            )

    approval = "approve" if not blocking_after_acceptance else "do_not_approve"
    decision = {
        "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
        "decision_id": f"operator_acceptance_{review_cycle_id}",
        "created_at": cycle["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "workflow_id": consolidated.get("workflow_id"),
        "run_id": consolidated.get("run_id"),
        "stage_id": consolidated.get("stage_id"),
        "review_cycle_id": review_cycle_id,
        "review_kind": "operator_acceptance",
        "actor_role": "operator_codex",
        "agent_command_id": None,
        "status": "succeeded",
        "approval_decision": approval,
        "summary": (
            "Operator selective acceptance record. Accepted recommendations require applied-change evidence."
            + (
                f" Verified present at acceptance ({len(verified_present_paths)} reviewer-reported paths): "
                + ", ".join(verified_present_paths)
                if verified_present_paths
                else ""
            )
        ),
        "markdown_report_path": relpath(root, output_md_path),
        "json_report_path": relpath(root, output_json_path),
        "reviewed_artifacts": consolidated.get("reviewed_artifacts", []),
        "missing_artifacts": verified_missing,
        "blocking_issues": blocking_after_acceptance,
        "non_blocking_improvements": consolidated.get("non_blocking_improvements", []),
        "recommendations": recommendations,
        "unsupported_claims": consolidated.get("unsupported_claims", []),
        "evidence": consolidated.get("evidence", []),
        "command": None,
        "read_only_check": None,
        "validation_errors": [],
        "next_action": "create_review_bundle" if approval == "approve" else "blocked",
    }
    supervisor_agents.validate_review_decision(decision)
    _write_once_json(root, output_json_path, decision, label="operator acceptance")
    lines = ["# Operator Acceptance", "", "Accepted recommendations include concrete applied-change evidence; no changes are synthesized.", ""]
    for rec in recommendations:
        lines.append(f"- {rec.get('recommendation_id')}: {rec.get('operator_decision')} — {rec.get('decision_rationale')}")
    _write_once_text(root, output_md_path, "\n".join(lines).rstrip() + "\n", "operator acceptance markdown")

    cycle["acceptance_status"] = "accepted" if approval == "approve" else "blocked"
    cycle["acceptance_record"] = relpath(root, output_json_path)
    cycle["acceptance_record_sha256"] = sha256_file(output_json_path)
    cycle["acceptance_markdown_sha256"] = sha256_file(output_md_path)
    binding = {
        "schema_version": ACCEPTANCE_BINDING_SCHEMA_VERSION,
        "created_at": cycle["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "review_cycle_id": review_cycle_id,
        "subject_id": subject["subject_id"],
        "subject_sha256": cycle["subject_sha256"],
        "quorum_path": quorum_ref["path"],
        "quorum_sha256": quorum_ref["sha256"],
        "consolidation_path": relpath(root, consolidation_path),
        "consolidation_sha256": sha256_file(consolidation_path),
        "acceptance_path": relpath(root, output_json_path),
        "acceptance_sha256": sha256_file(output_json_path),
        "blocker_resolutions": resolution_records,
        "approval_decision": approval,
    }
    binding_path = cycle["derived_paths"]["acceptance_binding"]
    _write_once_json(root, binding_path, binding, "operator_acceptance_binding.schema.json", "operator acceptance binding")
    cycle["acceptance_binding"] = {"path": binding_path, "sha256": sha256_file(resolve_under_root(root, binding_path, must_exist=True))}
    if cycle.get("review_kind") == "scaffold" and approval == "approve":
        bound = next(item for item in session["scaffold_versions"] if item.get("version_id") == subject["scaffold_version_id"])
        bound["approval_status"] = "accepted"
        bound["accepted_subject_id"] = subject["subject_id"]
    session["operator_acceptance_records"].append(relpath(root, output_json_path))
    session["status"] = "ready_to_launch" if approval == "approve" else "blocked"
    session["current_phase"] = "stage_execution" if approval == "approve" else "acceptance"
    _write_session(root, session_path, session)
    return decision


def _accepted_scaffold_cycle(root: Path, session: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not session["scaffold_versions"]:
        raise SystemExit("No scaffold has been staged.")
    latest = session["scaffold_versions"][-1]
    if latest.get("approval_status") != "accepted":
        raise SystemExit("Scaffold launch is blocked until operator acceptance is recorded.")
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for cycle in session["review_cycles"]:
        if cycle.get("review_kind") != "scaffold" or cycle.get("acceptance_status") != "accepted":
            continue
        subject = _load_cycle_subject(root, cycle)
        if subject.get("scaffold_version_id") == latest.get("version_id"):
            matches.append((cycle, subject))
    if len(matches) != 1:
        raise SystemExit("Scaffold launch requires exactly one accepted review cycle bound to the current scaffold version.")
    cycle, subject = matches[0]
    _verify_subject_artifacts(root, subject)
    manifest_path = resolve_under_root(root, latest["hash_manifest_path"], must_exist=True)
    _verify_hash_manifest(root, manifest_path)
    if sha256_file(manifest_path) != subject.get("scaffold_sha256"):
        raise SystemExit("Current scaffold hash no longer matches the accepted review subject.")
    binding_ref = cycle.get("acceptance_binding")
    if not isinstance(binding_ref, dict):
        raise SystemExit("Accepted scaffold is missing its hash-bound acceptance record.")
    binding_path = resolve_under_root(root, binding_ref["path"], must_exist=True)
    if sha256_file(binding_path) != binding_ref.get("sha256"):
        raise SystemExit("Scaffold acceptance binding hash mismatch.")
    binding = load_json(binding_path, "operator acceptance binding")
    if binding.get("approval_decision") != "approve" or binding.get("subject_id") != subject["subject_id"]:
        raise SystemExit("Scaffold acceptance binding does not approve the current review subject.")
    return cycle, subject


def assert_scaffold_launch_allowed(*, root: Path, session_ref: str | Path) -> None:
    """Raise if the latest staged scaffold lacks the required accepted review cycle."""

    session, _session_path = _load_session_and_path(root, session_ref)
    _accepted_scaffold_cycle(root, session)


def _register_run_result(root: Path, session: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    run_dir = resolve_under_root(root, result["run_dir"], must_exist=True)
    manifest_path = resolve_under_root(root, result["run_manifest_path"], must_exist=True)
    manifest = load_json(manifest_path, "run manifest")
    record = {
        "run_id": manifest["run_id"],
        "workflow_id": manifest["workflow_id"],
        "run_dir": relpath(root, run_dir),
        "run_manifest_path": relpath(root, manifest_path),
        "run_manifest_sha256": sha256_file(manifest_path),
        "status": result.get("status") or manifest.get("status"),
        "registered_at": runner_now().isoformat(),
    }
    existing = next((item for item in session.get("runs", []) if item.get("run_id") == record["run_id"]), None)
    if existing is not None:
        if existing.get("run_dir") != record["run_dir"]:
            raise SystemExit("Registered run_id points to a different run directory.")
        existing.update(record)
    else:
        session["runs"].append(record)
    return record


def _require_registered_run(root: Path, session: dict[str, Any], run_dir: str | Path, *, require_v2: bool = False) -> dict[str, Any]:
    resolved = resolve_under_root(root, run_dir, must_exist=True)
    matches = [item for item in session.get("runs", []) if resolve_under_root(root, item["run_dir"], must_exist=True) == resolved]
    if len(matches) != 1:
        raise SystemExit("Supervisor operation requires one previously registered run.")
    manifest_path = resolve_under_root(root, matches[0]["run_manifest_path"], must_exist=True)
    manifest = load_json(manifest_path, "registered run manifest")
    if manifest.get("run_id") != matches[0].get("run_id"):
        raise SystemExit("Registered run identity no longer matches its manifest.")
    if require_v2 and manifest.get("schema_version") != "responses_runner_v2.run_manifest.v2":
        raise SystemExit("Frozen v1 runs are terminal read-only evidence and cannot perform live supervisor transitions.")
    return matches[0]


@contextmanager
def _launch_intent_lock(session_path: Path, launch_intent_id: str) -> Iterator[None]:
    """Hold fail-fast process ownership for one supervisor launch intent."""

    launch_dir = session_path / "launches" / launch_intent_id
    launch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(launch_dir, 0o700)
    lock_path = launch_dir / ".launch.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit(
                f"Supervisor launch intent {launch_intent_id} is already owned by another process."
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _launch_intent(
    *,
    root: Path,
    cycle: dict[str, Any],
    subject: dict[str, Any],
    workflow_path: Path,
    run_name: str | None,
    stage_id: str | None,
    primary_job_inputs: Sequence[str],
    reference_context: Sequence[str],
    review_bundles: Sequence[str],
    input_bindings: Sequence[Any],
    skip_token_count: bool,
) -> tuple[str, dict[str, Any]]:
    workflow = load_workflow_definition(workflow_path, root=root)
    runtime = {
        "run_name": normalize_slug(run_name or workflow.workflow_id),
        "stage_id": stage_id,
        "primary_job_inputs": [str(item) for item in primary_job_inputs],
        "reference_context": [str(item) for item in reference_context],
        "review_bundles": [str(item) for item in review_bundles],
        "input_bindings": [
            {
                "binding_id": binding.binding_id,
                "path": binding.path,
                "authority": binding.authority,
                "stage_ids": list(binding.stage_ids),
            }
            for binding in input_bindings
        ],
    }
    if skip_token_count:
        runtime["skip_token_count"] = True
    acceptance_binding = cycle.get("acceptance_binding") or {}
    intent = {
        "accepted_subject_id": subject["subject_id"],
        "review_cycle_id": cycle["review_cycle_id"],
        "acceptance_binding_sha256": acceptance_binding.get("sha256"),
        "scaffold_version_id": subject["scaffold_version_id"],
        "scaffold_sha256": subject["scaffold_sha256"],
        "workflow_path": relpath(root, workflow_path),
        "workflow_sha256": sha256_file(workflow_path),
        "runtime": runtime,
        "runtime_sha256": _canonical_sha256(runtime),
    }
    intent_sha256 = _canonical_sha256(intent)
    return f"launch_{intent_sha256}", intent


def _result_from_reserved_run(
    *,
    root: Path,
    reservation: dict[str, Any],
) -> dict[str, Any] | None:
    run_dir = resolve_under_root(root, reservation["run_dir"], must_exist=False)
    manifest_path = runner_artifacts.run_manifest_path(run_dir)
    if not manifest_path.exists():
        return None
    manifest = runner_artifacts.load_run_manifest(root, run_dir)
    if manifest.get("schema_version") != "responses_runner_v2.run_manifest.v2":
        raise SystemExit("A supervisor launch reservation cannot recover a non-v2 run.")
    if manifest.get("run_dir") != relpath(root, run_dir):
        raise SystemExit("Reserved launch run directory does not match its run manifest.")
    if manifest.get("workflow_manifest_sha256") != reservation.get("workflow_sha256"):
        raise SystemExit("Reserved launch workflow hash does not match its run manifest.")
    contract_path = resolve_under_root(root, manifest["run_contract_path"], must_exist=True)
    if contract_path != run_dir / "run_contract.json":
        raise SystemExit("Reserved launch does not use the canonical run contract.")
    if sha256_file(contract_path) != manifest.get("run_contract_sha256"):
        raise SystemExit("Reserved launch run-contract hash does not match its manifest.")
    contract = load_and_verify_run_contract(root=root, run_dir=run_dir)
    if (
        contract.get("workflow_id") != manifest.get("workflow_id")
        or contract.get("workflow_asset_set_hash") != manifest.get("workflow_asset_set_hash")
    ):
        raise SystemExit("Reserved launch run contract does not match its run manifest.")

    stages = manifest.get("stages")
    pristine_stages = isinstance(stages, list) and bool(stages) and all(
        isinstance(stage, dict)
        and stage.get("status") == "prepared"
        and not stage.get("current_attempt_id")
        and not stage.get("attempts")
        and not runner_artifacts.list_stage_attempts(
            run_dir,
            int(stage["stage_number"]),
            str(stage["stage_id"]),
        )
        for stage in stages
    )
    submission_evidence_exists = any(
        next(run_dir.rglob(filename), None) is not None
        for filename in (
            "submission.intent.json",
            "response.latest.json",
            "response.final.json",
        )
    )
    if (
        manifest.get("status") == "created"
        and not manifest.get("current_stage_id")
        and pristine_stages
        and not submission_evidence_exists
    ):
        # The engine durably freezes the run before allocating attempt_001.
        # With no attempt or submission evidence, re-entering that same run
        # directory is the only crash-recovery case where a fresh POST is safe.
        return None
    return {
        "run_dir": relpath(root, run_dir),
        "run_manifest_path": relpath(root, manifest_path),
        "status": manifest["status"],
        "stage_id": manifest.get("current_stage_id") or reservation["runtime"].get("stage_id"),
    }


def _register_reserved_launch(
    *,
    root: Path,
    session_ref: str | Path,
    launch_intent_id: str,
    result: dict[str, Any],
) -> None:
    """Merge engine evidence into the latest session revision without lost updates."""

    for _attempt in range(8):
        session, session_path = _load_session_and_path(root, session_ref)
        reservation = next(
            (
                item
                for item in session.get("launch_reservations", [])
                if item.get("launch_intent_id") == launch_intent_id
            ),
            None,
        )
        if reservation is None:
            raise SystemExit("Supervisor launch reservation disappeared before registration.")
        reserved_run_dir = resolve_under_root(root, reservation["run_dir"], must_exist=True)
        result_run_dir = resolve_under_root(root, result["run_dir"], must_exist=True)
        result_manifest_path = resolve_under_root(
            root, result["run_manifest_path"], must_exist=True
        )
        if result_run_dir != reserved_run_dir:
            raise SystemExit("Engine result does not match the reserved supervisor run directory.")
        if result_manifest_path != runner_artifacts.run_manifest_path(reserved_run_dir):
            raise SystemExit("Engine result does not use the reserved run's canonical manifest.")
        record = _register_run_result(root, session, result)
        reservation["status"] = "registered"
        reservation["result"] = dict(result)
        reservation["registered_run_id"] = record["run_id"]
        reservation["registered_at"] = reservation.get("registered_at") or runner_now().isoformat()
        reservation["updated_at"] = runner_now().isoformat()
        session["status"] = (
            "stage_running"
            if result.get("status") in {"in_progress", "running"}
            else str(result.get("status") or "stage_running")
        )
        session["current_phase"] = "stage_execution"
        try:
            _write_session(root, session_path, session)
            return
        except SystemExit as exc:
            if "revision conflict" not in str(exc):
                raise
    raise SystemExit("Supervisor launch registration could not win a session revision CAS.")


def launch_scaffold(
    *,
    root: Path,
    session_ref: str | Path,
    workflow_file: str | Path,
    run_name: str | None = None,
    primary_job_inputs: Sequence[str] = (),
    reference_context: Sequence[str] = (),
    review_bundles: Sequence[str] = (),
    input_binding_file: str | Path | None = None,
    stage_id: str | None = None,
    skip_token_count: bool = False,
    wait: bool = False,
    client: OpenAIClient | None = None,
) -> dict[str, Any]:
    """Atomically reserve and launch one run for an accepted scaffold intent."""

    session, session_path = _load_session_and_path(root, session_ref)
    if session.get("schema_version") != SUPERVISOR_SESSION_SCHEMA_VERSION:
        raise SystemExit(
            "Frozen v1 supervisor sessions remain readable evidence but cannot perform live launches."
        )
    cycle, subject = _accepted_scaffold_cycle(root, session)
    latest_source = resolve_under_root(root, session["scaffold_versions"][-1]["path"], must_exist=True)
    workflow_path = resolve_under_root(root, workflow_file, must_exist=True)
    if not workflow_path.is_relative_to(latest_source):
        raise SystemExit("Supervisor launch workflow must come from the accepted staged scaffold version.")
    input_bindings = _supervisor_input_bindings(
        root=root,
        workflow_file=workflow_path,
        input_binding_file=input_binding_file,
    )
    launch_intent_id, intent = _launch_intent(
        root=root,
        cycle=cycle,
        subject=subject,
        workflow_path=workflow_path,
        run_name=run_name,
        stage_id=stage_id,
        primary_job_inputs=primary_job_inputs,
        reference_context=reference_context,
        review_bundles=review_bundles,
        input_bindings=input_bindings,
        skip_token_count=skip_token_count,
    )

    with _launch_intent_lock(session_path, launch_intent_id):
        session, session_path = _load_session_and_path(root, session_ref)
        cycle, subject = _accepted_scaffold_cycle(root, session)
        current_intent_id, current_intent = _launch_intent(
            root=root,
            cycle=cycle,
            subject=subject,
            workflow_path=workflow_path,
            run_name=run_name,
            stage_id=stage_id,
            primary_job_inputs=primary_job_inputs,
            reference_context=reference_context,
            review_bundles=review_bundles,
            input_bindings=input_bindings,
            skip_token_count=skip_token_count,
        )
        if current_intent_id != launch_intent_id or current_intent != intent:
            raise SystemExit("Supervisor launch inputs changed before reservation; retry the launch.")

        reservations = session.setdefault("launch_reservations", [])
        reservation = next(
            (item for item in reservations if item.get("launch_intent_id") == launch_intent_id),
            None,
        )
        owner_token = uuid.uuid4().hex
        now = runner_now().isoformat()
        if reservation is None:
            reservation = {
                "launch_intent_id": launch_intent_id,
                "intent_sha256": launch_intent_id.removeprefix("launch_"),
                **intent,
                "run_dir": relpath(root, session_path / "launches" / launch_intent_id / "run"),
                "status": "reserved",
                "owner_token": owner_token,
                "recovery_count": 0,
                "created_at": now,
                "updated_at": now,
            }
            reservations.append(reservation)
            session = _write_session(root, session_path, session)
            reservation = next(
                item
                for item in session["launch_reservations"]
                if item["launch_intent_id"] == launch_intent_id
            )
        else:
            if reservation.get("intent_sha256") != launch_intent_id.removeprefix("launch_"):
                raise SystemExit("Supervisor launch intent identity collision detected.")
            recovered = _result_from_reserved_run(root=root, reservation=reservation)
            if recovered is not None:
                registered_run = next(
                    (
                        item
                        for item in session.get("runs", [])
                        if item.get("run_id") == reservation.get("registered_run_id")
                        and item.get("run_dir") == reservation.get("run_dir")
                    ),
                    None,
                )
                if reservation.get("status") == "registered" and registered_run is not None:
                    return recovered
                _register_reserved_launch(
                    root=root,
                    session_ref=session_ref,
                    launch_intent_id=launch_intent_id,
                    result=recovered,
                )
                return recovered
            if reservation.get("status") == "registered":
                raise SystemExit("Registered supervisor launch has no recoverable run manifest.")
            reservation["owner_token"] = owner_token
            reservation["recovery_count"] = int(reservation.get("recovery_count", 0)) + 1
            reservation["updated_at"] = now
            session = _write_session(root, session_path, session)
            reservation = next(
                item
                for item in session["launch_reservations"]
                if item["launch_intent_id"] == launch_intent_id
            )

        result = run_workflow(
            workflow_file=workflow_path,
            runtime=RuntimeOptions(
                run_name=run_name,
                run_dir=Path(reservation["run_dir"]),
                stage_id=stage_id,
                primary_job_inputs=list(primary_job_inputs),
                reference_context=list(reference_context),
                review_bundles=list(review_bundles),
                input_bindings=list(input_bindings),
                skip_token_count=skip_token_count,
                wait=wait,
            ),
            client=client or OpenAIClient.from_env(root=root),
            root=root,
        )
        _register_reserved_launch(
            root=root,
            session_ref=session_ref,
            launch_intent_id=launch_intent_id,
            result=result,
        )
        return result


def _rerun_intent(
    *,
    root: Path,
    archive_path: Path,
    archive: dict[str, Any],
    registered: dict[str, Any],
    workflow_path: Path,
    primary_job_inputs: Sequence[str],
    reference_context: Sequence[str],
    review_bundles: Sequence[str],
    input_bindings: Sequence[Any],
) -> tuple[str, dict[str, Any]]:
    source = archive["source"]
    runtime = {
        "run_dir": registered["run_dir"],
        "stage_id": source["stage_id"],
        "primary_job_inputs": [str(item) for item in primary_job_inputs],
        "reference_context": [str(item) for item in reference_context],
        "review_bundles": [str(item) for item in review_bundles],
        "input_bindings": [
            {
                "binding_id": binding.binding_id,
                "path": binding.path,
                "authority": binding.authority,
                "stage_ids": list(binding.stage_ids),
            }
            for binding in input_bindings
        ],
        "rerun_archive_manifest": relpath(root, archive_path),
    }
    intent = {
        "archive_manifest_path": relpath(root, archive_path),
        "archive_sha256": sha256_file(archive_path),
        "archive_id": archive["archive_id"],
        "run_dir": registered["run_dir"],
        "run_id": registered["run_id"],
        "stage_id": source["stage_id"],
        "workflow_path": relpath(root, workflow_path),
        "workflow_sha256": sha256_file(workflow_path),
        "runtime": runtime,
        "runtime_sha256": _canonical_sha256(runtime),
    }
    intent_sha256 = _canonical_sha256(intent)
    return f"rerun_{intent_sha256}", intent


def _validate_rerun_preconditions(
    *,
    root: Path,
    session: dict[str, Any],
    archive: dict[str, Any],
) -> None:
    source = archive["source"]
    current_request = supervisor_artifacts.compute_request_evidence(
        root,
        source["run_dir"],
        source["stage_id"],
    )
    if current_request.get("request_hash") != archive.get("request_hash"):
        raise SystemExit("Rerun request evidence changed after archive.")
    current_scaffold = supervisor_artifacts.latest_scaffold_evidence(root, session)
    if current_scaffold.get("scaffold_hash") != archive.get("scaffold_hash"):
        raise SystemExit("Rerun scaffold changed after archive.")


def _result_from_reserved_rerun(
    *,
    root: Path,
    reservation: dict[str, Any],
) -> dict[str, Any] | None:
    """Recover only the single attempt created after this archive reservation."""

    archive_path = resolve_under_root(
        root,
        reservation["archive_manifest_path"],
        must_exist=True,
    )
    if sha256_file(archive_path) != reservation.get("archive_sha256"):
        raise SystemExit("Reserved rerun archive changed after reservation.")
    run_dir = resolve_under_root(root, reservation["run_dir"], must_exist=True)
    manifest_path = runner_artifacts.run_manifest_path(run_dir)
    manifest = runner_artifacts.load_run_manifest(root, run_dir)
    if manifest.get("schema_version") != "responses_runner_v2.run_manifest.v2":
        raise SystemExit("A supervisor rerun reservation cannot recover a non-v2 run.")
    if (
        manifest.get("run_id") != reservation.get("run_id")
        or manifest.get("run_dir") != reservation.get("run_dir")
        or manifest.get("workflow_manifest_sha256") != reservation.get("workflow_sha256")
    ):
        raise SystemExit("Reserved rerun identity does not match its run manifest.")
    summary = runner_artifacts.find_stage_summary(manifest, reservation["stage_id"])
    manifest_attempts = {
        str(item["attempt_id"]): item
        for item in summary.get("attempts", [])
        if isinstance(item, dict) and isinstance(item.get("attempt_id"), str)
    }
    filesystem_attempt_ids = {
        f"attempt_{number:03d}"
        for number in runner_artifacts.list_stage_attempts(
            run_dir,
            int(summary["stage_number"]),
            str(summary["stage_id"]),
        )
    }
    baseline = set(reservation["baseline_attempt_ids"])
    if not baseline.issubset(set(manifest_attempts) | filesystem_attempt_ids):
        raise SystemExit("Reserved rerun baseline attempt evidence disappeared.")
    new_attempt_ids = sorted((set(manifest_attempts) | filesystem_attempt_ids) - baseline)
    if not new_attempt_ids:
        return None
    if len(new_attempt_ids) != 1:
        raise SystemExit("Reserved rerun produced more than one new attempt; refusing recovery.")
    attempt_id = new_attempt_ids[0]
    attempt = manifest_attempts.get(attempt_id)
    if attempt is None:
        attempt_dir = (
            runner_artifacts.stage_root_path(
                run_dir,
                int(summary["stage_number"]),
                str(summary["stage_id"]),
            )
            / attempt_id
        )
        if attempt_dir.is_symlink() or any(attempt_dir.iterdir()):
            raise SystemExit(
                "Reserved rerun has unregistered attempt evidence; "
                "manual recovery is required and resubmission is blocked."
            )
        # Allocation is an exclusive empty-directory creation and precedes all
        # request/submission evidence. Preserve that orphan as baseline, then
        # let the engine allocate a fresh attempt without deleting evidence.
        reservation["baseline_attempt_ids"] = sorted(baseline | {attempt_id})
        return None
    authorization = attempt.get("rerun_authorization")
    if (
        not isinstance(authorization, dict)
        or authorization.get("archive_manifest_path")
        != reservation["archive_manifest_path"]
        or authorization.get("archive_sha256") != reservation["archive_sha256"]
    ):
        raise SystemExit("New rerun attempt is not bound to the reserved archive.")
    return {
        "run_dir": reservation["run_dir"],
        "run_manifest_path": relpath(root, manifest_path),
        "status": manifest["status"],
        "stage_id": reservation["stage_id"],
    }


def _register_reserved_rerun(
    *,
    root: Path,
    session_ref: str | Path,
    rerun_intent_id: str,
    result: dict[str, Any],
) -> None:
    """Merge rerun engine evidence into the latest session revision."""

    for _attempt in range(8):
        session, session_path = _load_session_and_path(root, session_ref)
        reservation = next(
            (
                item
                for item in session.get("rerun_reservations", [])
                if item.get("rerun_intent_id") == rerun_intent_id
            ),
            None,
        )
        if reservation is None:
            raise SystemExit("Supervisor rerun reservation disappeared before registration.")
        reserved_run_dir = resolve_under_root(root, reservation["run_dir"], must_exist=True)
        result_run_dir = resolve_under_root(root, result["run_dir"], must_exist=True)
        result_manifest_path = resolve_under_root(
            root,
            result["run_manifest_path"],
            must_exist=True,
        )
        if result_run_dir != reserved_run_dir:
            raise SystemExit("Engine result does not match the reserved rerun directory.")
        if result_manifest_path != runner_artifacts.run_manifest_path(reserved_run_dir):
            raise SystemExit("Engine rerun result does not use the canonical run manifest.")
        record = _register_run_result(root, session, result)
        if record["run_id"] != reservation["run_id"]:
            raise SystemExit("Engine rerun result does not match the reserved run identity.")
        archive_record = next(
            (
                item
                for item in session.get("archives", [])
                if item.get("archive_manifest_path")
                == reservation["archive_manifest_path"]
            ),
            None,
        )
        if archive_record is None:
            raise SystemExit("Supervisor rerun archive record disappeared before registration.")
        now = runner_now().isoformat()
        reservation["status"] = "registered"
        reservation["result"] = dict(result)
        reservation["registered_run_id"] = record["run_id"]
        reservation["registered_at"] = reservation.get("registered_at") or now
        reservation["updated_at"] = now
        archive_record["rerun_started_at"] = archive_record.get("rerun_started_at") or now
        archive_record["rerun_stage_id"] = reservation["stage_id"]
        session["current_phase"] = "stage_execution"
        try:
            _write_session(root, session_path, session)
            return
        except SystemExit as exc:
            if "revision conflict" not in str(exc):
                raise
    raise SystemExit("Supervisor rerun registration could not win a session revision CAS.")


def rerun_archived_stage(
    *,
    root: Path,
    session_ref: str | Path,
    archive_manifest: str | Path,
    workflow_file: str | Path,
    primary_job_inputs: Sequence[str] = (),
    reference_context: Sequence[str] = (),
    review_bundles: Sequence[str] = (),
    input_binding_file: str | Path | None = None,
    wait: bool = False,
    client: OpenAIClient | None = None,
) -> dict[str, Any]:
    """Rerun one archived failed-no-artifact stage with unchanged evidence."""

    session, session_path = _load_session_and_path(root, session_ref)
    if session.get("schema_version") != SUPERVISOR_SESSION_SCHEMA_VERSION:
        raise SystemExit(
            "Frozen v1 supervisor sessions remain readable evidence but cannot perform live reruns."
        )
    archive_path = resolve_under_root(root, archive_manifest, must_exist=True)
    recorded = next((item for item in session.get("archives", []) if item.get("archive_manifest_path") == relpath(root, archive_path)), None)
    if recorded is None:
        raise SystemExit("Rerun requires a supervisor-recorded archive manifest.")
    archive = load_json(archive_path, "supervisor archive")
    if not archive.get("rerun_as_is_eligible"):
        raise SystemExit("Archive is not eligible for an as-is rerun.")
    source = archive["source"]
    registered = _require_registered_run(root, session, source["run_dir"], require_v2=True)
    workflow_path = resolve_under_root(root, workflow_file, must_exist=True)
    input_bindings = _supervisor_input_bindings(
        root=root,
        workflow_file=workflow_path,
        input_binding_file=input_binding_file,
    )
    rerun_intent_id, intent = _rerun_intent(
        root=root,
        archive_path=archive_path,
        archive=archive,
        registered=registered,
        workflow_path=workflow_path,
        primary_job_inputs=primary_job_inputs,
        reference_context=reference_context,
        review_bundles=review_bundles,
        input_bindings=input_bindings,
    )

    with _launch_intent_lock(session_path, rerun_intent_id):
        session, session_path = _load_session_and_path(root, session_ref)
        recorded = next(
            (
                item
                for item in session.get("archives", [])
                if item.get("archive_manifest_path") == relpath(root, archive_path)
            ),
            None,
        )
        if recorded is None:
            raise SystemExit("Rerun archive record changed before reservation.")
        archive = load_json(archive_path, "supervisor archive")
        if not archive.get("rerun_as_is_eligible"):
            raise SystemExit("Archive is not eligible for an as-is rerun.")
        source = archive["source"]
        registered = _require_registered_run(
            root,
            session,
            source["run_dir"],
            require_v2=True,
        )
        current_intent_id, current_intent = _rerun_intent(
            root=root,
            archive_path=archive_path,
            archive=archive,
            registered=registered,
            workflow_path=workflow_path,
            primary_job_inputs=primary_job_inputs,
            reference_context=reference_context,
            review_bundles=review_bundles,
            input_bindings=input_bindings,
        )
        if current_intent_id != rerun_intent_id or current_intent != intent:
            raise SystemExit("Supervisor rerun inputs changed before reservation; retry the rerun.")

        reservations = session.setdefault("rerun_reservations", [])
        same_archive = [
            item
            for item in reservations
            if item.get("archive_sha256") == intent["archive_sha256"]
        ]
        if len(same_archive) > 1:
            raise SystemExit("Supervisor session contains duplicate reservations for one archive.")
        if any(item.get("rerun_intent_id") != rerun_intent_id for item in same_archive):
            raise SystemExit("This archive is already reserved with different rerun inputs.")
        reservation = same_archive[0] if same_archive else None
        owner_token = uuid.uuid4().hex
        now = runner_now().isoformat()
        if reservation is None:
            _validate_rerun_preconditions(root=root, session=session, archive=archive)
            manifest = runner_artifacts.load_run_manifest(
                root,
                resolve_under_root(root, registered["run_dir"], must_exist=True),
            )
            summary = runner_artifacts.find_stage_summary(manifest, source["stage_id"])
            if summary.get("status") != "failed_no_artifact":
                raise SystemExit(
                    "Archive rerun reservation requires the current failed_no_artifact stage."
                )
            baseline_attempt_ids = sorted(
                {
                    str(item["attempt_id"])
                    for item in summary.get("attempts", [])
                    if isinstance(item, dict) and isinstance(item.get("attempt_id"), str)
                }
                | {
                    f"attempt_{number:03d}"
                    for number in runner_artifacts.list_stage_attempts(
                        resolve_under_root(root, registered["run_dir"], must_exist=True),
                        int(summary["stage_number"]),
                        str(summary["stage_id"]),
                    )
                }
            )
            reservation = {
                "rerun_intent_id": rerun_intent_id,
                "intent_sha256": rerun_intent_id.removeprefix("rerun_"),
                **intent,
                "baseline_attempt_ids": baseline_attempt_ids,
                "baseline_current_attempt_id": summary.get("current_attempt_id"),
                "status": "reserved",
                "owner_token": owner_token,
                "recovery_count": 0,
                "created_at": now,
                "updated_at": now,
            }
            reservations.append(reservation)
            session = _write_session(root, session_path, session)
        else:
            recovered = _result_from_reserved_rerun(
                root=root,
                reservation=reservation,
            )
            if recovered is not None:
                registered_run = next(
                    (
                        item
                        for item in session.get("runs", [])
                        if item.get("run_id") == reservation.get("registered_run_id")
                        and item.get("run_dir") == reservation.get("run_dir")
                    ),
                    None,
                )
                if reservation.get("status") == "registered" and registered_run is not None:
                    return recovered
                _register_reserved_rerun(
                    root=root,
                    session_ref=session_ref,
                    rerun_intent_id=rerun_intent_id,
                    result=recovered,
                )
                return recovered
            if reservation.get("status") == "registered":
                raise SystemExit("Registered supervisor rerun has no recoverable attempt evidence.")
            _validate_rerun_preconditions(root=root, session=session, archive=archive)
            reservation["owner_token"] = owner_token
            reservation["recovery_count"] = int(reservation.get("recovery_count", 0)) + 1
            reservation["updated_at"] = now
            session = _write_session(root, session_path, session)

        result = run_workflow(
            workflow_file=workflow_path,
            runtime=RuntimeOptions(
                run_dir=Path(registered["run_dir"]),
                stage_id=source["stage_id"],
                primary_job_inputs=list(primary_job_inputs),
                reference_context=list(reference_context),
                review_bundles=list(review_bundles),
                input_bindings=list(input_bindings),
                wait=wait,
                rerun_archive_manifest=relpath(root, archive_path),
            ),
            client=client or OpenAIClient.from_env(root=root),
            root=root,
        )
        _register_reserved_rerun(
            root=root,
            session_ref=session_ref,
            rerun_intent_id=rerun_intent_id,
            result=result,
        )
        return result


def classify_stage(*, root: Path, session_ref: str | Path, run_dir: str | Path, stage_id: str, output: str | Path | None = None) -> dict[str, Any]:
    """Classify a stage outcome and record reviewability or recovery requirements."""

    session, session_path = _load_session_and_path(root, session_ref)
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    _require_registered_run(root, session, resolved_run_dir)
    run_manifest = runner_artifacts.load_run_manifest(root, resolved_run_dir)
    summary = runner_artifacts.find_stage_summary(run_manifest, stage_id)
    checkpoint_value = summary.get("checkpoint_path")
    if not isinstance(checkpoint_value, str) or not checkpoint_value:
        stage_dirs = sorted((resolved_run_dir / "stages").glob(f"*_{stage_id}"))
        if not stage_dirs:
            raise SystemExit(f"Could not find stage directory for {stage_id}")
        checkpoint_value = relpath(root, stage_dirs[0] / "stage_checkpoint.json")
    output_path = output or (session_path / "review_cycles" / f"{stage_id}_classification" / "stage_outcome.json")
    human_pause_path = Path(output_path).with_suffix(".human_pause.json")
    outcome = supervisor_policies.classify_stage_outcome(root=root, checkpoint_path=checkpoint_value, human_pause_output=human_pause_path)
    outcome_rel = supervisor_policies.write_stage_outcome(root, output_path, outcome)
    session["stage_outcomes"].append(
        {
            "run_id": outcome.get("run_id"),
            "stage_id": outcome.get("stage_id"),
            "classification": outcome.get("classification"),
            "artifact_path": outcome_rel,
            "reviewability": bool(outcome.get("reviewable")),
        }
    )
    if outcome.get("human_pause"):
        session["human_pauses"].append(outcome["human_pause"])
    _write_session(root, session_path, session)
    return outcome


def monitor_stage(*, root: Path, session_ref: str | Path, run_dir: str | Path, stage_id: str, stale_after_seconds: float) -> dict[str, Any]:
    """Record monitoring state and create a human-pause anomaly when a stage is stale."""

    session, session_path = _load_session_and_path(root, session_ref)
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    _require_registered_run(root, session, resolved_run_dir, require_v2=True)
    run_manifest = runner_artifacts.load_run_manifest(root, resolved_run_dir)
    summary = runner_artifacts.find_stage_summary(run_manifest, stage_id)
    checkpoint_value = summary.get("checkpoint_path")
    if not isinstance(checkpoint_value, str) or not checkpoint_value:
        stage_dirs = sorted((resolved_run_dir / "stages").glob(f"*_{stage_id}"))
        if not stage_dirs:
            raise SystemExit(f"Could not find stage directory for {stage_id}")
        checkpoint_value = relpath(root, stage_dirs[0] / "stage_checkpoint.json")
    output_dir = session_path / "monitoring"
    output_dir.mkdir(parents=True, exist_ok=True)
    anomaly = supervisor_policies.detect_monitoring_anomaly(
        root=root,
        checkpoint_path=checkpoint_value,
        stale_after_seconds=stale_after_seconds,
        human_pause_output=output_dir / f"{stage_id}.monitoring.human_pause.json",
    )
    event = {
        "timestamp": runner_now().isoformat(),
        "run_id": run_manifest["run_id"],
        "stage_id": stage_id,
        "response_id": summary.get("response_id"),
        "status": summary.get("response_status") or summary.get("status"),
        "action": "monitor_without_duplicate_submit" if anomaly else "no_anomaly",
        "artifact_path": checkpoint_value,
    }
    session["monitoring_events"].append(event)
    if anomaly:
        outcome_path = output_dir / f"{stage_id}.monitoring_anomaly.json"
        anomaly_rel = supervisor_policies.write_stage_outcome(root, outcome_path, anomaly)
        session["stage_outcomes"].append(
            {
                "run_id": anomaly.get("run_id"),
                "stage_id": anomaly.get("stage_id"),
                "classification": anomaly.get("classification"),
                "artifact_path": anomaly_rel,
                "reviewability": False,
            }
        )
        if anomaly.get("human_pause"):
            session["human_pauses"].append(anomaly["human_pause"])
    _write_session(root, session_path, session)
    return anomaly or event


def archive_attempt(*, root: Path, session_ref: str | Path, run_dir: str | Path, stage_id: str, reason: str) -> dict[str, Any]:
    """Archive a failed no-artifact attempt before permitting a controlled rerun."""

    session, session_path = _load_session_and_path(root, session_ref)
    _require_registered_run(root, session, run_dir, require_v2=True)
    before = dict(session.get("retry_budget", {}))
    budget = int(before.get("failed_no_artifact", 0))
    if budget <= 0:
        raise SystemExit("No failed_no_artifact retry budget remains; archive/rerun is blocked.")
    after = dict(before)
    after["failed_no_artifact"] = budget - 1
    manifest = supervisor_artifacts.archive_attempt(
        root=root,
        session_path=session_path,
        session=session,
        run_dir=run_dir,
        stage_id=stage_id,
        reason=reason,
        retry_budget_before=before,
        retry_budget_after=after,
    )
    session["retry_budget"] = after
    session["archives"].append({"archive_manifest_path": manifest["archive_manifest_path"]})
    _write_session(root, session_path, session)
    return manifest


def create_approved_review_bundle(
    *,
    root: Path,
    session_ref: str | Path,
    output_path: str | Path | None,
    workflow_id: str,
    source_stage_id: str,
    source_run_id: str,
    primary_artifact_markdown: str | Path,
    response_artifact_json: str | Path,
    reviewer_notes: str | Path,
    acceptance_record: str | Path,
    approved_handoff_markdown: str | Path | None = None,
    structured_artifact_json: str | Path | None = None,
) -> dict[str, Any]:
    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _session_mutation_lock(session_path):
        session, session_path = _load_session_and_path(root, session_ref)
        acceptance_path = resolve_under_root(root, acceptance_record, must_exist=True)
        matches = [cycle for cycle in session.get("review_cycles", []) if cycle.get("acceptance_record") and resolve_under_root(root, cycle["acceptance_record"], must_exist=True) == acceptance_path]
        if len(matches) != 1:
            raise SystemExit("Acceptance record must be the supervisor-recorded acceptance for exactly one review cycle.")
        review_cycle_id = matches[0]["review_cycle_id"]
        with _cycle_invocation_lock(session_path, review_cycle_id, "approved_review_bundle"):
            return _create_approved_review_bundle_locked(
                root=root,
                session_ref=session_ref,
                output_path=output_path,
                workflow_id=workflow_id,
                source_stage_id=source_stage_id,
                source_run_id=source_run_id,
                primary_artifact_markdown=primary_artifact_markdown,
                response_artifact_json=response_artifact_json,
                reviewer_notes=reviewer_notes,
                acceptance_record=acceptance_record,
                approved_handoff_markdown=approved_handoff_markdown,
                structured_artifact_json=structured_artifact_json,
            )


def _create_approved_review_bundle_locked(
    *,
    root: Path,
    session_ref: str | Path,
    output_path: str | Path | None,
    workflow_id: str,
    source_stage_id: str,
    source_run_id: str,
    primary_artifact_markdown: str | Path,
    response_artifact_json: str | Path,
    reviewer_notes: str | Path,
    acceptance_record: str | Path,
    approved_handoff_markdown: str | Path | None = None,
    structured_artifact_json: str | Path | None = None,
) -> dict[str, Any]:
    """Create an approved review bundle only after an approving acceptance record."""

    session, session_path = _load_session_and_path(root, session_ref)
    acceptance_path = resolve_under_root(root, acceptance_record, must_exist=True)
    matches = [cycle for cycle in session.get("review_cycles", []) if cycle.get("acceptance_record") and resolve_under_root(root, cycle["acceptance_record"], must_exist=True) == acceptance_path]
    if len(matches) != 1:
        raise SystemExit("Acceptance record must be the supervisor-recorded acceptance for exactly one review cycle.")
    cycle = matches[0]
    if any(item.get("review_cycle_id") == cycle["review_cycle_id"] for item in session.get("approved_review_bundles", [])):
        raise SystemExit("Approved review bundle is immutable once recorded for this review cycle.")
    if cycle.get("review_kind") != "stage_output":
        raise SystemExit("Approved review bundles may be created only from accepted stage_output review cycles.")
    subject = _load_cycle_subject(root, cycle)
    _verify_subject_artifacts(root, subject)
    if sha256_file(acceptance_path) != cycle.get("acceptance_record_sha256"):
        raise SystemExit("Operator acceptance record hash mismatch.")
    binding_ref = cycle.get("acceptance_binding")
    if not isinstance(binding_ref, dict):
        raise SystemExit("Approved review bundle requires a hash-bound operator acceptance.")
    acceptance_binding_path = resolve_under_root(root, binding_ref["path"], must_exist=True)
    if sha256_file(acceptance_binding_path) != binding_ref.get("sha256"):
        raise SystemExit("Operator acceptance binding hash mismatch.")
    acceptance_binding = load_json(acceptance_binding_path, "operator acceptance binding")
    if acceptance_binding.get("approval_decision") != "approve" or acceptance_binding.get("subject_id") != subject["subject_id"]:
        raise SystemExit("Operator acceptance does not approve this immutable subject.")
    acceptance = _load_decision(root, acceptance_path, "operator acceptance")
    if acceptance.get("review_kind") != "operator_acceptance" or acceptance.get("approval_decision") != "approve":
        raise SystemExit("Approved review bundle requires an approving operator acceptance record.")
    if subject.get("workflow_id") != workflow_id or subject.get("run_id") != source_run_id or subject.get("stage_id") != source_stage_id:
        raise SystemExit("Review bundle source identity does not match the accepted review subject.")
    registered = next((item for item in session.get("runs", []) if item.get("run_id") == source_run_id), None)
    if not isinstance(registered, dict):
        raise SystemExit("Approved review bundle source run is not registered to this supervisor session.")
    _require_registered_run(root, session, registered["run_dir"])
    outcomes = [item for item in session.get("stage_outcomes", []) if item.get("run_id") == source_run_id and item.get("stage_id") == source_stage_id]
    if len(outcomes) != 1:
        raise SystemExit("Approved review bundle requires one supervisor-classified stage outcome.")
    outcome_path = resolve_under_root(root, outcomes[0]["artifact_path"], must_exist=True)
    outcome = load_json(outcome_path, "stage outcome")
    if not outcome.get("review_bundle_allowed") or not outcome.get("reviewable"):
        raise SystemExit("Stage outcome does not permit approved review-bundle progression.")
    expected_output = cycle["derived_paths"]["review_bundle"]
    output_path = _require_derived_path(root, output_path, expected_output, "approved review bundle output")
    manifest = load_json(resolve_under_root(root, subject["reviewed_artifact_manifest_path"], must_exist=True), "reviewed artifact manifest")
    reviewed_by_path = {item["path"]: item for item in manifest.get("artifacts", [])}
    outcome_record = reviewed_by_path.get(relpath(root, outcome_path))
    if outcome_record is None or outcome_record.get("sha256") != sha256_file(outcome_path):
        raise SystemExit("Stage outcome was not hash-bound in the accepted review subject.")
    for label, raw in (
        ("primary artifact", primary_artifact_markdown),
        ("response artifact", response_artifact_json),
        ("approved handoff", approved_handoff_markdown),
        ("structured artifact", structured_artifact_json),
    ):
        if raw is None:
            continue
        resolved = resolve_under_root(root, raw, must_exist=True)
        record = reviewed_by_path.get(relpath(root, resolved))
        if record is None or record.get("sha256") != sha256_file(resolved):
            raise SystemExit(f"{label} is not hash-bound in the reviewed artifact manifest.")
    expected_notes = resolve_under_root(root, cycle["derived_paths"]["consolidation_md"], must_exist=True)
    if resolve_under_root(root, reviewer_notes, must_exist=True) != expected_notes:
        raise SystemExit("Reviewer notes must be the supervisor-derived consolidated review markdown.")
    locked_decisions = ["Operator accepted only supported recommendations before bundle creation."]
    notes = [
        f"operator_acceptance_record={relpath(root, acceptance_path)}",
        f"operator_acceptance_sha256={sha256_file(acceptance_path)}",
        f"review_subject_id={subject['subject_id']}",
    ]
    if output_path.exists():
        payload = load_json(output_path, "existing approved review bundle")
        supervisor_artifacts.validate_against_schema(payload, "review_bundle.schema.json", "existing approved review bundle")
        payload["bundle_path"] = relpath(root, output_path)
    else:
        payload = create_review_bundle(
            root=root,
            output_path=output_path,
            workflow_id=workflow_id,
            source_stage_id=source_stage_id,
            source_run_id=source_run_id,
            primary_artifact_markdown=primary_artifact_markdown,
            response_artifact_json=response_artifact_json,
            reviewer_notes=reviewer_notes,
            approved_handoff_markdown=approved_handoff_markdown,
            structured_artifact_json=structured_artifact_json,
            locked_decisions=locked_decisions,
            open_dependencies=[],
            notes=notes,
        )
    expected_bundle = {
        "workflow_id": workflow_id,
        "source_stage_id": source_stage_id,
        "source_run_id": source_run_id,
        "primary_artifact_markdown": relpath(root, resolve_under_root(root, primary_artifact_markdown, must_exist=True)),
        "response_artifact_json": relpath(root, resolve_under_root(root, response_artifact_json, must_exist=True)),
        "reviewer_notes": relpath(root, expected_notes),
        "locked_decisions": locked_decisions,
        "open_dependencies": [],
        "notes": notes,
    }
    if approved_handoff_markdown is not None:
        expected_bundle["approved_handoff_markdown"] = relpath(root, resolve_under_root(root, approved_handoff_markdown, must_exist=True))
    if structured_artifact_json is not None:
        expected_bundle["structured_artifact_json"] = relpath(root, resolve_under_root(root, structured_artifact_json, must_exist=True))
    mismatches = [key for key, value in expected_bundle.items() if payload.get(key) != value]
    expected_hashes = {
        "primary_artifact_markdown_sha256": sha256_file(resolve_under_root(root, primary_artifact_markdown, must_exist=True)),
        "response_artifact_json_sha256": sha256_file(resolve_under_root(root, response_artifact_json, must_exist=True)),
        "reviewer_notes_sha256": sha256_file(expected_notes),
    }
    if approved_handoff_markdown is not None:
        expected_hashes["approved_handoff_markdown_sha256"] = sha256_file(resolve_under_root(root, approved_handoff_markdown, must_exist=True))
    if structured_artifact_json is not None:
        expected_hashes["structured_artifact_json_sha256"] = sha256_file(resolve_under_root(root, structured_artifact_json, must_exist=True))
    if payload.get("artifact_hashes") != expected_hashes:
        mismatches.append("artifact_hashes")
    if mismatches:
        raise SystemExit(f"Existing approved review bundle does not match this immutable transition: {', '.join(sorted(set(mismatches)))}")
    bundle_path = resolve_under_root(root, payload["bundle_path"], must_exist=True)
    bundle_binding = {
        "schema_version": REVIEW_BUNDLE_BINDING_SCHEMA_VERSION,
        "created_at": acceptance["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "review_cycle_id": cycle["review_cycle_id"],
        "subject_id": subject["subject_id"],
        "subject_sha256": cycle["subject_sha256"],
        "acceptance_binding_path": relpath(root, acceptance_binding_path),
        "acceptance_binding_sha256": sha256_file(acceptance_binding_path),
        "bundle_path": relpath(root, bundle_path),
        "bundle_sha256": sha256_file(bundle_path),
        "artifact_hashes": payload["artifact_hashes"],
    }
    bundle_binding_path = cycle["derived_paths"]["review_bundle_binding"]
    _write_once_json(root, bundle_binding_path, bundle_binding, "review_bundle_binding.schema.json", "review bundle binding")
    session["approved_review_bundles"].append(
        {
            "bundle_path": payload["bundle_path"],
            "source_run_id": source_run_id,
            "source_stage_id": source_stage_id,
            "artifact_hashes": payload["artifact_hashes"],
            "validation_status": "created",
            "review_cycle_id": cycle["review_cycle_id"],
            "subject_id": subject["subject_id"],
            "binding_path": bundle_binding_path,
            "binding_sha256": sha256_file(resolve_under_root(root, bundle_binding_path, must_exist=True)),
        }
    )
    _write_session(root, session_path, session)
    return payload


def _require_final_bundle_payload(payload: dict[str, Any]) -> None:
    required = [
        "packet_version",
        "summary",
        "file_inventory",
        "emitted_files",
        "validation_evidence",
        "agent_reviews",
        "consolidation",
        "operator_acceptance",
        "model_migration_summary",
        "failure_policy_summary",
        "human_pause_summary",
        "rollout_instructions",
        "residual_risks",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        raise SystemExit(f"Final implementation bundle missing required keys: {', '.join(missing)}")
    inventory_paths = [item.get("path") for item in payload.get("file_inventory", []) if isinstance(item, dict)]
    emitted_paths = [item.get("path") for item in payload.get("emitted_files", []) if isinstance(item, dict)]
    if sorted(inventory_paths) != sorted(emitted_paths):
        raise SystemExit("Final implementation bundle inventory and emitted file paths must match.")
    agent_reviews = payload.get("agent_reviews")
    if not isinstance(agent_reviews, dict):
        raise SystemExit("Final implementation bundle agent_reviews must be an object.")
    for key in ("operator_codex", "codex_review_agent", "claude_review_agent"):
        if key not in agent_reviews:
            raise SystemExit(f"Final implementation bundle missing agent review: {key}")


def _verify_declared_artifact(root: Path, path: str, expected_sha256: str, label: str) -> dict[str, str]:
    resolved = resolve_under_root(root, path, must_exist=True)
    if not resolved.is_file() or sha256_file(resolved) != expected_sha256:
        raise SystemExit(f"{label} path/hash verification failed: {path}")
    return {"path": relpath(root, resolved), "sha256": expected_sha256}


def _accepted_final_cycle(root: Path, session: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    cycles = [cycle for cycle in session.get("review_cycles", []) if cycle.get("review_kind") == "final_packet" and cycle.get("acceptance_status") == "accepted"]
    if len(cycles) != 1:
        raise SystemExit("Finalization requires exactly one accepted final_packet review cycle.")
    cycle = cycles[0]
    subject = _load_cycle_subject(root, cycle)
    _verify_subject_artifacts(root, subject)
    binding = cycle.get("acceptance_binding")
    if not isinstance(binding, dict):
        raise SystemExit("Finalization requires the final review cycle acceptance binding.")
    binding_path = resolve_under_root(root, binding["path"], must_exist=True)
    if sha256_file(binding_path) != binding.get("sha256"):
        raise SystemExit("Final review acceptance binding hash mismatch.")
    acceptance_binding = load_json(binding_path, "final review acceptance binding")
    supervisor_artifacts.validate_against_schema(acceptance_binding, "operator_acceptance_binding.schema.json", "final review acceptance binding")
    expected_binding = {
        "supervisor_session_id": session["supervisor_session_id"],
        "review_cycle_id": cycle["review_cycle_id"],
        "subject_id": subject["subject_id"],
        "subject_sha256": cycle["subject_sha256"],
        "approval_decision": "approve",
        "acceptance_path": cycle.get("acceptance_record"),
        "acceptance_sha256": cycle.get("acceptance_record_sha256"),
    }
    mismatches = [key for key, value in expected_binding.items() if acceptance_binding.get(key) != value]
    if mismatches:
        raise SystemExit(f"Final review acceptance binding mismatch: {', '.join(mismatches)}")
    quorum_path = resolve_under_root(root, acceptance_binding["quorum_path"], must_exist=True)
    if sha256_file(quorum_path) != acceptance_binding["quorum_sha256"]:
        raise SystemExit("Final review quorum hash mismatch.")
    quorum = load_json(quorum_path, "final review quorum")
    supervisor_artifacts.validate_against_schema(quorum, "review_quorum.schema.json", "final review quorum")
    if quorum.get("quorum_status") != "passed" or quorum.get("subject_id") != subject["subject_id"]:
        raise SystemExit("Final review quorum did not pass for the accepted subject.")
    return cycle, subject


def _reviewed_subject_records(root: Path, subject: dict[str, Any]) -> dict[str, dict[str, Any]]:
    manifest = load_json(
        resolve_under_root(root, subject["reviewed_artifact_manifest_path"], must_exist=True),
        "reviewed artifact manifest",
    )
    return {str(item["path"]): item for item in manifest.get("artifacts", []) if isinstance(item, dict) and item.get("path")}


def _final_payload_body(payload: dict[str, Any]) -> dict[str, Any]:
    schema_version = payload.get("schema_version")
    if schema_version == FINAL_IMPLEMENTATION_BUNDLE_SCHEMA_VERSION:
        excluded = {"agent_reviews", "consolidation", "operator_acceptance"}
    elif schema_version == "responses_runner_v2.final_delivery_bundle.v1":
        excluded = {"reviews", "operator_acceptance"}
    else:
        raise SystemExit("Final bundle has an unsupported schema_version.")
    return {key: value for key, value in payload.items() if key not in excluded}


def _require_reviewed_final_draft(
    root: Path,
    subject: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, str]:
    draft_value = subject.get("final_packet_draft_path")
    draft_sha256 = subject.get("final_packet_draft_sha256")
    if not isinstance(draft_value, str) or not isinstance(draft_sha256, str):
        raise SystemExit("Accepted final review subject is missing its final-packet draft binding.")
    draft_path = resolve_under_root(root, draft_value, must_exist=True)
    if sha256_file(draft_path) != draft_sha256:
        raise SystemExit("Reviewed final-packet draft hash mismatch.")
    draft = load_json(draft_path, "reviewed final-packet draft")
    if _canonical_sha256(draft) != _canonical_sha256(_final_payload_body(payload)):
        raise SystemExit("Finalization payload body differs from the exact reviewed final-packet draft.")
    return {"path": relpath(root, draft_path), "sha256": draft_sha256}


def _require_reviewed_artifact(
    root: Path,
    reviewed: dict[str, dict[str, Any]],
    path: str | Path,
    expected_sha256: str,
    label: str,
) -> dict[str, str]:
    verified = _verify_declared_artifact(root, path, expected_sha256, label)
    record = reviewed.get(verified["path"])
    if not isinstance(record, dict) or record.get("sha256") != verified["sha256"]:
        raise SystemExit(f"{label} was not path/hash-bound in the accepted final review subject: {verified['path']}")
    return verified


def _terminal_artifact_record(root: Path, subject: dict[str, Any], reviewed: dict[str, dict[str, Any]]) -> dict[str, str]:
    checkpoint = load_json(resolve_under_root(root, subject["checkpoint_path"], must_exist=True), "terminal stage checkpoint")
    artifacts = checkpoint.get("artifacts") if isinstance(checkpoint.get("artifacts"), dict) else {}
    path_value = artifacts.get("artifact_markdown_path")
    if not isinstance(path_value, str) or not path_value:
        raise SystemExit("Terminal stage checkpoint does not identify its primary artifact.md.")
    artifact_path = resolve_under_root(root, path_value, must_exist=True)
    digest = sha256_file(artifact_path)
    declared = artifacts.get("artifact_markdown_sha256")
    if declared is not None and declared != digest:
        raise SystemExit("Terminal stage checkpoint artifact hash mismatch.")
    manifest = load_json(resolve_under_root(root, subject["run_manifest_path"], must_exist=True), "terminal run manifest")
    stages = [item for item in manifest.get("stages", []) if item.get("stage_id") == subject.get("stage_id")]
    if len(stages) != 1:
        raise SystemExit("Terminal stage is not uniquely bound in the accepted run manifest.")
    stage_path = stages[0].get("artifact_markdown_path")
    stage_sha256 = stages[0].get("artifact_markdown_sha256")
    if not isinstance(stage_path, str) or not isinstance(stage_sha256, str):
        raise SystemExit("Terminal run manifest does not bind its primary artifact path/hash.")
    if resolve_under_root(root, stage_path, must_exist=True) != artifact_path or stage_sha256 != digest:
        raise SystemExit("Terminal artifact does not match the accepted run manifest and checkpoint.")
    return _require_reviewed_artifact(root, reviewed, artifact_path, digest, "terminal artifact")


def _verify_final_review_paths(root: Path, cycle: dict[str, Any], review_paths: dict[str, str], consolidation_path: str, acceptance_path: str) -> list[dict[str, str]]:
    expected = {
        "operator_codex": cycle.get("operator_provisional_record"),
        "codex_review_agent": (cycle.get("review_agent_outputs") or {}).get("codex_review_agent"),
        "claude_review_agent": (cycle.get("review_agent_outputs") or {}).get("claude_review_agent"),
    }
    if set(review_paths) != set(REVIEW_ROLES):
        raise SystemExit("Final bundle must provide the exact operator, Codex, and Claude review paths.")
    verified: list[dict[str, str]] = []
    for role, path in review_paths.items():
        if role not in expected or not isinstance(expected[role], str):
            raise SystemExit(f"Unexpected or missing final review role: {role}")
        resolved = resolve_under_root(root, path, must_exist=True)
        if resolved != resolve_under_root(root, expected[role], must_exist=True):
            raise SystemExit(f"Final bundle {role} path is not the cycle-recorded review.")
        gate = (cycle.get("review_gates") or {}).get(role)
        if not isinstance(gate, dict) or gate.get("gate_status") != "passed" or gate.get("decision_sha256") != sha256_file(resolved):
            raise SystemExit(f"Final bundle {role} review hash mismatch.")
        verified.append({"path": relpath(root, resolved), "sha256": sha256_file(resolved)})
    for label, supplied, expected_path, expected_hash in (
        ("consolidation", consolidation_path, cycle.get("consolidation"), cycle.get("consolidation_sha256")),
        ("operator acceptance", acceptance_path, cycle.get("acceptance_record"), cycle.get("acceptance_record_sha256")),
    ):
        if not isinstance(expected_path, str):
            raise SystemExit(f"Final review cycle is missing {label}.")
        resolved = resolve_under_root(root, supplied, must_exist=True)
        if resolved != resolve_under_root(root, expected_path, must_exist=True) or sha256_file(resolved) != expected_hash:
            raise SystemExit(f"Final bundle {label} path/hash mismatch.")
        verified.append({"path": relpath(root, resolved), "sha256": sha256_file(resolved)})
    return verified


def _verify_final_review_markdown(root: Path, cycle: dict[str, Any]) -> list[dict[str, str]]:
    verified: list[dict[str, str]] = []
    for role in REVIEW_ROLES:
        gate = (cycle.get("review_gates") or {}).get(role)
        if not isinstance(gate, dict):
            raise SystemExit(f"Final review cycle is missing the {role} gate.")
        markdown_value = gate.get("markdown_path")
        markdown_sha256 = gate.get("markdown_sha256")
        if not isinstance(markdown_value, str) or not isinstance(markdown_sha256, str):
            raise SystemExit(f"Final {role} review lacks a path/hash-bound markdown record.")
        markdown = resolve_under_root(root, markdown_value, must_exist=True)
        if sha256_file(markdown) != markdown_sha256:
            raise SystemExit(f"Final {role} review markdown hash mismatch.")
        decision = load_json(resolve_under_root(root, gate["decision_path"], must_exist=True), f"{role} final decision")
        if decision.get("markdown_report_path") != relpath(root, markdown):
            raise SystemExit(f"Final {role} review markdown path does not match its decision.")
        verified.append({"path": relpath(root, markdown), "sha256": sha256_file(markdown)})
    for label, decision_value, markdown_value, expected_hash in (
        ("consolidation", cycle.get("consolidation"), cycle["derived_paths"]["consolidation_md"], cycle.get("consolidation_markdown_sha256")),
        ("operator acceptance", cycle.get("acceptance_record"), cycle["derived_paths"]["acceptance_md"], cycle.get("acceptance_markdown_sha256")),
    ):
        if not isinstance(decision_value, str) or not isinstance(expected_hash, str):
            raise SystemExit(f"Final review cycle is missing the {label} markdown binding.")
        markdown = resolve_under_root(root, markdown_value, must_exist=True)
        if sha256_file(markdown) != expected_hash:
            raise SystemExit(f"Final {label} markdown hash mismatch.")
        decision = load_json(resolve_under_root(root, decision_value, must_exist=True), f"final {label}")
        if decision.get("markdown_report_path") != relpath(root, markdown):
            raise SystemExit(f"Final {label} markdown path does not match its decision.")
        verified.append({"path": relpath(root, markdown), "sha256": sha256_file(markdown)})
    return verified


def create_final_implementation_bundle(*, root: Path, session_ref: str | Path, payload: dict[str, Any], output: str | Path | None) -> dict[str, Any]:
    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _session_finalization_lock(session_path):
        return _create_final_implementation_bundle_locked(root=root, session_ref=session_ref, payload=payload, output=output)


def _create_final_implementation_bundle_locked(*, root: Path, session_ref: str | Path, payload: dict[str, Any], output: str | Path | None) -> dict[str, Any]:
    """Validate and record the final implementation bundle for a supervisor session."""

    session, session_path = _load_session_and_path(root, session_ref)
    if session.get("final_bundle") is not None:
        raise SystemExit("Supervisor session final bundle is immutable once recorded.")
    cycle, subject = _accepted_final_cycle(root, session)
    bundle = dict(payload)
    if bundle.get("schema_version") != FINAL_IMPLEMENTATION_BUNDLE_SCHEMA_VERSION:
        raise SystemExit("Final implementation bundle must carry the current schema_version in its reviewed draft.")
    _require_final_bundle_payload(bundle)
    output_path = _require_derived_path(root, output, relpath(root, session_path / "final_bundle" / "final_implementation_bundle.json"), "final implementation bundle output")
    if any(item.get("status") != "passed" for item in bundle["validation_evidence"]):
        raise SystemExit("Final implementation bundle cannot complete with failed or blocked validation evidence.")
    reviewed = _reviewed_subject_records(root, subject)
    verified: list[dict[str, str]] = [_require_reviewed_final_draft(root, subject, bundle)]
    emitted = {str(item["path"]): str(item["sha256"]) for item in bundle["emitted_files"]}
    for path, digest in sorted(emitted.items()):
        verified.append(_require_reviewed_artifact(root, reviewed, path, digest, "emitted file"))
    review_paths = {role: str(ref["json_path"]) for role, ref in bundle["agent_reviews"].items()}
    verified.extend(
        _verify_final_review_paths(
            root,
            cycle,
            review_paths,
            str(bundle["consolidation"]["json_path"]),
            str(bundle["operator_acceptance"]["json_path"]),
        )
    )
    verified.extend(_verify_final_review_markdown(root, cycle))
    review_refs = list(bundle["agent_reviews"].values()) + [bundle["consolidation"], bundle["operator_acceptance"]]
    for ref in review_refs:
        decision_payload = load_json(resolve_under_root(root, ref["json_path"], must_exist=True), "final review reference")
        markdown = resolve_under_root(root, ref["markdown_path"], must_exist=True)
        if relpath(root, markdown) != decision_payload.get("markdown_report_path"):
            raise SystemExit("Final implementation bundle review markdown does not match its decision record.")
    _write_once_json(root, output_path, bundle, "final_implementation_bundle.schema.json", "final implementation bundle")
    binding_path = session_path / "final_bundle" / "final_implementation_bundle.binding.json"
    binding = {
        "schema_version": FINAL_BUNDLE_BINDING_SCHEMA_VERSION,
        "created_at": bundle["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "reviewed_subject_id": subject["subject_id"],
        "reviewed_subject_sha256": cycle["subject_sha256"],
        "reviewed_draft_path": subject["final_packet_draft_path"],
        "reviewed_draft_sha256": subject["final_packet_draft_sha256"],
        "bundle_path": relpath(root, output_path),
        "bundle_sha256": sha256_file(output_path),
        "schema_version_validated": FINAL_IMPLEMENTATION_BUNDLE_SCHEMA_VERSION,
        "verified_artifacts": verified,
    }
    _write_once_json(root, binding_path, binding, "final_bundle_binding.schema.json", "final bundle binding")
    session["final_bundle"] = {
        "bundle_path": relpath(root, output_path),
        "schema_validation_status": "validated",
        "file_inventory_hash": sha256_text(json.dumps(bundle["file_inventory"], sort_keys=True)),
        "acceptance_record_id": str(bundle.get("operator_acceptance", {}).get("decision_id", "")),
        "bundle_sha256": sha256_file(output_path),
        "binding_path": relpath(root, binding_path),
        "binding_sha256": sha256_file(binding_path),
    }
    session["status"] = "completed"
    session["current_phase"] = "finalization"
    _write_session(root, session_path, session)
    return bundle


def create_final_delivery_bundle(*, root: Path, session_ref: str | Path, payload: dict[str, Any], output: str | Path | None) -> dict[str, Any]:
    session_path = supervisor_artifacts.session_dir(root, session_ref)
    with _session_finalization_lock(session_path):
        return _create_final_delivery_bundle_locked(root=root, session_ref=session_ref, payload=payload, output=output)


def _create_final_delivery_bundle_locked(*, root: Path, session_ref: str | Path, payload: dict[str, Any], output: str | Path | None) -> dict[str, Any]:
    """Validate, verify, and record the domain-neutral final delivery bundle."""

    session, session_path = _load_session_and_path(root, session_ref)
    if session.get("final_bundle") is not None:
        raise SystemExit("Supervisor session final bundle is immutable once recorded.")
    cycle, subject = _accepted_final_cycle(root, session)
    bundle = dict(payload)
    if bundle.get("schema_version") != "responses_runner_v2.final_delivery_bundle.v1":
        raise SystemExit("Unexpected final delivery bundle schema_version.")
    supervisor_artifacts.validate_against_schema(bundle, "final_delivery_bundle.schema.json", "final delivery bundle")
    reviewed_draft = _require_reviewed_final_draft(root, subject, bundle)
    output_path = _require_derived_path(root, output, relpath(root, session_path / "final_bundle" / "final_delivery_bundle.json"), "final delivery bundle output")
    if any(item.get("status") == "failed" for item in bundle["validation_evidence"]):
        raise SystemExit("Final delivery bundle cannot complete with failed validation evidence.")
    if subject.get("workflow_id") != bundle["subject"]["workflow_id"] or subject.get("run_id") != bundle["subject"]["run_id"] or subject.get("stage_id") != bundle["subject"]["terminal_stage_id"]:
        raise SystemExit("Final delivery subject does not match the accepted final review subject.")
    if subject.get("attempt_id") != bundle["subject"]["terminal_attempt_id"]:
        raise SystemExit("Final delivery terminal attempt does not match the accepted final review subject.")
    reviewed = _reviewed_subject_records(root, subject)
    terminal_artifact = _terminal_artifact_record(root, subject, reviewed)
    if bundle["subject"]["terminal_artifact_sha256"] != terminal_artifact["sha256"]:
        raise SystemExit("Final delivery terminal artifact does not match the exact reviewed terminal artifact.")
    registered = next((item for item in session["runs"] if item.get("run_id") == bundle["subject"]["run_id"]), None)
    if not isinstance(registered, dict):
        raise SystemExit("Final delivery bundle references an unregistered run.")
    _require_registered_run(root, session, registered["run_dir"])
    verified: list[dict[str, str]] = [reviewed_draft, terminal_artifact]
    for item in bundle["deliverables"]:
        verified.append(_require_reviewed_artifact(root, reviewed, item["path"], item["sha256"], "deliverable"))
    review_map = {item["role"]: item for item in bundle["reviews"]}
    if set(review_map) != {"operator_codex", "codex_review_agent", "claude_review_agent", "consolidation"}:
        raise SystemExit("Final delivery bundle must name the exact operator, Codex, Claude, and consolidation reviews.")
    review_paths = {role: str(review_map[role]["artifact_path"]) for role in REVIEW_ROLES}
    for role, item in review_map.items():
        verified.append(_verify_declared_artifact(root, item["artifact_path"], item["artifact_sha256"], f"{role} review"))
    verified.extend(
        _verify_final_review_paths(
            root,
            cycle,
            review_paths,
            str(review_map["consolidation"]["artifact_path"]),
            str(bundle["operator_acceptance"]["artifact_path"]),
        )
    )
    verified.extend(_verify_final_review_markdown(root, cycle))
    verified.append(_verify_declared_artifact(root, bundle["operator_acceptance"]["artifact_path"], bundle["operator_acceptance"]["artifact_sha256"], "operator acceptance"))
    for item in bundle["evidence"]:
        if item.get("citation_type") != "url":
            if not isinstance(item.get("sha256"), str):
                raise SystemExit("Non-URL final evidence requires a reviewed SHA-256 binding.")
            verified.append(
                _require_reviewed_artifact(
                    root,
                    reviewed,
                    item["locator"],
                    item["sha256"],
                    "evidence",
                )
            )
    _write_once_json(root, output_path, bundle, "final_delivery_bundle.schema.json", "final delivery bundle")
    binding_path = session_path / "final_bundle" / "final_delivery_bundle.binding.json"
    binding = {
        "schema_version": FINAL_BUNDLE_BINDING_SCHEMA_VERSION,
        "created_at": bundle["created_at"],
        "supervisor_session_id": session["supervisor_session_id"],
        "reviewed_subject_id": subject["subject_id"],
        "reviewed_subject_sha256": cycle["subject_sha256"],
        "reviewed_draft_path": subject["final_packet_draft_path"],
        "reviewed_draft_sha256": subject["final_packet_draft_sha256"],
        "bundle_path": relpath(root, output_path),
        "bundle_sha256": sha256_file(output_path),
        "schema_version_validated": "responses_runner_v2.final_delivery_bundle.v1",
        "verified_artifacts": sorted({(item["path"], item["sha256"]) for item in verified}),
    }
    binding["verified_artifacts"] = [{"path": path, "sha256": digest} for path, digest in binding["verified_artifacts"]]
    _write_once_json(root, binding_path, binding, "final_bundle_binding.schema.json", "final bundle binding")
    session["final_bundle"] = {
        "bundle_path": relpath(root, output_path),
        "schema_validation_status": "validated",
        "file_inventory_hash": _canonical_sha256(bundle["deliverables"]),
        "acceptance_record_id": str(bundle["operator_acceptance"]["decision_id"]),
        "bundle_sha256": sha256_file(output_path),
        "binding_path": relpath(root, binding_path),
        "binding_sha256": sha256_file(binding_path),
    }
    session["status"] = "completed"
    session["current_phase"] = "finalization"
    _write_session(root, session_path, session)
    return bundle


def finalize_bundle(*, root: Path, session_ref: str | Path, packet_json: str | Path, output: str | Path | None = None) -> dict[str, Any]:
    """Load a packet JSON file and write the validated final implementation bundle."""

    payload = load_json(resolve_under_root(root, packet_json, must_exist=True), "final bundle packet")
    if payload.get("schema_version") == "responses_runner_v2.final_delivery_bundle.v1":
        return create_final_delivery_bundle(root=root, session_ref=session_ref, payload=payload, output=output)
    return create_final_implementation_bundle(root=root, session_ref=session_ref, payload=payload, output=output)


def validate_session(*, root: Path, session_ref: str | Path) -> dict[str, Any]:
    """Validate the supervisor session manifest against its JSON schema."""

    session, session_path = _load_session_and_path(root, session_ref)
    schema_file = "supervisor_session.v2.schema.json" if session.get("schema_version") == "responses_runner_v2.supervisor_session.v2" else "supervisor_session.schema.json"
    supervisor_artifacts.validate_against_schema(
        {key: value for key, value in session.items() if not key.startswith("_")},
        schema_file,
        "supervisor session",
    )
    return {"session": session["supervisor_session_id"], "manifest": relpath(root, supervisor_artifacts.session_manifest_path(session_path)), "status": "valid"}
