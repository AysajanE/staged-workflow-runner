from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .contracts import (
    DIRECTORY_SKIP_NAMES,
    is_skippable_junk_file,
    SUPERVISOR_ARCHIVE_SCHEMA_VERSION,
    SUPERVISOR_SESSION_SCHEMA_VERSION,
    normalize_slug,
    relpath,
    resolve_under_root,
    runner_now,
    schema_dir,
    sha256_file,
    sha256_text,
)

SUPERVISOR_OUTPUT_ROOT = ".local/automation/responses_runner_v2/supervisor_sessions"
PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700
SUPERVISOR_SESSION_SCHEMA_BY_VERSION = {
    "responses_runner_v2.supervisor_session.v1": "supervisor_session.schema.json",
    "responses_runner_v2.supervisor_session.v2": "supervisor_session.v2.schema.json",
}


class SchemaValidationError(RuntimeError):
    pass


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_bytes(path: Path, content: bytes) -> Path:
    """Atomically replace a supervisor artifact with owner-only permissions."""

    parent_existed = path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    if not parent_existed:
        os.chmod(path.parent, PRIVATE_DIR_MODE)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, PRIVATE_FILE_MODE)
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def write_private_text(path: Path, text: str) -> Path:
    return _atomic_write_bytes(path, text.encode("utf-8"))


def write_private_json(path: Path, payload: Any) -> Path:
    return write_private_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


@contextmanager
def _session_write_lock(session_path: Path) -> Iterable[None]:
    lock_path = session_path / ".supervisor_session.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, PRIVATE_FILE_MODE)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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
    return write_private_json(path, payload)


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


def _supervisor_session_schema(payload: dict[str, Any]) -> str:
    version = payload.get("schema_version")
    schema_filename = SUPERVISOR_SESSION_SCHEMA_BY_VERSION.get(str(version))
    if schema_filename is None:
        supported = ", ".join(sorted(SUPERVISOR_SESSION_SCHEMA_BY_VERSION))
        raise SystemExit(f"Unsupported supervisor session schema_version {version!r}; expected one of: {supported}.")
    return schema_filename


def new_supervisor_session_id(prefix: str = "sup") -> str:
    return f"{prefix}_{runner_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def supervisor_sessions_root(root: Path) -> Path:
    path = resolve_under_root(root, SUPERVISOR_OUTPUT_ROOT, must_exist=False)
    path.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    os.chmod(path, PRIVATE_DIR_MODE)
    return path


def create_session_dir(root: Path, session_id: str | None = None) -> tuple[str, Path]:
    session_id = normalize_slug(session_id or new_supervisor_session_id())
    path = supervisor_sessions_root(root) / session_id
    path.mkdir(parents=False, exist_ok=False, mode=PRIVATE_DIR_MODE)
    for child in ("commands", "review_cycles", "scaffolds", "archives", "final_bundle", "human_pauses", "monitoring", "dry_runs"):
        (path / child).mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
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
    try:
        raw_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON for supervisor session: {manifest_path}: {exc}") from exc
    if not isinstance(raw_payload, dict):
        raise SystemExit(f"Supervisor session must be a JSON object: {manifest_path}")
    schema_filename = _supervisor_session_schema(raw_payload)
    try:
        validate_against_schema(raw_payload, schema_filename, "supervisor session")
    except SchemaValidationError as exc:
        raise SystemExit(str(exc)) from exc
    payload = raw_payload
    payload["_session_dir"] = relpath(root, path)
    payload["_manifest_path"] = relpath(root, manifest_path)
    if payload.get("schema_version") == SUPERVISOR_SESSION_SCHEMA_VERSION:
        payload["_revision"] = int(payload["revision"])
    else:
        revision_path = path / ".supervisor_session.revision"
        try:
            payload["_revision"] = int(revision_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError):
            payload["_revision"] = 0
    return payload


