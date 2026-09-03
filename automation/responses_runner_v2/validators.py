from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .attachments import is_sensitive_filename
from .contracts import relpath, resolve_under_root, sha256_file


VALIDATOR_RESULT_SCHEMA_VERSION = "responses_runner_v2.validator_result.v1"
EVIDENCE_CITATION_RE = re.compile(
    r"\[(workspace_file|repository_path|stage_artifact|operator_input):([^\]\r\n]+)\]"
)
def _violation(rule_id: str, message: str, line: int | None = None) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "message": message,
        "blocking": True,
        **({"line": line} if line is not None else {}),
    }


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _run_dir_for_artifact(artifact_path: Path) -> Path | None:
    for parent in artifact_path.parents:
        if (parent / "run_manifest.json").is_file():
            return parent
    return None


def validate_evidence_references_v1(
    text: str,
    *,
    artifact_path: Path,
    root: Path,
    context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Validate only explicit typed citations against frozen stage evidence."""

    violations: list[dict[str, Any]] = []
    context = context or {}
    policy = context.get("citation_policy")
    allowed_types = set(policy.get("allowed_locator_types", [])) if isinstance(policy, dict) else set()
    if not allowed_types:
        return [_violation("citation.policy", "evidence_references_v1 requires a citation policy")]

    manifest_path_value = context.get("input_manifest_path")
    if not isinstance(manifest_path_value, str) or not manifest_path_value:
        return [_violation("citation.manifest", "resolved input manifest path is required")]
    try:
        manifest_path = resolve_under_root(root, manifest_path_value, must_exist=True)
        manifest = _load_json_object(manifest_path, label="resolved input manifest")
    except (SystemExit, ValueError) as exc:
        return [_violation("citation.manifest", str(exc))]

    manifest_files: dict[str, str] = {}
    for field_name in (
        "primary_job_inputs",
        "reviewed_handoff_inputs",
        "attached_repository_files",
        "reference_context",
    ):
        for entry in manifest.get(field_name, []):
            if not isinstance(entry, dict):
                continue
            resolved = entry.get("resolved")
            if not isinstance(resolved, dict):
                continue
            for expanded in resolved.get("expanded_paths", []):
                if not isinstance(expanded, dict):
                    continue
                path = expanded.get("path")
                digest = expanded.get("sha256")
                if isinstance(path, str) and isinstance(digest, str):
                    manifest_files[path] = digest

    run_dir = _run_dir_for_artifact(artifact_path)
    run_manifest: dict[str, Any] = {}
    if run_dir is not None:
        try:
            run_manifest = _load_json_object(run_dir / "run_manifest.json", label="run manifest")
        except ValueError as exc:
            violations.append(_violation("citation.run_manifest", str(exc)))

    matches = list(EVIDENCE_CITATION_RE.finditer(text))
    if not matches:
        violations.append(_violation("citation.required", "artifact contains no typed evidence citation"))
        return violations

    stage_id = str(manifest.get("stage_id") or "")
    for match in matches:
        locator_type, locator = match.group(1), match.group(2).strip()
        line = text.count("\n", 0, match.start()) + 1
        if locator_type not in allowed_types:
            violations.append(
                _violation(
                    "citation.type_allowed",
                    f"citation type {locator_type!r} is not allowed for this stage",
                    line,
                )
            )
            continue
        if locator_type in {"workspace_file", "repository_path"}:
            expected_hash = manifest_files.get(locator)
            if expected_hash is None:
                violations.append(
                    _violation(
                        "citation.manifest_member",
                        f"cited workspace path is not an attached manifest file: {locator}",
                        line,
                    )
                )
                continue
            try:
                cited_path = resolve_under_root(root, locator, must_exist=True)
            except SystemExit as exc:
                violations.append(_violation("citation.path", str(exc), line))
                continue
            if sha256_file(cited_path) != expected_hash:
                violations.append(
                    _violation("citation.hash", f"cited workspace file hash drifted: {locator}", line)
                )
        elif locator_type == "stage_artifact":
            summary = next(
                (
                    item
                    for item in run_manifest.get("stages", [])
                    if isinstance(item, dict) and item.get("stage_id") == locator
                ),
                None,
            )
            if summary is None or locator == stage_id:
                violations.append(
                    _violation(
                        "citation.stage_artifact",
                        f"cited prior-stage artifact is not recorded: {locator}",
                        line,
                    )
                )
                continue
            artifact_rel = summary.get("artifact_markdown_path")
            artifact_hash = summary.get("artifact_markdown_sha256")
            if not isinstance(artifact_rel, str) or not isinstance(artifact_hash, str):
                violations.append(
                    _violation(
                        "citation.stage_artifact",
                        f"cited stage has no hash-bound clean artifact: {locator}",
                        line,
                    )
                )
                continue
            try:
                cited_path = resolve_under_root(root, artifact_rel, must_exist=True)
            except SystemExit as exc:
                violations.append(_violation("citation.stage_artifact", str(exc), line))
                continue
            if sha256_file(cited_path) != artifact_hash:
                violations.append(
                    _violation(
                        "citation.hash",
                        f"cited stage artifact hash drifted: {locator}",
                        line,
                    )
                )
        elif locator_type == "operator_input":
            bindings = run_manifest.get("operator_overrides", {}).get("input_bindings", [])
            binding = next(
                (
                    item
                    for item in bindings
                    if isinstance(item, dict) and item.get("binding_id") == locator
                ),
                None,
            )
            scoped = binding and (
                not binding.get("stage_ids") or stage_id in binding.get("stage_ids", [])
            )
            if not scoped or binding.get("path") not in manifest_files:
                violations.append(
                    _violation(
                        "citation.operator_input",
                        f"operator input is not bound to this stage: {locator}",
                        line,
                    )
                )
    return violations


Validator = Callable[[str], list[dict[str, Any]]]
VALIDATOR_REGISTRY: dict[str, Callable[..., list[dict[str, Any]]]] = {
    "evidence_references_v1": validate_evidence_references_v1,
}


def run_validator(
    validator_id: str,
    artifact_path: Path,
    *,
    root: Path,
    timeout_seconds: float = 5.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one trusted in-process validator and return a hash-bound result."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    try:
        validator = VALIDATOR_REGISTRY[validator_id]
    except KeyError as exc:
        raise ValueError(f"Unknown trusted validator: {validator_id}") from exc
    started = time.monotonic()
    text = artifact_path.read_text(encoding="utf-8")
    if validator_id == "evidence_references_v1":
        violations = validator(
            text,
            artifact_path=artifact_path,
            root=root,
            context=context,
        )
    else:
        violations = validator(text)
    duration_ms = int((time.monotonic() - started) * 1000)
    if duration_ms > timeout_seconds * 1000:
        violations.append(_violation("validator.timeout", "validator exceeded its trusted timeout"))
    return {
        "schema_version": VALIDATOR_RESULT_SCHEMA_VERSION,
        "validator_id": validator_id,
        "artifact": {
            "path": relpath(root, artifact_path),
            "sha256": sha256_file(artifact_path),
        },
        "passed": not violations,
        "duration_ms": duration_ms,
        "violations": violations,
    }
