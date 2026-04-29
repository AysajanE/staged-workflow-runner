from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    DIRECTORY_SKIP_NAMES,
    SUPERVISOR_ARCHIVE_SCHEMA_VERSION,
    SUPERVISOR_SESSION_SCHEMA_VERSION,
    normalize_slug,
    relpath,
    resolve_under_root,
    runner_now,
    schema_dir,
    sha256_file,
    sha256_text,
    write_json,
    write_text,
)

SUPERVISOR_OUTPUT_ROOT = ".local/automation/responses_runner_v2/supervisor_sessions"


class SchemaValidationError(RuntimeError):
    pass


def _schema_path(schema_filename: str) -> Path:
    path = schema_dir() / schema_filename
    if not path.exists():
        raise SystemExit(f"Missing supervisor schema: {path}")
    return path


def _type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True


def _resolve_ref(schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaValidationError(f"Unsupported external schema ref: {ref}")
    current: Any = schema
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise SchemaValidationError(f"Unresolved schema ref: {ref}")
        current = current[part]
    if not isinstance(current, dict):
        raise SchemaValidationError(f"Schema ref does not resolve to an object: {ref}")
    return current


def _fallback_validate(instance: Any, subschema: dict[str, Any], root_schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    if "$ref" in subschema:
        return _fallback_validate(instance, _resolve_ref(root_schema, str(subschema["$ref"])), root_schema, path)

    if "allOf" in subschema:
        for index, item in enumerate(subschema["allOf"]):
            if isinstance(item, dict):
                errors.extend(_fallback_validate(instance, item, root_schema, f"{path}.allOf[{index}]"))

    if "anyOf" in subschema:
        any_errors = []
        for item in subschema["anyOf"]:
            if isinstance(item, dict):
                item_errors = _fallback_validate(instance, item, root_schema, path)
                if not item_errors:
                    any_errors = []
                    break
                any_errors.append(item_errors)
        if any_errors:
            errors.append(f"{path}: does not match any allowed schema")

    if "const" in subschema and instance != subschema["const"]:
        errors.append(f"{path}: expected const {subschema['const']!r}, got {instance!r}")

    if "enum" in subschema and instance not in subschema["enum"]:
        errors.append(f"{path}: value {instance!r} not in enum {subschema['enum']!r}")

    expected_type = subschema.get("type")
    if isinstance(expected_type, str) and not _type_matches(instance, expected_type):
        errors.append(f"{path}: expected type {expected_type}, got {type(instance).__name__}")
        return errors

    if isinstance(instance, dict):
        required = subschema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in instance:
                    errors.append(f"{path}: missing required key {key!r}")
        properties = subschema.get("properties")
        if isinstance(properties, dict):
            for key, value in instance.items():
                if key in properties and isinstance(properties[key], dict):
                    errors.extend(_fallback_validate(value, properties[key], root_schema, f"{path}.{key}"))
                elif subschema.get("additionalProperties") is False:
                    errors.append(f"{path}: additional property {key!r} is not allowed")

    if isinstance(instance, list):
        min_items = subschema.get("minItems")
        if isinstance(min_items, int) and len(instance) < min_items:
            errors.append(f"{path}: expected at least {min_items} items")
        item_schema = subschema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(_fallback_validate(item, item_schema, root_schema, f"{path}[{index}]"))

    if isinstance(instance, str):
        min_length = subschema.get("minLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            errors.append(f"{path}: expected string length >= {min_length}")
        pattern = subschema.get("pattern")
        if isinstance(pattern, str) and re.fullmatch(pattern, instance) is None:
            errors.append(f"{path}: string {instance!r} does not match {pattern!r}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = subschema.get("minimum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            errors.append(f"{path}: expected value >= {minimum}")

    return errors


def validate_against_schema(payload: Any, schema_filename: str, label: str) -> None:
    schema_path = _schema_path(schema_filename)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        import jsonschema  # type: ignore
    except Exception:
        errors = _fallback_validate(payload, schema, schema)
        if errors:
            raise SchemaValidationError(f"{label} failed schema validation: " + "; ".join(errors))
        return

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'/'.join(str(part) for part in error.path) or '$'}: {error.message}"
            for error in errors[:10]
        )
        raise SchemaValidationError(f"{label} failed schema validation: {details}")


def write_json_validated(path: Path, payload: Any, schema_filename: str, label: str) -> Path:
    try:
        validate_against_schema(payload, schema_filename, label)
    except SchemaValidationError as exc:
        raise SystemExit(str(exc)) from exc
    return write_json(path, payload)


def load_json_validated(path: Path, schema_filename: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON for {label}: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{label} must be a JSON object: {path}")
    try:
        validate_against_schema(payload, schema_filename, label)
    except SchemaValidationError as exc:
        raise SystemExit(str(exc)) from exc
    return payload


def new_supervisor_session_id(prefix: str = "sup") -> str:
    return f"{prefix}_{runner_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def supervisor_sessions_root(root: Path) -> Path:
    path = resolve_under_root(root, SUPERVISOR_OUTPUT_ROOT, must_exist=False)
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_session_dir(root: Path, session_id: str | None = None) -> tuple[str, Path]:
    session_id = normalize_slug(session_id or new_supervisor_session_id())
    path = supervisor_sessions_root(root) / session_id
    path.mkdir(parents=False, exist_ok=False)
    for child in ("commands", "review_cycles", "scaffolds", "archives", "final_bundle", "human_pauses", "monitoring", "dry_runs"):
        (path / child).mkdir(parents=True, exist_ok=True)
    return session_id, path


def session_dir(root: Path, session_id_or_path: str | Path) -> Path:
    raw = Path(session_id_or_path)
    if raw.is_absolute() or len(raw.parts) > 1:
        return resolve_under_root(root, raw, must_exist=True)
    return resolve_under_root(root, supervisor_sessions_root(root) / str(session_id_or_path), must_exist=True)


def session_manifest_path(session_path: Path) -> Path:
    return session_path / "supervisor_session.json"


def load_session(root: Path, session_ref: str | Path) -> dict[str, Any]:
    path = session_dir(root, session_ref)
    manifest_path = session_manifest_path(path)
    payload = load_json_validated(manifest_path, "supervisor_session.schema.json", "supervisor session")
    payload["_session_dir"] = relpath(root, path)
    payload["_manifest_path"] = relpath(root, manifest_path)
    return payload


def write_session(root: Path, session_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    writable = {key: value for key, value in payload.items() if not key.startswith("_")}
    writable["updated_at"] = runner_now().isoformat()
    manifest_path = session_manifest_path(session_path)
    tmp = manifest_path.with_suffix(".json.tmp")
    write_json_validated(tmp, writable, "supervisor_session.schema.json", "supervisor session")
    tmp.replace(manifest_path)
    loaded = dict(writable)
    loaded["_session_dir"] = relpath(root, session_path)
    loaded["_manifest_path"] = relpath(root, manifest_path)
    return loaded


def write_json_artifact(root: Path, path: str | Path, payload: Any, schema_filename: str | None = None, label: str = "artifact") -> str:
    resolved = resolve_under_root(root, path, must_exist=False)
    if schema_filename:
        write_json_validated(resolved, payload, schema_filename, label)
    else:
        write_json(resolved, payload)
    return relpath(root, resolved)


def write_text_artifact(root: Path, path: str | Path, text: str) -> str:
    resolved = resolve_under_root(root, path, must_exist=False)
    write_text(resolved, text)
    return relpath(root, resolved)


def artifact_record(root: Path, path: str | Path, role: str) -> dict[str, Any]:
    resolved = resolve_under_root(root, path, must_exist=True)
    return {
        "path": relpath(root, resolved),
        "role": role,
        "sha256": sha256_file(resolved),
        "bytes": resolved.stat().st_size,
    }


def _iter_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
        return
    for child in sorted(path.rglob("*")):
        if child.is_file():
            if any(part in DIRECTORY_SKIP_NAMES for part in child.relative_to(path.anchor if child.is_absolute() else child.parent).parts):
                continue
            yield child


def hash_manifest(root: Path, target: str | Path, output_path: str | Path) -> str:
    resolved = resolve_under_root(root, target, must_exist=True)
    records = []
    for file_path in sorted(_iter_files(resolved)):
        rel = relpath(root, file_path)
        if any(part in DIRECTORY_SKIP_NAMES for part in Path(rel).parts):
            continue
        records.append({"path": rel, "sha256": sha256_file(file_path), "bytes": file_path.stat().st_size})
    manifest = {
        "schema_version": "responses_runner_v2.hash_manifest.v1",
        "created_at": runner_now().isoformat(),
        "target": relpath(root, resolved),
        "aggregate_file_count": len(records),
        "files": records,
    }
    return write_json_artifact(root, output_path, manifest)


def hash_manifest_digest(root: Path, hash_manifest_path: str | Path) -> str:
    resolved = resolve_under_root(root, hash_manifest_path, must_exist=True)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise SystemExit(f"Hash manifest has no files: {resolved}")
    return sha256_text(json.dumps(files, sort_keys=True, ensure_ascii=False))


def copy_into_scaffold_version(root: Path, source: str | Path, destination: Path) -> str:
    resolved_source = resolve_under_root(root, source, must_exist=True)
    if resolved_source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(resolved_source, destination)
    else:
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved_source, destination / resolved_source.name)
    return relpath(root, destination)


def snapshot_workspace(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        relative_parts = file_path.relative_to(root).parts
        if any(part in DIRECTORY_SKIP_NAMES for part in relative_parts):
            continue
        snapshot[file_path.relative_to(root).as_posix()] = sha256_file(file_path)
    return snapshot


def diff_snapshots(before: dict[str, str], after: dict[str, str]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        if old == new:
            continue
        status = "created" if old is None else "deleted" if new is None else "modified"
        changes.append({"path": path, "status": status, "before_sha256": old or "", "after_sha256": new or ""})
    return changes


def write_diff(root: Path, output_path: str | Path, changes: list[dict[str, str]]) -> str:
    lines = ["# Read-only snapshot diff", ""]
    if not changes:
        lines.append("No workspace source changes detected.")
    for change in changes:
        lines.append(
            f"- {change['status']}: {change['path']} "
            f"({change['before_sha256']} -> {change['after_sha256']})"
        )
    return write_text_artifact(root, output_path, "\n".join(lines).rstrip() + "\n")


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _stage_dir_from_run(run_dir: Path, stage_id: str) -> Path | None:
    stages = sorted((run_dir / "stages").glob(f"*_{stage_id}"))
    return stages[0] if stages else None


def compute_request_evidence(root: Path, run_dir: str | Path, stage_id: str) -> dict[str, Any]:
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    run_manifest_path = resolved_run_dir / "run_manifest.json"
    run_manifest = _safe_load_json(run_manifest_path) or {}
    stage_dir = _stage_dir_from_run(resolved_run_dir, stage_id)
    evidence_files: list[dict[str, Any]] = []
    if run_manifest_path.exists():
        evidence_files.append(artifact_record(root, run_manifest_path, "run_manifest"))

    stage_summary = {}
    for item in run_manifest.get("stages", []) if isinstance(run_manifest.get("stages"), list) else []:
        if isinstance(item, dict) and item.get("stage_id") == stage_id:
            stage_summary = item
            break

    candidate_paths: list[tuple[str, Path]] = []
    if stage_dir is not None:
        candidate_paths.extend(
            [
                ("request_payload", stage_dir / "request_payload.json"),
                ("input_manifest_json", stage_dir / "input_manifest.json"),
                ("input_manifest_markdown", stage_dir / "input_manifest.md"),
                ("stage_checkpoint", stage_dir / "stage_checkpoint.json"),
                ("response_latest_json", stage_dir / "response.latest.json"),
                ("response_final_json", stage_dir / "response.final.json"),
            ]
        )
    workflow_manifest_path_value = run_manifest.get("workflow_manifest_path")
    if isinstance(workflow_manifest_path_value, str) and workflow_manifest_path_value:
        workflow_manifest_path = resolve_under_root(root, workflow_manifest_path_value, must_exist=False)
        candidate_paths.append(("workflow_manifest", workflow_manifest_path))
    review_bundle_value = stage_summary.get("review_bundle_path") or stage_summary.get("consumed_review_bundle_path")
    if isinstance(review_bundle_value, str) and review_bundle_value:
        candidate_paths.append(("review_bundle", resolve_under_root(root, review_bundle_value, must_exist=False)))

    request_payload = None
    for role, candidate in candidate_paths:
        if candidate.exists() and candidate.is_file():
            evidence_files.append(artifact_record(root, candidate, role))
            if role == "request_payload":
                request_payload = _safe_load_json(candidate)

    model_tool_settings: dict[str, Any] = {}
    if isinstance(request_payload, dict):
        for key in ("model", "reasoning", "text", "max_output_tokens", "prompt_cache_retention", "tools", "tool_choice", "max_tool_calls", "parallel_tool_calls", "service_tier"):
            if key in request_payload:
                model_tool_settings[key] = request_payload[key]

    evidence = {
        "status": "complete" if any(item["role"] == "request_payload" for item in evidence_files) and any(item["role"] == "input_manifest_json" for item in evidence_files) else "missing_required_evidence",
        "run_dir": relpath(root, resolved_run_dir),
        "stage_id": stage_id,
        "workflow_manifest_path": run_manifest.get("workflow_manifest_path"),
        "workflow_manifest_sha256": run_manifest.get("workflow_manifest_sha256"),
        "stage_summary": stage_summary,
        "model_tool_settings": model_tool_settings,
        "evidence_files": evidence_files,
    }
    evidence["request_hash"] = sha256_text(json.dumps(evidence, sort_keys=True, ensure_ascii=False))
    return evidence


def latest_scaffold_evidence(root: Path, session: dict[str, Any]) -> dict[str, Any]:
    versions = session.get("scaffold_versions")
    if not isinstance(versions, list) or not versions:
        return {"status": "missing_scaffold", "scaffold_hash": None, "hash_manifest_path": None}
    latest = versions[-1]
    if not isinstance(latest, dict):
        return {"status": "missing_scaffold", "scaffold_hash": None, "hash_manifest_path": None}
    manifest_path = latest.get("hash_manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path:
        return {"status": "missing_hash_manifest", "scaffold_hash": None, "hash_manifest_path": None}
    resolved = resolve_under_root(root, manifest_path, must_exist=False)
    if not resolved.exists():
        return {"status": "missing_hash_manifest", "scaffold_hash": None, "hash_manifest_path": manifest_path}
    digest = hash_manifest_digest(root, manifest_path)
    return {"status": "complete", "scaffold_hash": digest, "hash_manifest_path": manifest_path, "version_id": latest.get("version_id")}


def archive_attempt(
    *,
    root: Path,
    session_path: Path,
    session: dict[str, Any],
    run_dir: str | Path,
    stage_id: str,
    reason: str,
    retry_budget_before: dict[str, Any],
    retry_budget_after: dict[str, Any],
) -> dict[str, Any]:
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    request_evidence = compute_request_evidence(root, resolved_run_dir, stage_id)
    scaffold_evidence = latest_scaffold_evidence(root, session)
    archive_id = normalize_slug(f"archive_{runner_now().strftime('%Y%m%d_%H%M%S')}_{stage_id}_{uuid.uuid4().hex[:8]}")
    archive_dir = session_path / "archives" / archive_id
    archive_dir.mkdir(parents=True, exist_ok=False)

    included: list[dict[str, Any]] = []
    for item in request_evidence.get("evidence_files", []):
        if not isinstance(item, dict) or not item.get("path"):
            continue
        source = resolve_under_root(root, str(item["path"]), must_exist=True)
        dest = archive_dir / "artifacts" / source.relative_to(resolved_run_dir).as_posix() if source.is_relative_to(resolved_run_dir) else archive_dir / "artifacts" / source.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        included.append(
            {
                "source_path": relpath(root, source),
                "archive_path": relpath(root, dest),
                "sha256": sha256_file(dest),
                "bytes": dest.stat().st_size,
            }
        )

    rerun_eligible = (
        request_evidence.get("status") == "complete"
        and scaffold_evidence.get("status") == "complete"
        and int(retry_budget_after.get("failed_no_artifact", 0)) >= 0
        and bool(included)
    )
    manifest = {
        "schema_version": SUPERVISOR_ARCHIVE_SCHEMA_VERSION,
        "archive_id": archive_id,
        "archived_at": runner_now().isoformat(),
        "reason": reason,
        "source": {
            "run_dir": relpath(root, resolved_run_dir),
            "run_id": request_evidence.get("stage_summary", {}).get("run_id"),
            "workflow_id": None,
            "stage_id": stage_id,
            "response_id": request_evidence.get("stage_summary", {}).get("response_id"),
        },
        "included_artifacts": included,
        "request_hash": str(request_evidence["request_hash"]),
        "scaffold_hash": str(scaffold_evidence.get("scaffold_hash") or ""),
        "request_evidence": request_evidence,
        "scaffold_evidence": scaffold_evidence,
        "unchanged_input_evidence": {
            "request_hash_before": str(request_evidence["request_hash"]),
            "scaffold_hash_before": str(scaffold_evidence.get("scaffold_hash") or ""),
            "rerun_requires_same_hashes": True,
        },
        "retry_budget_before": retry_budget_before,
        "retry_budget_after": retry_budget_after,
        "rerun_as_is_eligible": rerun_eligible,
    }
    manifest_path = archive_dir / "supervisor_archive.json"
    write_json_validated(manifest_path, manifest, "supervisor_archive.schema.json", "supervisor archive")
    manifest["archive_manifest_path"] = relpath(root, manifest_path)
    return manifest
