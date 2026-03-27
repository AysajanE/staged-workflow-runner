from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import (
    RUN_MANIFEST_SCHEMA_VERSION,
    STAGE_CHECKPOINT_SCHEMA_VERSION,
    WorkflowDefinition,
    relpath,
    resolve_under_root,
    runner_now,
    sha256_file,
    timestamp_slug,
    write_json,
    write_text,
)


def create_run_dir(
    *,
    root: Path,
    output_root: Path,
    run_name: str,
    workflow_id: str,
    run_dir: Path | None = None,
) -> Path:
    if run_dir is not None:
        resolved = resolve_under_root(root, run_dir, must_exist=False)
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved
    resolved_output_root = resolve_under_root(root, output_root, must_exist=False)
    resolved_output_root.mkdir(parents=True, exist_ok=True)
    target = resolved_output_root / f"{timestamp_slug()}_{run_name}_{workflow_id}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def build_stage_paths(run_dir: Path, stage_number: int, stage_id: str) -> dict[str, Path]:
    stage_dir = run_dir / "stages" / f"{stage_number:02d}_{stage_id}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    return {
        "stage_dir": stage_dir,
        "input_manifest_json": stage_dir / "input_manifest.json",
        "input_manifest_md": stage_dir / "input_manifest.md",
        "request_payload": stage_dir / "request_payload.json",
        "token_preflight": stage_dir / "token_preflight.json",
        "token_preflight_error": stage_dir / "token_preflight.error.json",
        "uploads_json": stage_dir / "uploads.json",
        "response_latest_json": stage_dir / "response.latest.json",
        "response_final_json": stage_dir / "response.final.json",
        "response_final_md": stage_dir / "response.final.md",
        "structured_output": stage_dir / "output.structured.json",
        "sidecar_response_json": stage_dir / "sidecar.response.json",
        "sidecar_response_md": stage_dir / "sidecar.response.md",
        "stage_checkpoint": stage_dir / "stage_checkpoint.json",
    }


def initialize_run_manifest(
    *,
    root: Path,
    workflow: WorkflowDefinition,
    run_id: str,
    run_name: str,
    run_dir: Path,
    operator_overrides: dict[str, Any],
) -> dict[str, Any]:
    stages = []
    for stage in workflow.stages:
        stage_paths = build_stage_paths(run_dir, stage.stage_number, stage.stage_id)
        stages.append(
            {
                "stage_id": stage.stage_id,
                "stage_number": stage.stage_number,
                "gate": stage.gate.value,
                "stage_dir": relpath(root, stage_paths["stage_dir"]),
                "status": "prepared",
            }
        )
    now = runner_now().isoformat()
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "run_name": run_name,
        "workflow_id": workflow.workflow_id,
        "workflow_manifest_path": relpath(root, workflow.workflow_file),
        "workflow_manifest_sha256": sha256_file(workflow.workflow_file),
        "run_dir": relpath(root, run_dir),
        "started_at": now,
        "updated_at": now,
        "status": "created",
        "stage_order": [stage.stage_id for stage in workflow.stages],
        "operator_overrides": operator_overrides,
        "stages": stages,
    }


def run_manifest_path(run_dir: Path) -> Path:
    return run_dir / "run_manifest.json"


def load_run_manifest(root: Path, run_dir: Path) -> dict[str, Any]:
    return json.loads(resolve_under_root(root, run_manifest_path(run_dir), must_exist=True).read_text(encoding="utf-8"))


def write_run_manifest(run_dir: Path, manifest: dict[str, Any]) -> Path:
    manifest["updated_at"] = runner_now().isoformat()
    return write_json(run_manifest_path(run_dir), manifest)


def find_stage_summary(run_manifest: dict[str, Any], stage_id: str) -> dict[str, Any]:
    for stage_summary in run_manifest["stages"]:
        if stage_summary["stage_id"] == stage_id:
            return stage_summary
    raise KeyError(stage_id)


def write_input_manifests(
    *,
    stage_paths: dict[str, Path],
    resolved_manifest: dict[str, Any],
    rendered_markdown: str,
) -> tuple[Path, Path]:
    write_json(stage_paths["input_manifest_json"], resolved_manifest)
    write_text(stage_paths["input_manifest_md"], rendered_markdown)
    return stage_paths["input_manifest_json"], stage_paths["input_manifest_md"]


def write_request_payload(
    *,
    stage_paths: dict[str, Path],
    payload: dict[str, Any],
) -> Path:
    return write_json(stage_paths["request_payload"], payload)


def write_uploads_payload(stage_paths: dict[str, Path], uploads_payload: dict[str, Any]) -> Path:
    return write_json(stage_paths["uploads_json"], uploads_payload)


def write_token_preflight_success(
    stage_paths: dict[str, Path],
    payload: dict[str, Any],
) -> Path:
    return write_json(stage_paths["token_preflight"], payload)


def write_token_preflight_error(
    stage_paths: dict[str, Path],
    payload: dict[str, Any],
) -> Path:
    return write_json(stage_paths["token_preflight_error"], payload)


def write_response_latest(stage_paths: dict[str, Path], response_json: dict[str, Any]) -> Path:
    return write_json(stage_paths["response_latest_json"], response_json)


def write_stage_checkpoint(stage_paths: dict[str, Path], checkpoint: dict[str, Any]) -> Path:
    checkpoint["schema_version"] = STAGE_CHECKPOINT_SCHEMA_VERSION
    return write_json(stage_paths["stage_checkpoint"], checkpoint)


