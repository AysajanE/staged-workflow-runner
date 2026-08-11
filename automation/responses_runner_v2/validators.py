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
MARKDOWN_PLAYBOOK_COLUMNS = (
    "step_id",
    "phase",
    "action",
    "why_now",
    "owner_type",
    "prerequisites",
    "repo_surfaces",
    "deliverable",
    "exit_criteria",
    "allowed_write_roots",
    "requires_red_green",
    "required_verification_commands",
)
MARKDOWN_PLAYBOOK_HEADINGS = (
    "## 1. Phase Overview",
    "## 2. Execution Items",
    "## 3. Phase Details",
    "## 4. Shared Guidance",
    "## 5. Risks And Contingencies",
    "## 6. Immediate Next Actions",
)
EVIDENCE_CITATION_RE = re.compile(
    r"\[(workspace_file|repository_path|stage_artifact|operator_input):([^\]\r\n]+)\]"
)
COMMONMARK_FENCE_OPEN_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")


def _violation(rule_id: str, message: str, line: int | None = None) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "message": message,
        "blocking": True,
        **({"line": line} if line is not None else {}),
    }


def validate_commonmark_fences(text: str) -> list[dict[str, Any]]:
    """Report unclosed CommonMark fenced code blocks without parsing code contents."""

    active: tuple[str, int, int] | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        if active is None:
            match = COMMONMARK_FENCE_OPEN_RE.match(line)
            if match is None:
                continue
            fence = match.group(2)
            info = match.group(3)
            if fence[0] == "`" and "`" in info:
                continue
            active = (fence[0], len(fence), line_number)
            continue
        marker, minimum_length, _opening_line = active
        closing = re.fullmatch(rf" {{0,3}}{re.escape(marker)}{{{minimum_length},}}[ \t]*", line)
        if closing is not None:
            active = None
    if active is None:
        return []
    marker, length, opening_line = active
    return [
        _violation(
            "markdown.unclosed_fence",
            f"unclosed CommonMark fence ({marker * length}) opened on line {opening_line}",
            opening_line,
        )
    ]


def _split_table_row(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in stripped[1:-1]:
        if escaped:
            current.append("\\")
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip())
    return cells


def _unsafe_path_reason(cell: str, *, write_root: bool) -> str | None:
    lowered = cell.casefold()
    if re.search(r"(^|[\s,;`])(?:/|~/|[a-z]:\\)", lowered):
        return "absolute paths are not allowed"
    if re.search(r"(^|[/\s,;`])\.\.?/", lowered):
        return "dot-relative and parent-relative paths are not allowed"
    if re.search(r"(^|[/\s,;`])(?:\.git|\.local)(?:[/\s,;`]|$)", lowered):
        return ".git and .local paths are not allowed"
    tokens = [token.strip(" `") for token in re.split(r",|;|<br\s*/?>", cell, flags=re.I)]
    if any(
        is_sensitive_filename(part)
        for token in tokens
        for part in Path(token).parts
        if token
    ):
        return "sensitive filenames are not allowed"
    if write_root and lowered.strip(" `") in {".", "./", "repo", "repository", "root"}:
        return "allowed_write_roots must be narrower than the repository root"
    return None