def write_session(root: Path, session_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    writable = {key: value for key, value in payload.items() if not key.startswith("_")}
    writable["updated_at"] = runner_now().isoformat()
    manifest_path = session_manifest_path(session_path)
    revision_path = session_path / ".supervisor_session.revision"
    expected_revision = payload.get("_revision")
    schema_version = writable.get("schema_version")
    is_v2 = schema_version == SUPERVISOR_SESSION_SCHEMA_VERSION
    with _session_write_lock(session_path):
        if manifest_path.exists() and is_v2:
            try:
                current_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                current_revision = int(current_payload["revision"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise SystemExit(
                    "Existing v2 supervisor session has no valid in-manifest revision; "
                    "recover it before updating."
                ) from exc
        else:
            try:
                current_revision = int(revision_path.read_text(encoding="utf-8").strip())
            except (FileNotFoundError, ValueError):
                current_revision = 0
        if manifest_path.exists() and expected_revision is None:
            raise SystemExit("Supervisor session update requires a loaded _revision for compare-and-swap.")
        if expected_revision is not None and int(expected_revision) != current_revision:
            raise SystemExit(
                "Supervisor session revision conflict: "
                f"expected {expected_revision}, current {current_revision}. Reload before retrying."
            )
        next_revision = current_revision + 1
        if is_v2:
            writable["revision"] = next_revision
        try:
            validate_against_schema(writable, _supervisor_session_schema(writable), "supervisor session")
        except SchemaValidationError as exc:
            raise SystemExit(str(exc)) from exc
        write_private_json(manifest_path, writable)
        if not is_v2:
            write_private_text(revision_path, f"{next_revision}\n")
    loaded = dict(writable)
    loaded["_session_dir"] = relpath(root, session_path)
    loaded["_manifest_path"] = relpath(root, manifest_path)
    loaded["_revision"] = next_revision
    payload["_revision"] = next_revision
    if is_v2:
        payload["revision"] = next_revision
    return loaded


def write_json_artifact(root: Path, path: str | Path, payload: Any, schema_filename: str | None = None, label: str = "artifact") -> str:
    resolved = resolve_under_root(root, path, must_exist=False)
    if schema_filename:
        write_json_validated(resolved, payload, schema_filename, label)
    else:
        write_private_json(resolved, payload)
    return relpath(root, resolved)


def write_text_artifact(root: Path, path: str | Path, text: str) -> str:
    resolved = resolve_under_root(root, path, must_exist=False)
    write_private_text(resolved, text)
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
            relative_parts = child.relative_to(path).parts
            if any(part in DIRECTORY_SKIP_NAMES for part in relative_parts[:-1]):
                continue
            if is_skippable_junk_file(relative_parts[-1]):
                continue
            yield child


def scaffold_content_sha256(root: Path, target: str | Path) -> str:
    """Hash scaffold content independently of its staging destination."""

    resolved = resolve_under_root(root, target, must_exist=True)
    records = [
        {
            "path": (
                file_path.relative_to(resolved).as_posix()
                if resolved.is_dir()
                else file_path.name
            ),
            "sha256": sha256_file(file_path),
            "bytes": file_path.stat().st_size,
        }
        for file_path in sorted(_iter_files(resolved))
    ]
    if not records:
        raise SystemExit(f"Scaffold has no files: {resolved}")
    return sha256_text(json.dumps(records, sort_keys=True, ensure_ascii=False))


def hash_manifest(root: Path, target: str | Path, output_path: str | Path) -> str:
    resolved = resolve_under_root(root, target, must_exist=True)
    records = []
    for file_path in sorted(_iter_files(resolved)):
        rel = relpath(root, file_path)
        if any(part in DIRECTORY_SKIP_NAMES for part in file_path.relative_to(resolved).parts[:-1]):
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


def gitignored_workspace_paths(root: Path) -> frozenset[str]:
    """Return git-ignored paths under root as posix relpaths (dirs end with /).

    Uses one `git ls-files` invocation so the read-only snapshot can exclude
    every ignored path, not just statically known junk names. Returns an
    empty set when git is unavailable, root is not a work tree, or the
    query fails: enforcement then falls back to the static skip lists and
    can only over-report, never under-report, source modifications.
    """

    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "--directory",
                "-z",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if completed.returncode != 0:
        return frozenset()
    return frozenset(entry for entry in completed.stdout.split("\0") if entry)


def _is_gitignored(relative_posix: str, ignored_paths: frozenset[str]) -> bool:
    if not ignored_paths:
        return False
    if relative_posix in ignored_paths or f"{relative_posix}/" in ignored_paths:
        return True
    prefix = ""
    for part in relative_posix.split("/")[:-1]:
        prefix = f"{prefix}{part}/"
        if prefix in ignored_paths:
            return True
    return False


def snapshot_workspace(root: Path, *, include_paths: Iterable[str | Path] = ()) -> dict[str, str]:
    """Hash workspace source files, excluding junk, caches, and ignored paths.

    This snapshot feeds read-only reviewer enforcement. It must only see
    real workspace source content: directory caches (DIRECTORY_SKIP_NAMES),
    OS junk and bytecode files (.DS_Store, Thumbs.db, *.pyc, ...), and
    git-ignored paths are all excluded so that incidental churn in them can
    never be classified as a read_only_violation.
    """

    snapshot: dict[str, str] = {}
    ignored_paths = gitignored_workspace_paths(root)

    def walk(path: Path) -> Iterable[Path]:
        try:
            children = sorted(path.iterdir())
        except FileNotFoundError:
            return
        for child in children:
            try:
                relative_parts = child.relative_to(root).parts
                if any(part in DIRECTORY_SKIP_NAMES for part in relative_parts):
                    continue
                if is_skippable_junk_file(relative_parts[-1]):
                    continue
                if _is_gitignored("/".join(relative_parts), ignored_paths):
                    continue
                if child.is_dir():
                    yield from walk(child)
                elif child.is_file():
                    yield child
            except FileNotFoundError:
                continue

    for file_path in walk(root):
        if not file_path.is_file():
            continue
        snapshot[file_path.relative_to(root).as_posix()] = sha256_file(file_path)

    # Explicit review inputs are evidence even when they live in ignored
    # runner directories such as .local. Include only declared targets so
    # unrelated runtime churn cannot create false read-only failures.
    for raw_path in include_paths:
        explicit = resolve_under_root(root, raw_path, must_exist=False)
        if not explicit.exists():
            continue
        candidates = [explicit] if explicit.is_file() else sorted(path for path in explicit.rglob("*") if path.is_file())
        for file_path in candidates:
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


def _stage_dir_from_run(
    root: Path,
    run_dir: Path,
    stage_id: str,
    stage_summary: dict[str, Any],
) -> Path | None:
    """Resolve the immutable current attempt, with the v1 stage root as fallback."""

    current_attempt_id = stage_summary.get("current_attempt_id")
    for attempt in stage_summary.get("attempts", []):
        if not isinstance(attempt, dict) or attempt.get("attempt_id") != current_attempt_id:
            continue
        attempt_dir = attempt.get("attempt_dir")
        if isinstance(attempt_dir, str) and attempt_dir:
            return resolve_under_root(root, attempt_dir, must_exist=False)
    stages = sorted((run_dir / "stages").glob(f"*_{stage_id}"))
    return stages[0] if stages else None


def compute_request_evidence(root: Path, run_dir: str | Path, stage_id: str) -> dict[str, Any]:
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    run_manifest_path = resolved_run_dir / "run_manifest.json"
    run_manifest = _safe_load_json(run_manifest_path) or {}
    evidence_files: list[dict[str, Any]] = []
    if run_manifest_path.exists():
        evidence_files.append(artifact_record(root, run_manifest_path, "run_manifest"))

    stage_summary = {}
    for item in run_manifest.get("stages", []) if isinstance(run_manifest.get("stages"), list) else []:
        if isinstance(item, dict) and item.get("stage_id") == stage_id:
            stage_summary = item
            break
    stage_dir = _stage_dir_from_run(root, resolved_run_dir, stage_id, stage_summary)

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
        for key in ("model", "reasoning", "text", "max_output_tokens", "prompt_cache_options", "prompt_cache_retention", "tools", "tool_choice", "max_tool_calls", "parallel_tool_calls", "service_tier"):
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