def load_stage_checkpoint(stage_paths: dict[str, Path]) -> dict[str, Any]:
    return json.loads(stage_paths["stage_checkpoint"].read_text(encoding="utf-8"))


def _render_json_block(value: object) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def extract_output_text(response_json: dict[str, Any]) -> str:
    texts: list[str] = []
    output = response_json.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                texts.append(part["refusal"])
    return "\n\n".join(text.strip() for text in texts if text and text.strip()).strip()


def extract_response_sources(response_json: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    output = response_json.get("output")
    if not isinstance(output, list):
        return sources
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "web_search_call":
            action = item.get("action")
            if isinstance(action, dict) and isinstance(action.get("sources"), list):
                for source in action["sources"]:
                    if not isinstance(source, dict):
                        continue
                    url = source.get("url")
                    if not isinstance(url, str) or not url:
                        continue
                    title = source.get("title") if isinstance(source.get("title"), str) else url
                    key = ("web_search_call", title, url)
                    if key in seen:
                        continue
                    seen.add(key)
                    sources.append({"origin": "web_search_call", "title": title, "url": url})
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "output_text":
                continue
            annotations = part.get("annotations")
            if not isinstance(annotations, list):
                continue
            for annotation in annotations:
                if not isinstance(annotation, dict):
                    continue
                url = annotation.get("url")
                if not isinstance(url, str) or not url:
                    continue
                title = annotation.get("title") if isinstance(annotation.get("title"), str) else url
                key = ("message_citation", title, url)
                if key in seen:
                    continue
                seen.add(key)
                sources.append({"origin": "message_citation", "title": title, "url": url})
    return sources


def extract_tool_call_summaries(response_json: dict[str, Any]) -> list[str]:
    summaries: list[str] = []
    output = response_json.get("output")
    if not isinstance(output, list):
        return summaries
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if not isinstance(item_type, str) or not item_type.endswith("_call"):
            continue
        parts = [f"type={item_type}"]
        if isinstance(item.get("id"), str):
            parts.append(f"id={item['id']}")
        if isinstance(item.get("status"), str):
            parts.append(f"status={item['status']}")
        action = item.get("action")
        if isinstance(action, dict) and isinstance(action.get("query"), str):
            parts.append(f'query="{action["query"]}"')
        summaries.append("- " + ", ".join(parts))
    return summaries


def extract_structured_output(response_json: dict[str, Any], requested_text_format: str) -> Any | None:
    if requested_text_format != "json_schema":
        return None
    if response_json.get("output_parsed") is not None:
        return response_json["output_parsed"]
    output = response_json.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("parsed") is not None:
                return part.get("parsed")
            if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                continue
            try:
                return json.loads(part["text"])
            except json.JSONDecodeError:
                continue
    return None


def write_response_pair(
    *,
    root: Path,
    markdown_path: Path,
    json_path: Path,
    title: str,
    workflow_id: str,
    run_id: str,
    stage_id: str,
    stage_number: int,
    response_json: dict[str, Any],
    requested_text_format: str,
    structured_output: Any | None = None,
    uploads_payload: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    write_json(json_path, response_json)
    output_text = extract_output_text(response_json)
    usage = response_json.get("usage") if isinstance(response_json.get("usage"), dict) else {}
    prompt_details = usage.get("prompt_tokens_details", {}) if isinstance(usage, dict) else {}
    completion_details = usage.get("completion_tokens_details", {}) if isinstance(usage, dict) else {}
    lines = [
        f"# {title}",
        "",
        f"- generated_at: {runner_now().isoformat()}",
        f"- workflow_id: {workflow_id}",
        f"- run_id: {run_id}",
        f"- stage_id: {stage_id}",
        f"- stage_number: {stage_number}",
        f"- response_id: {response_json.get('id')}",
        f"- status: {response_json.get('status')}",
        f"- model: {response_json.get('model')}",
        f"- input_tokens: {usage.get('input_tokens') if isinstance(usage, dict) else None}",
        f"- output_tokens: {usage.get('output_tokens') if isinstance(usage, dict) else None}",
        f"- total_tokens: {usage.get('total_tokens') if isinstance(usage, dict) else None}",
        f"- cached_tokens: {prompt_details.get('cached_tokens') if isinstance(prompt_details, dict) else None}",
        f"- reasoning_tokens: {completion_details.get('reasoning_tokens') if isinstance(completion_details, dict) else None}",
        "",
    ]
    body = output_text or "No assistant text was returned."
    sections = [body]
    if isinstance(usage, dict) and usage:
        sections.append("## Usage Summary\n\n" + _render_json_block(usage))
    if response_json.get("incomplete_details") is not None:
        sections.append(
            "## Incomplete Details\n\n"
            + _render_json_block(response_json.get("incomplete_details"))
        )
    sources = extract_response_sources(response_json)
    if sources:
        sections.append(
            "## Source Summary\n\n"
            + "\n".join(
                f"- [{source['origin']}] {source['title']} — {source['url']}" for source in sources
            )
        )
    tool_summaries = extract_tool_call_summaries(response_json)
    if tool_summaries:
        sections.append("## Tool Call Summary\n\n" + "\n".join(tool_summaries))
    if uploads_payload:
        sections.append("## Uploaded File Lifecycle\n\n" + _render_json_block(uploads_payload))
    if structured_output is not None:
        sections.append("## Structured Output\n\n" + _render_json_block(structured_output))
    write_text(markdown_path, "\n".join(lines) + "\n\n".join(sections).rstrip() + "\n")
    return markdown_path, json_path
