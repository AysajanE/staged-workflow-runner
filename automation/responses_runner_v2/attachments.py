from __future__ import annotations

import copy
import functools
import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

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

MAX_RESPONSE_INPUT_FILES = 100
TEXT_CLASSIFICATION_SAMPLE_BYTES = 4096
STREAM_CHUNK_CHARACTERS = 1024 * 1024
MAX_WORKSPACE_INVENTORY_ENTRIES = 2000
BUNDLE_ROLE_PRIORITY = (
    "Attached Repository Files",
    "Reference Context",
    "Reviewed Handoff Inputs",
    "Primary Job Inputs",
)

SENSITIVE_EXACT_NAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "secrets.json",
}
SENSITIVE_SUFFIXES = (".jks", ".key", ".keystore", ".p12", ".pem", ".pfx")


def is_sensitive_filename(name: str) -> bool:
    """Return whether a filename is unsafe to include without an audited override."""

    lowered = name.casefold()
    return (
        lowered in SENSITIVE_EXACT_NAMES
        or lowered.startswith(".env.")
        or (lowered.startswith("service-account") and lowered.endswith(".json"))
        or lowered.endswith(SENSITIVE_SUFFIXES)
    )


def _require_safe_attachment_path(root: Path, path: Path) -> Path:
    """Resolve an input and fail closed on root escapes and sensitive names."""

    root_resolved = root.resolve()
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise SystemExit(f"Attachment path cannot be resolved safely: {path}") from exc
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SystemExit(f"Attachment path escapes workspace root: {path}") from exc
    if is_sensitive_filename(path.name) or is_sensitive_filename(resolved.name):
        raise SystemExit(f"Sensitive attachment filename is not allowed: {path}")
    return resolved


def is_probably_utf8_text(path: Path) -> bool:
    with path.open("rb") as handle:
        sample = handle.read(TEXT_CLASSIFICATION_SAMPLE_BYTES)
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


def _safe_markdown_fence(longest_run: int, language: str = "") -> tuple[str, str]:
    marker = "`" * max(3, longest_run + 1)
    return f"{marker}{language}" if language else marker, marker


def _content_addressed_staging_name(relative_path: str, content_hash: str, name: str) -> str:
    identity = f"{relative_path}\0{content_hash}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "source"
    return f"{safe_name}.{digest}.md"


def _scan_source(path: Path) -> tuple[str, int]:
    """Return the byte hash and longest backtick run without a full-file read."""

    digest = hashlib.sha256()
    longest_run = 0
    trailing_run = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(STREAM_CHUNK_CHARACTERS), b""):
            digest.update(chunk)
            first_match = None
            last_match = None
            for match in re.finditer(rb"`+", chunk):
                if first_match is None:
                    first_match = match
                last_match = match
                longest_run = max(longest_run, len(match.group(0)))
            if first_match is None or last_match is None:
                trailing_run = 0
                continue
            if first_match.start() == 0 and trailing_run:
                longest_run = max(longest_run, trailing_run + len(first_match.group(0)))
            if last_match.end() == len(chunk):
                trailing_run = (
                    trailing_run + len(chunk)
                    if last_match.start() == 0
                    else len(last_match.group(0))
                )
                longest_run = max(longest_run, trailing_run)
            else:
                trailing_run = 0
    return digest.hexdigest(), longest_run


def _copy_source_text(source_path: Path, destination: Any) -> None:
    with source_path.open("r", encoding="utf-8", errors="replace") as source:
        for chunk in iter(lambda: source.read(STREAM_CHUNK_CHARACTERS), ""):
            destination.write(chunk)


def _streamed_temp_file(staging_dir: Path) -> tuple[Path, Any]:
    staging_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(staging_dir, 0o700)
    fd, temporary_name = tempfile.mkstemp(dir=staging_dir, prefix=".attachment.", suffix=".tmp")
    os.fchmod(fd, 0o600)
    return Path(temporary_name), os.fdopen(fd, "w", encoding="utf-8")


