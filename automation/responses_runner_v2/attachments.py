from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .contracts import (
    CODE_FENCE_LANGUAGE_BY_SUFFIX,
    DIRECTORY_SKIP_NAMES,
    FIELD_TO_ROLE,
    MAX_REQUEST_ATTACHMENT_BYTES,
    MAX_SINGLE_FILE_BYTES,
    RESPONSES_CONTEXT_SUPPORTED_SUFFIXES,
    ROLE_TO_FIELD,
    AttachmentEntry,
    relpath,
    repo_root,
    resolve_under_root,
    runner_now,
    sha256_file,
    write_text,
)


def is_probably_utf8_text(path: Path) -> bool:
    sample = path.read_bytes()[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def needs_context_wrapper(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in RESPONSES_CONTEXT_SUPPORTED_SUFFIXES:
        return False
    return is_probably_utf8_text(path)


def build_context_wrapper(root: Path, source_path: Path, staging_dir: Path) -> Path:
    rel = relpath(root, source_path)
    language = CODE_FENCE_LANGUAGE_BY_SUFFIX.get(source_path.suffix.lower(), "")
    fence = f"```{language}" if language else "```"
    wrapped_path = staging_dir / (rel.replace("/", "__") + ".md")
    body = source_path.read_text(encoding="utf-8", errors="replace")
    wrapped = "\n".join(
        [
            "# Wrapped Source Artifact",
            "",
            f"source_path: {rel}",
            "",
            fence,
            body,
            "```",
            "",
        ]
    )
    write_text(wrapped_path, wrapped)
    return wrapped_path


def matches_exclude_globs(relative_path: str, exclude_globs: tuple[str, ...]) -> bool:
    rel = Path(relative_path)
    return any(rel.match(pattern) for pattern in exclude_globs)


def expand_attachment_target(
    root: Path,
    target: Path,
    *,
    exclude_globs: tuple[str, ...],
) -> list[Path]:
    if not target.exists():
        raise SystemExit(f"Attachment path does not exist: {target}")
    if target.is_file():
        rel = relpath(root, target)
        return [] if matches_exclude_globs(rel, exclude_globs) else [target]
    if not target.is_dir():
        raise SystemExit(f"Attachment path must be a file or directory: {target}")

    results: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(target):
        current_dir = Path(dirpath)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in DIRECTORY_SKIP_NAMES
            and not matches_exclude_globs(
                relpath(root, current_dir / name),
                exclude_globs,
            )
        ]
        for filename in sorted(filenames):
            if filename == ".DS_Store":
                continue
            file_path = current_dir / filename
            rel = relpath(root, file_path)
            if matches_exclude_globs(rel, exclude_globs):
                continue
            results.append(file_path)
    return results


def _resolve_entry(
    root: Path,
    entry: AttachmentEntry,
) -> dict[str, Any]:
    target = resolve_under_root(root, entry.path, must_exist=True)
    if entry.kind == "file" and not target.is_file():
        raise SystemExit(f"Attachment entry expects a file: {entry.path}")
    if entry.kind == "directory" and not target.is_dir():
        raise SystemExit(f"Attachment entry expects a directory: {entry.path}")
    expanded_paths = expand_attachment_target(root, target, exclude_globs=entry.exclude_globs)
    if entry.required and not expanded_paths:
        raise SystemExit(f"Attachment entry resolved to no files: {entry.path}")
    resolved_paths: list[dict[str, Any]] = []
    aggregate_bytes = 0
    for file_path in expanded_paths:
        size = file_path.stat().st_size
        if size > MAX_SINGLE_FILE_BYTES:
            raise SystemExit(f"Attachment exceeds 50MB limit: {file_path}")
        aggregate_bytes += size
        resolved_paths.append(
            {
                "path": relpath(root, file_path),
                "sha256": sha256_file(file_path),
                "bytes": size,
                "wrapped_as_markdown": needs_context_wrapper(file_path),
            }
        )
    return {
        "path": entry.path,
        "kind": entry.kind,
        "required": entry.required,
        "exclude_globs": list(entry.exclude_globs),
        **({"notes": entry.notes} if entry.notes else {}),
        "resolved": {
            "expanded_paths": resolved_paths,
            "aggregate_file_count": len(resolved_paths),
            "aggregate_bytes": aggregate_bytes,
        },
    }


def resolve_stage_input_manifest(
    *,
    root: Path | None,
    workflow_id: str,
    stage_id: str,
    run_id: str,
    manifest_id: str,
    description: str | None,
    primary_job_inputs: list[AttachmentEntry],
    reviewed_handoff_inputs: list[AttachmentEntry],
    attached_repository_files: list[AttachmentEntry],
    reference_context: list[AttachmentEntry],
) -> dict[str, Any]:
    root = root or repo_root()
    resolved = {
        "schema_version": "responses_runner_v2.input_manifest.v1",
        "manifest_id": manifest_id,
        "workflow_id": workflow_id,
        "stage_id": stage_id,
        "run_id": run_id,
        "generated_at": runner_now().isoformat(),
        "description": description or "",
        "primary_job_inputs": [_resolve_entry(root, entry) for entry in primary_job_inputs],
        "reviewed_handoff_inputs": [_resolve_entry(root, entry) for entry in reviewed_handoff_inputs],
        "attached_repository_files": [
            _resolve_entry(root, entry) for entry in attached_repository_files
        ],
        "reference_context": [_resolve_entry(root, entry) for entry in reference_context],
    }
    total_bytes = 0
    for field_name in ROLE_TO_FIELD.values():
        for entry in resolved[field_name]:
            total_bytes += int(entry["resolved"]["aggregate_bytes"])
    if total_bytes > MAX_REQUEST_ATTACHMENT_BYTES:
        raise SystemExit(
            f"Combined attachment size exceeds 50MB request limit: {total_bytes} bytes."
        )
    return resolved


def render_input_manifest_markdown(resolved_manifest: dict[str, Any]) -> str:
    lines = [
        "# Responses Runner V2 Stage Input Manifest",
        "",
        f"- schema_version: {resolved_manifest['schema_version']}",
        f"- manifest_id: {resolved_manifest['manifest_id']}",
        f"- workflow_id: {resolved_manifest.get('workflow_id')}",
        f"- stage_id: {resolved_manifest.get('stage_id')}",
        f"- run_id: {resolved_manifest.get('run_id')}",
        f"- generated_at: {resolved_manifest.get('generated_at')}",
        "",
    ]
    description = str(resolved_manifest.get("description") or "").strip()
    if description:
        lines.extend(["## Description", "", description, ""])
    for field_name in ROLE_TO_FIELD.values():
        lines.append(f"## {FIELD_TO_ROLE[field_name]}")
        entries = resolved_manifest.get(field_name, [])
        if not entries:
            lines.extend(["None.", ""])
            continue
        for index, entry in enumerate(entries, start=1):
            lines.append(f"{index:02d}. {entry['path']} ({entry['kind']})")
            if entry.get("notes"):
                lines.append(f"    - notes: {entry['notes']}")
            resolved = entry.get("resolved", {})
            lines.append(
                f"    - aggregate_file_count: {resolved.get('aggregate_file_count', 0)}"
            )
            lines.append(f"    - aggregate_bytes: {resolved.get('aggregate_bytes', 0)}")
            for expanded in resolved.get("expanded_paths", []):
                wrapped_note = " [wrapped as markdown at upload]" if expanded.get(
                    "wrapped_as_markdown"
                ) else ""
                lines.append(
                    f"      - {expanded['path']} ({expanded['bytes']} bytes, sha256={expanded['sha256'][:12]}...){wrapped_note}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def prepare_upload_plan(
    *,
    root: Path | None,
    resolved_manifest: dict[str, Any],
    input_manifest_markdown_path: Path,
    staging_dir: Path,
) -> list[dict[str, Any]]:
    root = root or repo_root()
    prepared = [
        {
            "role_label": "Stage Input Manifest",
            "field_name": None,
            "attachment_index": None,
            "expanded_index": None,
            "display_name": "input_manifest.md",
            "source_path": input_manifest_markdown_path,
            "upload_path": input_manifest_markdown_path,
            "wrapped_as_markdown": False,
        }
    ]
    for field_name in ROLE_TO_FIELD.values():
        role_label = FIELD_TO_ROLE[field_name]
        for attachment_index, entry in enumerate(resolved_manifest.get(field_name, [])):
            for expanded_index, expanded in enumerate(entry["resolved"]["expanded_paths"]):
                source_path = resolve_under_root(root, expanded["path"], must_exist=True)
                upload_path = source_path
                if expanded.get("wrapped_as_markdown"):
                    upload_path = build_context_wrapper(root, source_path, staging_dir)
                prepared.append(
                    {
                        "role_label": role_label,
                        "field_name": field_name,
                        "attachment_index": attachment_index,
                        "expanded_index": expanded_index,
                        "display_name": expanded["path"],
                        "source_path": source_path,
                        "upload_path": upload_path,
                        "wrapped_as_markdown": bool(expanded.get("wrapped_as_markdown")),
                    }
                )
    return prepared


def upload_prepared_attachments(
    *,
    root: Path | None,
    client: Any,
    resolved_manifest: dict[str, Any],
    prepared_uploads: list[dict[str, Any]],
    purpose: str,
    file_expiration_policy: dict[str, Any] | None,
    delete_uploaded_files_on_complete: bool,
) -> tuple[str, dict[str, list[str]], dict[str, Any], dict[str, Any]]:
    root = root or repo_root()
    manifest_file_id = ""
    role_to_file_ids: dict[str, list[str]] = {}
    uploads_payload = {
        "delete_uploaded_files_on_complete": delete_uploaded_files_on_complete,
        "file_expiration_policy": file_expiration_policy,
        "files": [],
    }
    for prepared in prepared_uploads:
        response = client.upload_file(
            prepared["upload_path"],
            purpose=purpose,
            file_expiration_policy=file_expiration_policy,
        )
        file_id = str(response["id"])
        role_to_file_ids.setdefault(prepared["role_label"], []).append(file_id)
        if prepared["field_name"] is None:
            manifest_file_id = file_id
        else:
            expanded = resolved_manifest[prepared["field_name"]][prepared["attachment_index"]]["resolved"][
                "expanded_paths"
            ][prepared["expanded_index"]]
            expanded["uploaded_file_id"] = file_id
            expanded["purpose"] = response.get("purpose", purpose)
            if response.get("expires_at") is not None:
                expanded["expires_at"] = int(response["expires_at"])
        uploads_payload["files"].append(
            {
                "attachment_role": prepared["role_label"],
                "display_name": prepared["display_name"],
                "source_path": relpath(root, prepared["source_path"]),
                "upload_filename": prepared["upload_path"].name,
                "wrapped_as_markdown": prepared["wrapped_as_markdown"],
                "bytes": prepared["upload_path"].stat().st_size,
                "file_id": file_id,
                "purpose": response.get("purpose", purpose),
                "created_at": response.get("created_at"),
                "expires_at": response.get("expires_at"),
            }
        )
    if not manifest_file_id:
        raise SystemExit("Failed to upload stage input manifest markdown.")
    return manifest_file_id, role_to_file_ids, uploads_payload, resolved_manifest


def cleanup_uploaded_files(
    *,
    client: Any,
    uploads_payload: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(uploads_payload)
    updated_files: list[dict[str, Any]] = []
    for record in uploads_payload.get("files", []):
        if not isinstance(record, dict):
            continue
        item = dict(record)
        file_id = item.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            updated_files.append(item)
            continue
        if item.get("delete_status") == "deleted":
            updated_files.append(item)
            continue
        try:
            delete_response = client.delete_file(file_id)
            item["delete_status"] = "deleted" if delete_response.get("deleted") else "not_deleted"
            item["delete_response"] = delete_response
        except Exception as exc:  # pragma: no cover - defensive
            item["delete_status"] = "error"
            item["delete_error"] = str(exc)
        updated_files.append(item)
    updated["files"] = updated_files
    return updated


def _append_role_block(
    content: list[dict[str, Any]],
    role_blocks: list[dict[str, Any]],
    *,
    label: str,
    description: str,
    file_ids: list[str],
) -> None:
    if not file_ids:
        return
    content.append({"type": "input_text", "text": f"Attachment role: {label}. {description}"})
    for file_id in file_ids:
        content.append({"type": "input_file", "file_id": file_id})
    role_blocks.append({"role": label, "file_ids": list(file_ids)})


def build_request_input_content(
    *,
    task_text: str,
    input_manifest_file_id: str | None,
    role_to_file_ids: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": task_text}]
    role_blocks: list[dict[str, Any]] = []
    _append_role_block(
        content,
        role_blocks,
        label="Stage Input Manifest",
        description="The next attached file enumerates every repo-local file attached in this stage and its attachment role.",
        file_ids=[input_manifest_file_id] if input_manifest_file_id else [],
    )
    _append_role_block(
        content,
        role_blocks,
        label="Primary Job Inputs",
        description="The next attached files are authoritative task inputs for the immediate target.",
        file_ids=role_to_file_ids.get("Primary Job Inputs", []),
    )
    _append_role_block(
        content,
        role_blocks,
        label="Reviewed Handoff Inputs",
        description="The next attached files are reviewed handoff artifacts from an earlier gated stage.",
        file_ids=role_to_file_ids.get("Reviewed Handoff Inputs", []),
    )
    _append_role_block(
        content,
        role_blocks,
        label="Attached Repository Files",
        description="The next attached files are repository evidence for the current task.",
        file_ids=role_to_file_ids.get("Attached Repository Files", []),
    )
    _append_role_block(
        content,
        role_blocks,
        label="Reference Context",
        description="The next attached files are lower-authority carry-forward or reference context.",
        file_ids=role_to_file_ids.get("Reference Context", []),
    )
    return content, role_blocks