def validate_markdown_playbook_v1(text: str) -> list[dict[str, Any]]:
    """Return blocking mechanical violations of the markdown_playbook_v1 contract."""

    lines = text.splitlines()
    violations: list[dict[str, Any]] = []
    nonempty = [(index, line) for index, line in enumerate(lines, start=1) if line.strip()]
    if not nonempty or not re.match(r"^#\s+\S", nonempty[0][1]):
        violations.append(
            _violation("title.required", "first non-empty line must be a level-one title")
        )
    if nonempty and nonempty[0][1].lstrip().startswith(("```", "~~~")):
        violations.append(
            _violation("artifact.no_outer_fence", "playbook must not be wrapped in a code fence")
        )

    heading_lines: dict[str, int] = {}
    for heading in MARKDOWN_PLAYBOOK_HEADINGS:
        matches = [index for index, line in enumerate(lines, start=1) if line.strip() == heading]
        if len(matches) != 1:
            violations.append(
                _violation(
                    "section.required_once",
                    f"required section must appear exactly once: {heading}",
                )
            )
        else:
            heading_lines[heading] = matches[0]
    ordered_lines = [
        heading_lines[heading]
        for heading in MARKDOWN_PLAYBOOK_HEADINGS
        if heading in heading_lines
    ]
    if (
        len(ordered_lines) == len(MARKDOWN_PLAYBOOK_HEADINGS)
        and ordered_lines != sorted(ordered_lines)
    ):
        violations.append(_violation("section.order", "required sections are out of order"))

    header_index: int | None = None
    header_cells: list[str] | None = None
    for index, line in enumerate(lines):
        cells = _split_table_row(line)
        if cells and tuple(cell.casefold() for cell in cells) == MARKDOWN_PLAYBOOK_COLUMNS:
            header_index = index
            header_cells = cells
            break
    if header_index is None or header_cells is None:
        violations.append(
            _violation(
                "execution_table.columns",
                "execution table is missing the exact markdown_playbook_v1 columns",
            )
        )
        return violations
    execution_heading = heading_lines.get("## 2. Execution Items")
    details_heading = heading_lines.get("## 3. Phase Details")
    header_line = header_index + 1
    if (
        execution_heading is not None
        and details_heading is not None
        and not execution_heading < header_line < details_heading
    ):
        violations.append(
            _violation(
                "execution_table.location",
                "execution table must appear inside the Execution Items section",
                header_line,
            )
        )

    if "\\|" in lines[header_index]:
        violations.append(
            _violation(
                "execution_table.cell_pipe",
                "pipe characters are not allowed inside table cells",
                header_index + 1,
            )
        )
    separator_index = header_index + 1
    separator = _split_table_row(lines[separator_index]) if separator_index < len(lines) else None
    if separator is None or len(separator) != len(MARKDOWN_PLAYBOOK_COLUMNS) or any(
        re.fullmatch(r":?-{3,}:?", cell) is None for cell in separator
    ):
        violations.append(
            _violation(
                "execution_table.separator",
                "execution table must have one Markdown separator cell per required column",
                separator_index + 1,
            )
        )
        return violations

    rows: list[tuple[int, list[str]]] = []
    for index in range(separator_index + 1, len(lines)):
        if not lines[index].strip():
            if rows:
                break
            continue
        cells = _split_table_row(lines[index])
        if cells is None:
            break
        rows.append((index + 1, cells))
    if not rows:
        violations.append(
            _violation("execution_table.rows", "execution table must contain at least one row")
        )
        return violations

    seen_step_ids: set[str] = set()
    for row_number, (line_number, cells) in enumerate(rows, start=1):
        if "\\|" in lines[line_number - 1]:
            violations.append(
                _violation(
                    "execution_table.cell_pipe",
                    "pipe characters are not allowed inside table cells",
                    line_number,
                )
            )
        if len(cells) != len(MARKDOWN_PLAYBOOK_COLUMNS):
            violations.append(
                _violation(
                    "execution_table.cell_count",
                    f"row has {len(cells)} cells; expected {len(MARKDOWN_PLAYBOOK_COLUMNS)}",
                    line_number,
                )
            )
            continue
        empty_columns = [
            MARKDOWN_PLAYBOOK_COLUMNS[index] for index, cell in enumerate(cells) if not cell.strip()
        ]
        if empty_columns:
            violations.append(
                _violation(
                    "execution_table.nonempty_cells",
                    f"row has empty required cells: {', '.join(empty_columns)}",
                    line_number,
                )
            )
        row = dict(zip(MARKDOWN_PLAYBOOK_COLUMNS, cells, strict=True))
        expected_step_id = f"{row_number:02d}"
        if row["step_id"] != expected_step_id:
            violations.append(
                _violation(
                    "step_id.sequential",
                    f"step_id must be {expected_step_id}, got {row['step_id'] or '<empty>'}",
                    line_number,
                )
            )
        prerequisites = row["prerequisites"].casefold().strip()
        if prerequisites != "none":
            references = re.findall(r"\b\d{2,}\b", prerequisites)
            if not references:
                violations.append(
                    _violation(
                        "prerequisites.explicit",
                        "prerequisites must be none, explicit step ids, or an explicit range",
                        line_number,
                    )
                )
            for reference in references:
                if reference not in seen_step_ids:
                    violations.append(
                        _violation(
                            "prerequisites.backward_only",
                            f"prerequisite {reference} must identify an earlier step",
                            line_number,
                        )
                    )
        if row["requires_red_green"].casefold() not in {"true", "false"}:
            violations.append(
                _violation(
                    "requires_red_green.boolean",
                    "requires_red_green must be true or false",
                    line_number,
                )
            )
        for column in ("repo_surfaces", "deliverable", "allowed_write_roots"):
            reason = _unsafe_path_reason(row[column], write_root=column == "allowed_write_roots")
            if reason:
                violations.append(_violation(f"{column}.safe_path", reason, line_number))
        seen_step_ids.add(row["step_id"])
    return violations


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
    run_contract: dict[str, Any] = {}
    if run_dir is not None:
        try:
            run_manifest = _load_json_object(run_dir / "run_manifest.json", label="run manifest")
            run_contract = _load_json_object(run_dir / "run_contract.json", label="run contract")
        except ValueError as exc:
            violations.append(_violation("citation.run_contract", str(exc)))

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
            bindings = run_contract.get("effective_runtime", {}).get("input_bindings", [])
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
    "markdown_playbook_v1": validate_markdown_playbook_v1,
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