def _publish_streamed_file(temporary_path: Path, target_path: Path) -> Path:
    os.replace(temporary_path, target_path)
    os.chmod(target_path, 0o600)
    directory_fd = os.open(target_path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return target_path


def build_context_wrapper(root: Path, source_path: Path, staging_dir: Path) -> Path:
    staging_dir = resolve_under_root(root, staging_dir)
    rel = relpath(root, source_path)
    language = CODE_FENCE_LANGUAGE_BY_SUFFIX.get(source_path.suffix.lower(), "")
    content_hash, longest_run = _scan_source(source_path)
    opening_fence, closing_fence = _safe_markdown_fence(longest_run, language)
    wrapped_path = staging_dir / _content_addressed_staging_name(
        rel,
        content_hash,
        source_path.name,
    )
    temporary_path, handle = _streamed_temp_file(staging_dir)
    try:
        with handle:
            handle.write(
                "\n".join(
                    [
                        "# Wrapped Source Artifact",
                        "",
                        f"source_path: {rel}",
                        "",
                        opening_fence,
                        "",
                    ]
                )
            )
            _copy_source_text(source_path, handle)
            handle.write(f"\n{closing_fence}\n")
            handle.flush()
            os.fsync(handle.fileno())
        return _publish_streamed_file(temporary_path, wrapped_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _safe_bundle_name(role_label: str, content_hash: str) -> str:
    role = re.sub(r"[^a-z0-9._-]+", "_", role_label.casefold()).strip("_.") or "role"
    return f"{role}.{content_hash}.attachment_bundle.md"


def build_attachment_bundle(
    *,
    root: Path,
    role_label: str,
    bundle_items: list[dict[str, Any]],
    staging_dir: Path,
) -> Path:
    staging_dir = resolve_under_root(root, staging_dir)
    temporary_path, handle = _streamed_temp_file(staging_dir)
    try:
        with handle:
            handle.write(
                "\n".join(
                    [
                        f"# Attachment Role Bundle: {role_label}",
                        "",
                        "This deterministic bundle preserves repo-relative source paths for a large attachment role.",
                        "Cite only repo-relative source paths listed in input_manifest.md.",
                        "",
                    ]
                )
                + "\n"
            )
            for index, item in enumerate(bundle_items, start=1):
                source_path = item["source_path"]
                rel = relpath(root, source_path)
                source_hash, longest_run = _scan_source(source_path)
                language = CODE_FENCE_LANGUAGE_BY_SUFFIX.get(source_path.suffix.lower(), "")
                opening_fence, closing_fence = _safe_markdown_fence(longest_run, language)
                handle.write(
                    "\n".join(
                        [
                            f"## File {index:03d}: {rel}",
                            "",
                            f"- source_path: {rel}",
                            f"- sha256: {source_hash}",
                            f"- bytes: {source_path.stat().st_size}",
                            f"- originally_wrapped_as_markdown: {str(bool(item.get('wrapped_as_markdown'))).lower()}",
                            "",
                            opening_fence,
                            "",
                        ]
                    )
                )
                _copy_source_text(source_path, handle)
                handle.write(f"\n{closing_fence}")
                handle.write("\n\n" if index < len(bundle_items) else "\n")
            handle.flush()
            os.fsync(handle.fileno())
        content_hash = sha256_file(temporary_path)
        bundle_path = staging_dir / _safe_bundle_name(role_label, content_hash)
        _publish_streamed_file(temporary_path, bundle_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    if bundle_path.stat().st_size > MAX_SINGLE_FILE_BYTES:
        raise SystemExit(f"Attachment role bundle exceeds 50MB limit: {bundle_path}")
    return bundle_path


def matches_exclude_globs(relative_path: str, exclude_globs: tuple[str, ...]) -> bool:
    rel = Path(relative_path)
    return any(rel.match(pattern) for pattern in exclude_globs)


@functools.lru_cache(maxsize=8)
def _git_ignored_entries(root: Path) -> tuple[str, ...]:
    """Return ignored paths once per workspace; non-Git roots simply return none."""

    if not (root / ".git").exists():
        return ()
    ignored: set[str] = set()
    commands = (
        ("--others", "--ignored", "--exclude-standard", "--directory"),
        ("--cached", "--ignored", "--exclude-standard"),
    )
    for flags in commands:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", *flags],
            check=False,
            capture_output=True,
        )
        if completed.returncode != 0:
            return ()
        ignored.update(
            item.decode("utf-8", errors="surrogateescape").rstrip("/")
            for item in completed.stdout.split(b"\0")
            if item
        )
    return tuple(sorted(ignored))


def _is_git_ignored(relative_path: str, ignored_entries: tuple[str, ...]) -> bool:
    return any(
        relative_path == ignored or relative_path.startswith(f"{ignored}/")
        for ignored in ignored_entries
    )


def expand_attachment_target(
    root: Path,
    target: Path,
    *,
    exclude_globs: tuple[str, ...],
) -> list[Path]:
    target = _require_safe_attachment_path(root, target)
    if not target.exists():
        raise SystemExit(f"Attachment path does not exist: {target}")
    if target.is_file():
        _require_safe_attachment_path(root, target)
        rel = relpath(root, target)
        return [] if matches_exclude_globs(rel, exclude_globs) else [target]
    if not target.is_dir():
        raise SystemExit(f"Attachment path must be a file or directory: {target}")

    results: list[Path] = []
    ignored_entries = _git_ignored_entries(root.resolve())
    for dirpath, dirnames, filenames in os.walk(target):
        current_dir = Path(dirpath)
        _require_safe_attachment_path(root, current_dir)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in DIRECTORY_SKIP_NAMES
            and not matches_exclude_globs(
                relpath(root, current_dir / name),
                exclude_globs,
            )
            and not _is_git_ignored(
                relpath(root, current_dir / name),
                ignored_entries,
            )
        ]
        for name in dirnames:
            _require_safe_attachment_path(root, current_dir / name)
        for filename in sorted(filenames):
            if filename == ".DS_Store":
                continue
            file_path = current_dir / filename
            rel = relpath(root, file_path)
            if is_sensitive_filename(filename):
                raise SystemExit(f"Sensitive attachment filename is not allowed: {file_path}")
            if matches_exclude_globs(rel, exclude_globs) or _is_git_ignored(
                rel, ignored_entries
            ):
                continue
            _require_safe_attachment_path(root, file_path)
            results.append(file_path)
    return results


def _workspace_inventory(
    root: Path,
    target: Path,
    *,
    exclude_globs: tuple[str, ...],
) -> dict[str, Any]:
    """Build a bounded, deterministic metadata-only workspace projection."""

    target = _require_safe_attachment_path(root, target)
    if not target.is_dir():
        raise SystemExit(f"workspace_inventory expects a directory: {target}")
    ignored_entries = _git_ignored_entries(root.resolve())
    entries: list[dict[str, Any]] = []
    omitted_sensitive = 0
    truncated = False
    for dirpath, dirnames, filenames in os.walk(target):
        current_dir = Path(dirpath)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in DIRECTORY_SKIP_NAMES
            and not matches_exclude_globs(relpath(root, current_dir / name), exclude_globs)
            and not _is_git_ignored(relpath(root, current_dir / name), ignored_entries)
        ]
        for filename in sorted(filenames):
            file_path = current_dir / filename
            relative_path = relpath(root, file_path)
            if (
                filename == ".DS_Store"
                or matches_exclude_globs(relative_path, exclude_globs)
                or _is_git_ignored(relative_path, ignored_entries)
            ):
                continue
            if is_sensitive_filename(filename):
                omitted_sensitive += 1
                continue
            safe_path = _require_safe_attachment_path(root, file_path)
            entries.append({"path": relative_path, "bytes": safe_path.stat().st_size})
            if len(entries) >= MAX_WORKSPACE_INVENTORY_ENTRIES:
                truncated = True
                break
        if truncated:
            break
    return {
        "inventory_entries": entries,
        "inventory_entry_count": len(entries),
        "inventory_truncated": truncated,
        "sensitive_entries_omitted": omitted_sensitive,
    }


