from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterable

from .contracts import relpath, resolve_under_root, runner_now, sha256_file, write_json
from .schema_validation import validate_contract


PURGE_SCHEMA_VERSION = "responses_runner_v2.purge_tombstone.v1"
PURGE_PATTERNS: dict[str, tuple[str, ...]] = {
    "raw_request": (
        "**/request_payload.json",
        "**/sidecar.response.request.json",
    ),
    "raw_response": (
        "**/response.latest.json",
        "**/response.final.json",
        "**/sidecar.response.latest.json",
        "**/sidecar.response.raw.json",
        "**/sidecar.response.json",
    ),
    "reviewer_output": (
        "**/review_cycles/*/operator/**",
        "**/review_cycles/*/reviewers/codex/**",
        "**/review_cycles/*/reviewers/claude/**",
        "**/review_cycles/*/revision/operator/**",
    ),
    "reasoning_summary": ("**/reasoning_summary*.json", "**/reasoning_summary*.md"),
}


def _targets(target_dir: Path, categories: Iterable[str]) -> list[tuple[str, Path]]:
    selected: dict[Path, str] = {}
    for category in categories:
        if category not in PURGE_PATTERNS:
            raise SystemExit(f"Unknown purge category {category!r}.")
        for pattern in PURGE_PATTERNS[category]:
            for path in target_dir.glob(pattern):
                if path.is_file() and "purge_tombstones" not in path.parts:
                    selected.setdefault(path, category)
    return [(selected[path], path) for path in sorted(selected)]


def _write_tombstone(path: Path, payload: dict[str, Any]) -> None:
    validate_contract(payload, "purge_tombstone.schema.json", label="purge tombstone")
    write_json(path, payload)


def purge_evidence(
    *,
    root: Path,
    target_dir: str | Path,
    categories: Iterable[str] = (),
    reason: str = "",
    resume_tombstone: str | Path | None = None,
) -> dict[str, Any]:
    """Delete only explicit raw-evidence classes while preserving hashes and intent."""

    resolved_target = resolve_under_root(root, target_dir, must_exist=True)
    if not resolved_target.is_dir():
        raise SystemExit(f"Purge target must be a directory: {resolved_target}")
    if resume_tombstone is not None:
        tombstone_path = resolve_under_root(root, resume_tombstone, must_exist=True)
        payload = json.loads(tombstone_path.read_text(encoding="utf-8"))
        validate_contract(payload, "purge_tombstone.schema.json", label="purge tombstone")
        if resolve_under_root(root, payload["target_dir"], must_exist=True) != resolved_target:
            raise SystemExit("Purge tombstone target does not match --target-dir.")
    else:
        selected_categories = tuple(dict.fromkeys(categories))
        if not selected_categories:
            raise SystemExit("At least one --category is required for a new purge.")
        if not reason.strip():
            raise SystemExit("--reason is required for a new purge.")
        tombstone_dir = resolved_target / "purge_tombstones"
        tombstone_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        tombstone_path = tombstone_dir / (
            f"purge_{runner_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:12]}.json"
        )
        records = [
            {
                "category": category,
                "path": relpath(root, path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "status": "pending",
            }
            for category, path in _targets(resolved_target, selected_categories)
        ]
        payload = {
            "schema_version": PURGE_SCHEMA_VERSION,
            "purge_id": tombstone_path.stem,
            "created_at": runner_now().isoformat(),
            "updated_at": runner_now().isoformat(),
            "status": "pending",
            "target_dir": relpath(root, resolved_target),
            "categories": list(selected_categories),
            "reason": reason.strip(),
            "records": records,
        }
        _write_tombstone(tombstone_path, payload)

    for record in payload["records"]:
        if record["status"] == "deleted":
            continue
        target = resolve_under_root(root, record["path"], must_exist=False)
        try:
            target.relative_to(resolved_target)
        except ValueError as exc:
            raise SystemExit(f"Purge record escapes target directory: {target}") from exc
        if target.exists():
            if not target.is_file() or target.is_symlink():
                raise SystemExit(f"Purge target is not a regular file: {target}")
            if sha256_file(target) != record["sha256"]:
                raise SystemExit(f"Purge target changed after intent was recorded: {target}")
            target.unlink()
        record["status"] = "deleted"
        record["deleted_at"] = runner_now().isoformat()
        payload["updated_at"] = runner_now().isoformat()
        _write_tombstone(tombstone_path, payload)

    payload["status"] = "completed"
    payload["updated_at"] = runner_now().isoformat()
    _write_tombstone(tombstone_path, payload)
    return {
        "tombstone_path": relpath(root, tombstone_path),
        "deleted_count": sum(record["status"] == "deleted" for record in payload["records"]),
    }