def _render_workspace_inventory(path: str, resolved: dict[str, Any]) -> str:
    lines = [
        "# Workspace Inventory",
        "",
        f"- root: {path}",
        f"- entries: {resolved['inventory_entry_count']}",
        f"- truncated: {str(bool(resolved['inventory_truncated'])).lower()}",
        f"- sensitive_entries_omitted: {resolved['sensitive_entries_omitted']}",
        "",
        "## Files",
        "",
    ]
    lines.extend(
        f"- {item['path']} ({item['bytes']} bytes)"
        for item in resolved["inventory_entries"]
    )
    return "\n".join(lines).rstrip() + "\n"


def detect_authority_duplicates(resolved_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return stable cross-authority path/content duplicates from a resolved manifest."""

    records: list[dict[str, str]] = []
    for field_name in ROLE_TO_FIELD.values():
        role = FIELD_TO_ROLE[field_name]
        for entry in resolved_manifest.get(field_name, []):
            for expanded in entry.get("resolved", {}).get("expanded_paths", []):
                path = expanded.get("path")
                digest = expanded.get("sha256")
                if isinstance(path, str) and isinstance(digest, str):
                    records.append({"authority": role, "path": path, "sha256": digest})

    duplicates: list[dict[str, Any]] = []
    seen_groups: set[tuple[str, tuple[tuple[str, str, str], ...]]] = set()
    for duplicate_by, key_name in (("path", "path"), ("content_hash", "sha256")):
        grouped: dict[str, list[dict[str, str]]] = {}
        for record in records:
            grouped.setdefault(record[key_name], []).append(record)
        for value, matches in sorted(grouped.items()):
            authorities = {match["authority"] for match in matches}
            if len(authorities) < 2:
                continue
            normalized = tuple(
                sorted((match["authority"], match["path"], match["sha256"]) for match in matches)
            )
            identity = (duplicate_by, normalized)
            if identity in seen_groups:
                continue
            seen_groups.add(identity)
            duplicates.append(
                {
                    "duplicate_by": duplicate_by,
                    "value": value,
                    "authorities": sorted(authorities),
                    "occurrences": [
                        {"authority": authority, "path": path, "sha256": digest}
                        for authority, path, digest in normalized
                    ],
                }
            )
    return duplicates


def _resolve_entry(
    root: Path,
    entry: AttachmentEntry,
) -> dict[str, Any]:
    if is_sensitive_filename(Path(entry.path).name):
        raise SystemExit(f"Sensitive attachment filename is not allowed: {entry.path}")
    target = resolve_under_root(root, entry.path, must_exist=True)
    if entry.kind == "file" and not target.is_file():
        raise SystemExit(f"Attachment entry expects a file: {entry.path}")
    if entry.kind == "directory" and not target.is_dir():
        raise SystemExit(f"Attachment entry expects a directory: {entry.path}")
    if entry.kind == "workspace_inventory":
        inventory = _workspace_inventory(
            root,
            target,
            exclude_globs=entry.exclude_globs,
        )
        inventory_text = _render_workspace_inventory(entry.path, inventory)
        return {
            "path": entry.path,
            "kind": entry.kind,
            "required": entry.required,
            "exclude_globs": list(entry.exclude_globs),
            **({"notes": entry.notes} if entry.notes else {}),
            "resolved": {
                "expanded_paths": [],
                "aggregate_file_count": 1,
                "aggregate_bytes": len(inventory_text.encode("utf-8")),
                **inventory,
            },
        }
    if entry.kind not in {"file", "directory"}:
        raise SystemExit(f"Unknown attachment kind: {entry.kind}")
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
    resolved: dict[str, Any] = {
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
    manifest_upload: dict[str, Any] = {
        "role_label": "Stage Input Manifest",
        "field_name": None,
        "attachment_index": None,
        "expanded_index": None,
        "display_name": "input_manifest.md",
        "source_path": input_manifest_markdown_path,
        "upload_path": input_manifest_markdown_path,
        "wrapped_as_markdown": False,
    }
    role_uploads: dict[str, list[dict[str, Any]]] = {role: [] for role in ROLE_TO_FIELD}
    for field_name in ROLE_TO_FIELD.values():
        role_label = FIELD_TO_ROLE[field_name]
        for attachment_index, entry in enumerate(resolved_manifest.get(field_name, [])):
            if entry.get("kind") == "workspace_inventory":
                content = _render_workspace_inventory(entry["path"], entry["resolved"])
                inventory_path = staging_dir / (
                    "workspace_inventory."
                    + hashlib.sha256(content.encode("utf-8")).hexdigest()
                    + ".md"
                )
                write_text(inventory_path, content)
                role_uploads[role_label].append(
                    {
                        "role_label": role_label,
                        "field_name": None,
                        "attachment_index": attachment_index,
                        "expanded_index": None,
                        "display_name": f"workspace inventory for {entry['path']}",
                        "source_path_display": f"workspace_inventory:{entry['path']}",
                        "source_path": inventory_path,
                        "upload_path": inventory_path,
                        "wrapped_as_markdown": False,
                        "generated_kind": "workspace_inventory",
                    }
                )
                continue
            for expanded_index, expanded in enumerate(entry["resolved"]["expanded_paths"]):
                source_path = resolve_under_root(root, expanded["path"], must_exist=True)
                upload_path = source_path
                if expanded.get("wrapped_as_markdown"):
                    upload_path = build_context_wrapper(root, source_path, staging_dir)
                role_uploads[role_label].append(
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

    direct_count = 1 + sum(len(items) for items in role_uploads.values())
    bundled_roles: set[str] = set()
    text_classification: dict[Path, bool] = {}

    def is_bundleable(item: dict[str, Any]) -> bool:
        source_path = item["source_path"]
        if source_path not in text_classification:
            text_classification[source_path] = is_probably_utf8_text(source_path)
        return text_classification[source_path]

    if direct_count > MAX_RESPONSE_INPUT_FILES:
        for role_label in BUNDLE_ROLE_PRIORITY:
            items = role_uploads.get(role_label, [])
            bundleable = [item for item in items if is_bundleable(item)]
            if len(bundleable) <= 1:
                continue
            direct_count = direct_count - len(bundleable) + 1
            bundled_roles.add(role_label)
            if direct_count <= MAX_RESPONSE_INPUT_FILES:
                break
    if direct_count > MAX_RESPONSE_INPUT_FILES:
        raise SystemExit(
            f"Stage would attach {direct_count} response input files after bundling; "
            f"maximum supported is {MAX_RESPONSE_INPUT_FILES}. Reduce input manifest scope."
        )

    prepared: list[dict[str, Any]] = [manifest_upload]
    for role_label in ROLE_TO_FIELD:
        items = role_uploads[role_label]
        if role_label not in bundled_roles:
            prepared.extend(items)
            continue
        bundleable = [item for item in items if is_bundleable(item)]
        direct_items = [item for item in items if not is_bundleable(item)]
        prepared.extend(direct_items)
        if bundleable:
            bundle_path = build_attachment_bundle(
                root=root,
                role_label=role_label,
                bundle_items=bundleable,
                staging_dir=staging_dir,
            )
            prepared.append(
                {
                    "role_label": role_label,
                    "field_name": None,
                    "attachment_index": None,
                    "expanded_index": None,
                    "display_name": f"{role_label} attachment bundle",
                    "source_path_display": f"generated_bundle:{role_label}",
                    "source_path": bundle_path,
                    "upload_path": bundle_path,
                    "wrapped_as_markdown": False,
                    "bundle_items": bundleable,
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
    journal_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str, dict[str, list[str]], dict[str, Any], dict[str, Any]]:
    root = root or repo_root()
    manifest_file_id = ""
    role_to_file_ids: dict[str, list[str]] = {}
    uploads_payload: dict[str, Any] = {
        "delete_uploaded_files_on_complete": delete_uploaded_files_on_complete,
        "file_expiration_policy": file_expiration_policy,
        "files": [],
    }

    def persist_journal() -> None:
        if journal_callback is not None:
            journal_callback(copy.deepcopy(uploads_payload))

    for prepared in prepared_uploads:
        expected_sources: list[tuple[Path, str]] = []
        if prepared.get("bundle_items"):
            for bundle_item in prepared["bundle_items"]:
                expanded = resolved_manifest[bundle_item["field_name"]][
                    bundle_item["attachment_index"]
                ]["resolved"]["expanded_paths"][bundle_item["expanded_index"]]
                expected_sources.append((bundle_item["source_path"], str(expanded["sha256"])))
        elif prepared["field_name"] is not None:
            expanded = resolved_manifest[prepared["field_name"]][prepared["attachment_index"]][
                "resolved"
            ]["expanded_paths"][prepared["expanded_index"]]
            expected_sources.append((prepared["source_path"], str(expanded["sha256"])))

        for source_path, expected_sha256 in expected_sources:
            actual_sha256 = sha256_file(source_path)
            if actual_sha256 != expected_sha256:
                raise SystemExit(
                    f"Attachment changed after manifest resolution: {relpath(root, source_path)}"
                )

        upload_path = _require_safe_attachment_path(root, prepared["upload_path"])
        upload_sha256 = sha256_file(upload_path)
        journal_record: dict[str, Any] = {
            "attachment_role": prepared["role_label"],
            "display_name": prepared["display_name"],
            "source_path": prepared.get("source_path_display")
            or relpath(root, prepared["source_path"]),
            "upload_filename": upload_path.name,
            "wrapped_as_markdown": prepared["wrapped_as_markdown"],
            "bytes": upload_path.stat().st_size,
            "upload_sha256": upload_sha256,
            "status": "uploading",
            **(
                {
                    "bundled_file_count": len(prepared["bundle_items"]),
                    "bundled_source_paths": [
                        relpath(root, item["source_path"]) for item in prepared["bundle_items"]
                    ],
                }
                if prepared.get("bundle_items")
                else {}
            ),
        }
        uploads_payload["files"].append(journal_record)
        persist_journal()
        try:
            response = client.upload_file(
                upload_path,
                purpose=purpose,
                file_expiration_policy=file_expiration_policy,
            )
            raw_file_id = response.get("id") if isinstance(response, dict) else None
            if not isinstance(raw_file_id, str) or not raw_file_id:
                raise ValueError("File upload response did not include a non-empty id")
            file_id = raw_file_id
        except Exception as exc:
            journal_record["status"] = "upload_outcome_unknown"
            journal_record["error_type"] = type(exc).__name__
            journal_record["error"] = str(exc)
            persist_journal()
            raise

        post_upload_sha256 = sha256_file(upload_path)
        if post_upload_sha256 != upload_sha256:
            journal_record.update(
                {
                    "status": "upload_source_mutated",
                    "file_id": file_id,
                    "post_upload_sha256": post_upload_sha256,
                }
            )
            persist_journal()
            raise SystemExit(f"Upload source changed during upload: {upload_path}")
        role_to_file_ids.setdefault(prepared["role_label"], []).append(file_id)
        if prepared.get("bundle_items"):
            for bundle_item in prepared["bundle_items"]:
                expanded = resolved_manifest[bundle_item["field_name"]][bundle_item["attachment_index"]]["resolved"][
                    "expanded_paths"
                ][bundle_item["expanded_index"]]
                expanded["uploaded_file_id"] = file_id
                expanded["purpose"] = response.get("purpose", purpose)
                if response.get("expires_at") is not None:
                    expanded["expires_at"] = int(response["expires_at"])
        elif prepared["role_label"] == "Stage Input Manifest":
            manifest_file_id = file_id
        else:
            expanded = resolved_manifest[prepared["field_name"]][prepared["attachment_index"]]["resolved"][
                "expanded_paths"
            ][prepared["expanded_index"]]
            expanded["uploaded_file_id"] = file_id
            expanded["purpose"] = response.get("purpose", purpose)
            if response.get("expires_at") is not None:
                expanded["expires_at"] = int(response["expires_at"])
        journal_record.update(
            {
                "status": "uploaded",
                "file_id": file_id,
                "purpose": response.get("purpose", purpose),
                "created_at": response.get("created_at"),
                "expires_at": response.get("expires_at"),
            }
        )
        persist_journal()
    if not manifest_file_id:
        raise SystemExit("Failed to upload stage input manifest markdown.")
    return manifest_file_id, role_to_file_ids, uploads_payload, resolved_manifest


def cleanup_uploaded_files(
    *,
    client: Any,
    uploads_payload: dict[str, Any],
    journal_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Delete recorded files, checkpointing each intent and result when requested."""

    updated = copy.deepcopy(uploads_payload)
    updated_files = updated.get("files")
    if not isinstance(updated_files, list):
        updated_files = []
        updated["files"] = updated_files

    def persist_journal() -> None:
        if journal_callback is not None:
            journal_callback(copy.deepcopy(updated))

    for index, record in enumerate(list(updated_files)):
        if not isinstance(record, dict):
            continue
        item = dict(record)
        updated_files[index] = item
        file_id = item.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            continue
        if item.get("delete_status") == "deleted":
            continue
        item["delete_status"] = "deleting"
        item.pop("delete_error", None)
        item.pop("delete_response", None)
        persist_journal()
        try:
            delete_response = client.delete_file(file_id)
            item["delete_status"] = "deleted" if delete_response.get("deleted") else "not_deleted"
            item["delete_response"] = delete_response
        except Exception as exc:  # pragma: no cover - defensive
            if getattr(exc, "status_code", None) == 404:
                item["delete_status"] = "deleted"
                item["delete_response"] = {
                    "id": file_id,
                    "deleted": True,
                    "recovered_from": "not_found",
                }
            else:
                item["delete_status"] = (
                    "delete_outcome_unknown"
                    if bool(getattr(exc, "outcome_unknown", False))
                    else "error"
                )
                item["delete_error"] = str(exc)
        persist_journal()
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
