from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from . import attachments, artifacts, review_bundle, sidecar
from .contracts import (
    COMMON_RUNNER_INSTRUCTIONS,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PRIMARY_MODEL,
    DEFAULT_STRUCTURAL_MODEL,
    GateType,
    ModelRole,
    RUNNER_VERSION,
    ResumeMode,
    RunStatus,
    RuntimeOptions,
    StageDefinition,
    StageStatus,
    TERMINAL_RESPONSE_STATUSES,
    build_prompt_cache_key,
    load_json,
    model_max_output_tokens,
    new_run_id,
    normalize_prompt_cache_retention,
    normalize_slug,
    parse_duration_seconds,
    read_text,
    relpath,
    repo_root,
    resolve_under_root,
    runner_now,
    sha256_file,
    unique_strings,
    validate_model_options,
)
from .openai_client import ApiError, OpenAIClient
from .pack_loader import (
    load_input_manifest,
    load_schema_json,
    load_text_asset,
    load_tool_profile,
    load_workflow_definition,
    validate_operator_inputs,
)

REVIEWABLE_APPROVED_SOURCE_STATUSES = {
    StageStatus.WAITING_FOR_REVIEW.value,
    StageStatus.FAILED.value,
}


def _entry_from_path(path: str | Path, *, notes: str | None = None) -> attachments.AttachmentEntry:
    from .contracts import AttachmentEntry

    resolved = Path(path)
    kind = "directory" if resolved.suffix == "" and resolved.exists() and resolved.is_dir() else "file"
    return AttachmentEntry(path=str(path), kind=kind, notes=notes)


def _build_operator_entries(paths: list[str], *, notes: str | None = None) -> list[attachments.AttachmentEntry]:
    from .contracts import AttachmentEntry

    entries: list[AttachmentEntry] = []
    for raw in paths:
        entries.append(AttachmentEntry(path=str(raw), kind="file", notes=notes))
    return entries


def _load_review_bundles(root: Path, review_bundle_paths: list[str]) -> dict[str, dict[str, Any]]:
    bundles: dict[str, dict[str, Any]] = {}
    for bundle_path in review_bundle_paths:
        bundle = review_bundle.load_review_bundle(root=root, bundle_path=bundle_path)
        bundles[str(bundle["source_stage_id"])] = bundle
    return bundles


def _operator_overrides(runtime: RuntimeOptions) -> dict[str, Any]:
    return {
        "primary_job_inputs": list(runtime.primary_job_inputs),
        "reference_context": list(runtime.reference_context),
        "review_bundles": list(runtime.review_bundles),
        "skip_token_count": runtime.skip_token_count,
    }


def _load_or_create_run_manifest(
    *,
    root: Path,
    workflow,
    runtime: RuntimeOptions,
) -> tuple[Path, dict[str, Any]]:
    output_root = runtime.output_root or resolve_under_root(root, DEFAULT_OUTPUT_ROOT, must_exist=False)
    run_name = normalize_slug(runtime.run_name or workflow.workflow_id)
    run_dir = artifacts.create_run_dir(
        root=root,
        output_root=output_root,
        run_name=run_name,
        workflow_id=workflow.workflow_id,
        run_dir=runtime.run_dir,
    )
    manifest_path = artifacts.run_manifest_path(run_dir)
    if manifest_path.exists():
        manifest = artifacts.load_run_manifest(root, run_dir)
        if manifest["workflow_id"] != workflow.workflow_id:
            raise SystemExit(
                f"Run directory workflow mismatch: expected {workflow.workflow_id}, got {manifest['workflow_id']}"
            )
        return run_dir, manifest
    manifest = artifacts.initialize_run_manifest(
        root=root,
        workflow=workflow,
        run_id=new_run_id(),
        run_name=run_name,
        run_dir=run_dir,
        operator_overrides=_operator_overrides(runtime),
    )
    artifacts.write_run_manifest(run_dir, manifest)
    return run_dir, manifest


def _stage_summary_map(run_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["stage_id"]: item for item in run_manifest["stages"]}


def _determine_next_stage(
    *,
    workflow,
    run_manifest: dict[str, Any],
    review_bundles: dict[str, dict[str, Any]],
    explicit_stage_id: str | None,
) -> StageDefinition:
    def approved_review_handoff_exists(previous_stage, previous_summary: dict[str, Any]) -> bool:
        previous_status = str(previous_summary.get("status", ""))
        return (
            previous_stage.gate == GateType.REVIEW_REQUIRED
            and previous_status in REVIEWABLE_APPROVED_SOURCE_STATUSES
            and (
                previous_stage.stage_id in review_bundles
                or (
                    bool(previous_summary.get("review_approved"))
                    and isinstance(previous_summary.get("review_bundle_path"), str)
                    and bool(previous_summary.get("review_bundle_path"))
                )
            )
        )

    stage_summaries = _stage_summary_map(run_manifest)
    if explicit_stage_id:
        stage = workflow.stage(explicit_stage_id)
        for previous_stage in workflow.stages:
            if previous_stage.stage_number >= stage.stage_number:
                break
            previous_summary = stage_summaries[previous_stage.stage_id]
            previous_status = previous_summary["status"]
            if previous_status == StageStatus.COMPLETED.value:
                continue
            if approved_review_handoff_exists(previous_stage, previous_summary):
                continue
            if previous_status == StageStatus.WAITING_FOR_REVIEW.value:
                raise SystemExit(
                    f"Stage {stage.stage_id} requires a review bundle from stage {previous_stage.stage_id}."
                )
            if previous_status != StageStatus.COMPLETED.value:
                raise SystemExit(
                    f"Stage {stage.stage_id} cannot run before stage {previous_stage.stage_id} completes."
                )
        return stage

    for stage in workflow.stages:
        summary = stage_summaries[stage.stage_id]
        status = summary["status"]
        if status in {StageStatus.SUBMITTED.value, StageStatus.IN_PROGRESS.value}:
            raise SystemExit(
                f"Stage {stage.stage_id} is nonterminal. Use resume or refresh instead of run."
            )
        if status not in {StageStatus.PREPARED.value, StageStatus.BLOCKED.value}:
            continue
        if stage.stage_number == 1:
            return stage
        previous_stage = workflow.stages[stage.stage_number - 2]
        previous_summary = stage_summaries[previous_stage.stage_id]
        if previous_summary["status"] == StageStatus.COMPLETED.value:
            return stage
        if approved_review_handoff_exists(previous_stage, previous_summary):
            return stage
        if previous_summary["status"] == StageStatus.WAITING_FOR_REVIEW.value:
            raise SystemExit(
                f"Run is waiting for review after stage {previous_stage.stage_id}. Supply --review-bundle."
            )
    raise SystemExit("No eligible stage was found for this run.")


def _effective_model(workflow, stage: StageDefinition, runtime: RuntimeOptions) -> str:
    if stage.model_role == ModelRole.PRIMARY_GENERATION:
        return runtime.primary_model or workflow.model_roles["primary_generation"].model
    return runtime.structural_model or workflow.model_roles["structural_processing"].model


def _effective_reasoning(workflow, stage: StageDefinition) -> str:
    profile = workflow.model_roles[stage.model_role.value]
    return stage.reasoning_effort or profile.reasoning_effort


def _effective_verbosity(workflow, stage: StageDefinition) -> str:
    profile = workflow.model_roles[stage.model_role.value]
    return stage.verbosity or profile.verbosity


def _effective_prompt_cache_retention(workflow, stage: StageDefinition) -> str | None:
    profile = workflow.model_roles[stage.model_role.value]
    return profile.prompt_cache_retention


def _effective_max_output_tokens(workflow, stage: StageDefinition, runtime: RuntimeOptions) -> int:
    if runtime.max_output_tokens is not None:
        return runtime.max_output_tokens
    if stage.max_output_tokens is not None:
        return stage.max_output_tokens
    model = _effective_model(workflow, stage, runtime)
    return model_max_output_tokens(model) or 32000


def _effective_max_input_tokens(stage: StageDefinition, runtime: RuntimeOptions) -> int | None:
    if runtime.max_input_tokens is not None:
        return runtime.max_input_tokens
    return stage.max_input_tokens


def _effective_service_tier(workflow, runtime: RuntimeOptions) -> str | None:
    return runtime.service_tier or workflow.request_defaults.service_tier


def _effective_safety_identifier(workflow, runtime: RuntimeOptions) -> str | None:
    return runtime.safety_identifier or workflow.request_defaults.safety_identifier


def _effective_expiration_policy(workflow, runtime: RuntimeOptions) -> dict[str, Any] | None:
    seconds = parse_duration_seconds(runtime.file_expires_after)
    if seconds is None:
        seconds = workflow.request_defaults.file_uploads.expires_after_seconds
    if seconds is None:
        return None
    return {"anchor": "created_at", "seconds": seconds}


def _delete_uploads_on_complete(workflow, runtime: RuntimeOptions) -> bool:
    if runtime.delete_uploaded_files_on_complete is not None:
        return runtime.delete_uploaded_files_on_complete
    return workflow.request_defaults.file_uploads.delete_on_completion


def _build_instructions(workflow, stage: StageDefinition) -> str:
    pieces = [COMMON_RUNNER_INSTRUCTIONS.strip(), load_text_asset(workflow.shared_instructions_path).strip()]
    if stage.stage_instructions_path is not None:
        pieces.append(load_text_asset(stage.stage_instructions_path).strip())
    return "\n\n".join(part for part in pieces if part)


def _build_text_config(
    *,
    root: Path,
    workflow,
    stage: StageDefinition,
    runtime: RuntimeOptions,
) -> dict[str, Any]:
    model = _effective_model(workflow, stage, runtime)
    prompt_cache_retention = _effective_prompt_cache_retention(workflow, stage)
    max_output_tokens = _effective_max_output_tokens(workflow, stage, runtime)
    validate_model_options(
        model=model,
        max_output_tokens=max_output_tokens,
        prompt_cache_retention=prompt_cache_retention,
        text_format=stage.output.primary_format,
    )
    if stage.output.primary_format == "text":
        return {"format": {"type": "text"}, "verbosity": _effective_verbosity(workflow, stage)}
    schema = load_schema_json(stage.output.schema_path, root=root)
    return {
        "format": {
            "type": "json_schema",
            "name": stage.output.schema_name,
            "schema": schema,
            "strict": True,
        }
    }


def _resolve_tool_settings(root: Path, workflow, stage: StageDefinition) -> dict[str, Any]:
    profile = load_tool_profile(stage.tool_profile_path, root=root) if stage.tool_profile_path else {}
    tools = profile.get("tools")
    if not isinstance(tools, list) or not tools:
        return {}
    resolved = dict(profile)
    resolved.setdefault("parallel_tool_calls", workflow.request_defaults.parallel_tool_calls)
    resolved.setdefault("max_tool_calls", workflow.request_defaults.max_tool_calls)
    return resolved


def _reference_context_from_stage_outputs(
    *,
    workflow,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
) -> list[attachments.AttachmentEntry]:
    from .contracts import AttachmentEntry

    summaries = _stage_summary_map(run_manifest)
    entries: list[AttachmentEntry] = []
    for source_stage_id in stage.carry_forward.reference_context_from_stage_ids:
        summary = summaries[source_stage_id]
        response_markdown_path = summary.get("response_markdown_path")
        if not response_markdown_path:
            raise SystemExit(
                f"Stage {stage.stage_id} cannot carry forward {source_stage_id}; no response markdown was recorded."
            )
        entries.append(
            AttachmentEntry(
                path=response_markdown_path,
                kind="file",
                notes=f"carry-forward markdown from stage {source_stage_id}",
            )
        )
    return entries


def _review_handoff_entries(
    *,
    root: Path,
    workflow,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    review_bundles: dict[str, dict[str, Any]],
) -> tuple[list[attachments.AttachmentEntry], str | None]:
    source_stage_id = stage.carry_forward.review_bundle_from_stage_id
    if source_stage_id is None:
        return [], None
    if source_stage_id not in review_bundles:
        raise SystemExit(
            f"Stage {stage.stage_id} requires a review bundle from stage {source_stage_id}."
        )
    source_summary = artifacts.find_stage_summary(run_manifest, source_stage_id)
    source_status = str(source_summary.get("status", ""))
    if source_status not in REVIEWABLE_APPROVED_SOURCE_STATUSES:
        raise SystemExit(
            f"Stage {stage.stage_id} cannot consume a review bundle from stage {source_stage_id} "
            f"with status {source_status!r}."
        )
    bundle = review_bundles[source_stage_id]
    review_bundle.validate_review_bundle_for_stage(
        bundle,
        workflow_id=workflow.workflow_id,
        expected_source_stage_id=source_stage_id,
        expected_source_run_id=run_manifest["run_id"],
        root=root,
        source_stage_summary=source_summary,
    )
    source_summary["review_approved"] = True
    source_summary["approved_from_status"] = source_status
    source_summary["review_bundle_path"] = bundle["bundle_path"]
    return review_bundle.expand_review_bundle_inputs(
        bundle,
        include_response_artifact_json=stage.carry_forward.review_bundle_include_response_artifact_json,
    ), str(bundle["bundle_path"])


def _build_request_payload(
    *,
    workflow,
    stage: StageDefinition,
    run_manifest: dict[str, Any],
    runtime: RuntimeOptions,
    text_config: dict[str, Any],
    content: list[dict[str, Any]],
    role_blocks: list[dict[str, Any]],
    tool_settings: dict[str, Any],
) -> dict[str, Any]:
    model = _effective_model(workflow, stage, runtime)
    payload: dict[str, Any] = {
        "model": model,
        "instructions": _build_instructions(workflow, stage),
        "input": [{"role": "user", "content": content}],
        "background": workflow.request_defaults.background,
        "store": workflow.request_defaults.store,
        "truncation": "disabled",
        "reasoning": {"effort": _effective_reasoning(workflow, stage)},
        "text": text_config,
        "max_output_tokens": _effective_max_output_tokens(workflow, stage, runtime),
        "metadata": {
            "workflow_id": workflow.workflow_id,
            "run_id": run_manifest["run_id"],
            "run_name": run_manifest["run_name"],
            "stage_id": stage.stage_id,
            "stage_number": str(stage.stage_number),
            "runner_version": RUNNER_VERSION,
        },
        "prompt_cache_key": build_prompt_cache_key(
            f"{workflow.workflow_id}:{run_manifest['run_id']}",
            stage.stage_id,
        ),
    }
    normalized_retention = normalize_prompt_cache_retention(
        _effective_prompt_cache_retention(workflow, stage)
    )
    if normalized_retention:
        payload["prompt_cache_retention"] = normalized_retention
    if workflow.request_defaults.temperature is not None:
        payload["temperature"] = workflow.request_defaults.temperature
    service_tier = _effective_service_tier(workflow, runtime)
    safety_identifier = _effective_safety_identifier(workflow, runtime)
    if service_tier:
        payload["service_tier"] = service_tier
    if safety_identifier:
        payload["safety_identifier"] = safety_identifier
    for key in ("tools", "tool_choice", "include", "max_tool_calls", "parallel_tool_calls"):
        if key in tool_settings:
            payload[key] = tool_settings[key]
    return payload


def _token_preflight_state(
    *,
    root: Path,
    client: OpenAIClient,
    workflow,
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    payload: dict[str, Any],
    runtime: RuntimeOptions,
) -> dict[str, Any]:
    hard_limit = _effective_max_input_tokens(stage, runtime)
    if runtime.skip_token_count or not workflow.request_defaults.token_preflight.enabled:
        return {"status": "skipped_by_operator", "attempts": 0}
    policy = workflow.request_defaults.token_preflight
    attempts = 0
    last_error: ApiError | None = None
    for attempt in range(1, policy.max_retries + 1):
        attempts = attempt
        try:
            result = client.count_input_tokens_once(payload)
            input_tokens = result.get("input_tokens")
            if not isinstance(input_tokens, int):
                raise SystemExit("token preflight did not return an integer input_tokens value.")
            diagnostics = {
                "object": "token_preflight",
                "workflow_id": workflow.workflow_id,
                "stage_id": stage.stage_id,
                "input_tokens": input_tokens,
                "max_input_tokens": hard_limit,
                "within_limit": hard_limit is None or input_tokens <= hard_limit,
            }
            diagnostics_path = artifacts.write_token_preflight_success(stage_paths, diagnostics)
            if hard_limit is not None and input_tokens > hard_limit:
                error_payload = {
                    "status": "failed_closed",
                    "reason": "max_input_tokens_exceeded",
                    "input_tokens": input_tokens,
                    "max_input_tokens": hard_limit,
                }
                artifacts.write_stage_checkpoint(
                    stage_paths,
                    {
                        "run_id": payload["metadata"]["run_id"],
                        "stage_id": stage.stage_id,
                        "stage_number": stage.stage_number,
                        "updated_at": runner_now().isoformat(),
                        "status": StageStatus.BLOCKED.value,
                        "terminal": True,
                        "resume_mode": ResumeMode.FRESH_SUBMIT.value,
                        "review_checkpoint_required": stage.gate == GateType.REVIEW_REQUIRED,
                        "request_payload_path": relpath(root, stage_paths["request_payload"]),
                        "input_manifest_json_path": relpath(root, stage_paths["input_manifest_json"]),
                        "input_manifest_markdown_path": relpath(root, stage_paths["input_manifest_md"]),
                        "token_preflight": {
                            "status": "failed_closed",
                            "attempts": attempts,
                            "input_tokens": input_tokens,
                            "error_message": "max_input_tokens exceeded",
                            "diagnostics_path": relpath(root, diagnostics_path),
                        },
                        "artifacts": {
                            "stage_dir": relpath(root, stage_paths["stage_dir"]),
                        },
                        "error": error_payload,
                    },
                )
                raise SystemExit(
                    f"Stage {stage.stage_id} input token count {input_tokens} exceeds configured limit {hard_limit}."
                )
            return {
                "status": "succeeded",
                "attempts": attempts,
                "input_tokens": input_tokens,
                "diagnostics_path": relpath(root, diagnostics_path),
            }
        except ApiError as exc:
            last_error = exc
            if exc.status_code in policy.retryable_http_status_codes and attempt < policy.max_retries:
                continue
            error_payload = {
                "object": "token_preflight_error",
                "workflow_id": workflow.workflow_id,
                "stage_id": stage.stage_id,
                "attempts": attempts,
                "status_code": exc.status_code,
                "error_message": str(exc),
                "fallback_decision": (
                    "continue_without_token_count"
                    if exc.status_code in policy.retryable_http_status_codes
                    and policy.on_retryable_service_failure == "continue_without_token_count"
                    and hard_limit is None
                    else "fail_closed"
                ),
            }
            error_path = artifacts.write_token_preflight_error(stage_paths, error_payload)
            if (
                exc.status_code in policy.retryable_http_status_codes
                and policy.on_retryable_service_failure == "continue_without_token_count"
                and hard_limit is None
            ):
                return {
                    "status": "continued_after_retryable_service_failure",
                    "attempts": attempts,
                    "error_message": str(exc),
                    "diagnostics_path": relpath(root, error_path),
                }
            raise SystemExit(
                f"Token preflight failed closed for stage {stage.stage_id}: {exc}"
            ) from exc
    raise SystemExit(f"Token preflight failed for stage {stage.stage_id}: {last_error}")


def _stage_status_from_response(response_json: dict[str, Any], stage: StageDefinition, has_next_stage: bool) -> str:
    status = str(response_json.get("status", "unknown"))
    if status == "completed":
        if stage.gate == GateType.REVIEW_REQUIRED and has_next_stage:
            return StageStatus.WAITING_FOR_REVIEW.value
        return StageStatus.COMPLETED.value
    if status == "failed":
        return StageStatus.FAILED.value
    if status == "cancelled":
        return StageStatus.CANCELLED.value
    if status == "incomplete":
        return StageStatus.INCOMPLETE.value
    if status in {"queued", "in_progress"}:
        return StageStatus.IN_PROGRESS.value
    return StageStatus.SUBMITTED.value


def _response_supports_sidecar_processing(response_json: dict[str, Any]) -> bool:
    status = str(response_json.get("status", "unknown"))
    if status == "completed":
        return True
    if status != "failed":
        return False
    if not artifacts.extract_output_text(response_json):
        return False
    output = response_json.get("output")
    if not isinstance(output, list):
        return False
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        if str(item.get("status", "")) == "completed":
            return True
    return False


def _build_checkpoint(
    *,
    root: Path,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    stage_status: str,
    resume_mode: ResumeMode,
    token_preflight: dict[str, Any],
    response_json: dict[str, Any] | None,
    review_bundle_path: str | None,
    structured_output_written: bool,
    sidecar_written: bool,
    uploads_payload_path: Path | None,
) -> dict[str, Any]:
    checkpoint: dict[str, Any] = {
        "run_id": run_manifest["run_id"],
        "stage_id": stage.stage_id,
        "stage_number": stage.stage_number,
        "updated_at": runner_now().isoformat(),
        "status": stage_status,
        "terminal": stage_status in {
            StageStatus.COMPLETED.value,
            StageStatus.WAITING_FOR_REVIEW.value,
            StageStatus.FAILED.value,
            StageStatus.CANCELLED.value,
            StageStatus.INCOMPLETE.value,
            StageStatus.BLOCKED.value,
        },
        "resume_mode": resume_mode.value,
        "review_checkpoint_required": stage.gate == GateType.REVIEW_REQUIRED,
        "request_payload_path": relpath(root, stage_paths["request_payload"]),
        "input_manifest_json_path": relpath(root, stage_paths["input_manifest_json"]),
        "input_manifest_markdown_path": relpath(root, stage_paths["input_manifest_md"]),
        "token_preflight": token_preflight,
        "artifacts": {
            "stage_dir": relpath(root, stage_paths["stage_dir"]),
            "response_latest_json_path": relpath(root, stage_paths["response_latest_json"]),
            **(
                {"response_final_json_path": relpath(root, stage_paths["response_final_json"])}
                if stage_paths["response_final_json"].exists()
                else {}
            ),
            **(
                {"response_final_markdown_path": relpath(root, stage_paths["response_final_md"])}
                if stage_paths["response_final_md"].exists()
                else {}
            ),
            **(
                {"structured_output_path": relpath(root, stage_paths["structured_output"])}
                if structured_output_written and stage_paths["structured_output"].exists()
                else {}
            ),
            **(
                {"sidecar_response_json_path": relpath(root, stage_paths["sidecar_response_json"])}
                if sidecar_written and stage_paths["sidecar_response_json"].exists()
                else {}
            ),
            **(
                {"sidecar_response_markdown_path": relpath(root, stage_paths["sidecar_response_md"])}
                if sidecar_written and stage_paths["sidecar_response_md"].exists()
                else {}
            ),
            **(
                {"uploads_json_path": relpath(root, uploads_payload_path)}
                if uploads_payload_path is not None
                else {}
            ),
        },
    }
    if review_bundle_path is not None:
        checkpoint["review_bundle_path"] = review_bundle_path
    if response_json is not None:
        checkpoint["response"] = {
            "id": str(response_json.get("id")),
            "status": str(response_json.get("status")),
            "model": str(response_json.get("model")),
            "background": bool(response_json.get("background", False)),
            "store": bool(response_json.get("store", False)),
            **(
                {"created_at": int(response_json["created_at"])}
                if response_json.get("created_at") is not None
                else {}
            ),
            **(
                {"completed_at": int(response_json["completed_at"])}
                if response_json.get("completed_at") is not None
                else {}
            ),
        }
        if response_json.get("error") is not None:
            checkpoint["error"] = response_json.get("error")
        if response_json.get("incomplete_details") is not None:
            checkpoint["incomplete_details"] = response_json.get("incomplete_details")
    return checkpoint


def _sync_stage_summary(
    *,
    root: Path,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    stage_status: str,
    response_json: dict[str, Any] | None,
    review_bundle_path: str | None,
    token_preflight_path: Path | None,
) -> None:
    summary = artifacts.find_stage_summary(run_manifest, stage.stage_id)
    summary["status"] = stage_status
    summary["checkpoint_path"] = relpath(root, stage_paths["stage_checkpoint"])
    summary["input_manifest_json_path"] = relpath(root, stage_paths["input_manifest_json"])
    if stage_paths["response_final_md"].exists():
        summary["response_markdown_path"] = relpath(root, stage_paths["response_final_md"])
        summary["response_markdown_sha256"] = sha256_file(stage_paths["response_final_md"])
    if stage_paths["response_final_json"].exists():
        summary["response_json_path"] = relpath(root, stage_paths["response_final_json"])
        summary["response_json_sha256"] = sha256_file(stage_paths["response_final_json"])
    if stage_paths["structured_output"].exists():
        summary["structured_output_path"] = relpath(root, stage_paths["structured_output"])
        summary["structured_output_sha256"] = sha256_file(stage_paths["structured_output"])
    if token_preflight_path is not None and token_preflight_path.exists():
        summary["token_preflight_path"] = relpath(root, token_preflight_path)
    if review_bundle_path is not None:
        summary["review_bundle_path"] = review_bundle_path
    if response_json is not None:
        summary["response_id"] = str(response_json.get("id"))
        summary["response_status"] = str(response_json.get("status"))


def _run_status_after_stage(
    *,
    stage_status: str,
    has_next_stage: bool,
    stage: StageDefinition,
) -> str:
    if stage_status == StageStatus.WAITING_FOR_REVIEW.value:
        return RunStatus.WAITING_FOR_REVIEW.value
    if stage_status == StageStatus.COMPLETED.value and not has_next_stage:
        return RunStatus.COMPLETED.value
    if stage_status == StageStatus.FAILED.value:
        return RunStatus.FAILED.value
    if stage_status == StageStatus.CANCELLED.value:
        return RunStatus.CANCELLED.value
    if stage_status == StageStatus.INCOMPLETE.value:
        return RunStatus.FAILED.value
    return RunStatus.RUNNING.value


def _build_stage_runtime_manifest(
    *,
    root: Path,
    workflow,
    stage: StageDefinition,
    run_manifest: dict[str, Any],
    static_manifest: dict[str, Any],
    runtime: RuntimeOptions,
    review_bundles: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    reviewed_entries, consumed_review_bundle_path = _review_handoff_entries(
        root=root,
        workflow=workflow,
        run_manifest=run_manifest,
        stage=stage,
        review_bundles=review_bundles,
    )
    carry_forward_reference_entries = _reference_context_from_stage_outputs(
        workflow=workflow,
        run_manifest=run_manifest,
        stage=stage,
    )
    resolved_manifest = attachments.resolve_stage_input_manifest(
        root=root,
        workflow_id=workflow.workflow_id,
        stage_id=stage.stage_id,
        run_id=run_manifest["run_id"],
        manifest_id=f"{workflow.workflow_id}.{stage.stage_id}",
        description=str(static_manifest.get("description") or ""),
        primary_job_inputs=[
            *static_manifest["primary_job_inputs"],
            *_build_operator_entries(runtime.primary_job_inputs, notes="operator-supplied primary job input"),
        ],
        reviewed_handoff_inputs=[
            *static_manifest["reviewed_handoff_inputs"],
            *reviewed_entries,
        ],
        attached_repository_files=static_manifest["attached_repository_files"],
        reference_context=[
            *static_manifest["reference_context"],
            *_build_operator_entries(runtime.reference_context, notes="operator-supplied reference context"),
            *carry_forward_reference_entries,
        ],
    )
    return resolved_manifest, consumed_review_bundle_path


def _write_stage_artifacts_for_response(
    *,
    root: Path,
    client: OpenAIClient | None,
    workflow,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    runtime: RuntimeOptions,
    response_json: dict[str, Any],
    uploads_payload: dict[str, Any] | None,
    allow_sidecar_processing: bool = True,
) -> tuple[bool, bool, dict[str, Any] | None]:
    main_requested_text_format = stage.output.primary_format
    structured_output_written = False
    sidecar_written = False
    structured_output = None
    effective_uploads_payload = uploads_payload
    if main_requested_text_format == "json_schema":
        structured_output = artifacts.extract_structured_output(response_json, "json_schema")
        if structured_output is None:
            raise SystemExit(f"Stage {stage.stage_id} did not return structured output.")
        stage_paths["structured_output"].write_text(
            __import__("json").dumps(structured_output, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        structured_output_written = True

    artifacts.write_response_pair(
        root=root,
        markdown_path=stage_paths["response_final_md"],
        json_path=stage_paths["response_final_json"],
        title="Responses Runner V2 Stage Output",
        workflow_id=workflow.workflow_id,
        run_id=run_manifest["run_id"],
        stage_id=stage.stage_id,
        stage_number=stage.stage_number,
        response_json=response_json,
        requested_text_format=main_requested_text_format,
        structured_output=structured_output,
        uploads_payload=effective_uploads_payload,
    )

    if (
        allow_sidecar_processing
        and stage.output.sidecar is not None
        and _response_supports_sidecar_processing(response_json)
    ):
        if client is None:
            raise SystemExit("A live OpenAI client is required for sidecar processing.")
        structural_model = runtime.structural_model or workflow.model_roles[
            ModelRole.STRUCTURAL_PROCESSING.value
        ].model
        result = sidecar.run_sidecar_processing(
            root=root,
            client=client,
            workflow_id=workflow.workflow_id,
            run_id=run_manifest["run_id"],
            stage_id=stage.stage_id,
            stage_number=stage.stage_number,
            structural_model=structural_model,
            reasoning_effort=workflow.model_roles[
                ModelRole.STRUCTURAL_PROCESSING.value
            ].reasoning_effort,
            prompt_cache_retention=workflow.model_roles[
                ModelRole.STRUCTURAL_PROCESSING.value
            ].prompt_cache_retention,
            schema_file=stage.output.sidecar.schema_path,
            schema_name=stage.output.sidecar.schema_name,
            response_markdown_path=stage_paths["response_final_md"],
            response_json_path=stage_paths["response_final_json"],
            sidecar_response_json_path=stage_paths["sidecar_response_json"],
            sidecar_response_markdown_path=stage_paths["sidecar_response_md"],
            structured_output_path=stage_paths["structured_output"],
            service_tier=_effective_service_tier(workflow, runtime),
            safety_identifier=_effective_safety_identifier(workflow, runtime),
            file_expiration_policy=_effective_expiration_policy(workflow, runtime),
            delete_uploaded_files_on_complete=_delete_uploads_on_complete(workflow, runtime),
        )
        effective_uploads_payload = _merge_uploads_payloads(
            effective_uploads_payload,
            result.get("uploads_payload"),
        )
        if effective_uploads_payload is not None:
            artifacts.write_response_pair(
                root=root,
                markdown_path=stage_paths["response_final_md"],
                json_path=stage_paths["response_final_json"],
                title="Responses Runner V2 Stage Output",
                workflow_id=workflow.workflow_id,
                run_id=run_manifest["run_id"],
                stage_id=stage.stage_id,
                stage_number=stage.stage_number,
                response_json=response_json,
                requested_text_format=main_requested_text_format,
                structured_output=structured_output,
                uploads_payload=effective_uploads_payload,
            )
        structured_output_written = True
        sidecar_written = True
    return structured_output_written, sidecar_written, effective_uploads_payload


def run_workflow(
    *,
    workflow_file: str | Path,
    runtime: RuntimeOptions,
    client: OpenAIClient | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Launch the next eligible workflow stage or dry-run request construction."""

    root = root or repo_root()
    workflow = load_workflow_definition(
        workflow_file,
        root=root,
        primary_model_override=runtime.primary_model,
        structural_model_override=runtime.structural_model,
    )
    validate_operator_inputs(
        workflow,
        primary_job_inputs=runtime.primary_job_inputs,
        reference_context=runtime.reference_context,
    )
    run_dir, run_manifest = _load_or_create_run_manifest(root=root, workflow=workflow, runtime=runtime)
    review_bundles = _load_review_bundles(root, runtime.review_bundles)
    current_stage = _determine_next_stage(
        workflow=workflow,
        run_manifest=run_manifest,
        review_bundles=review_bundles,
        explicit_stage_id=runtime.stage_id,
    )

    while True:
        stage = current_stage
        stage_paths = artifacts.build_stage_paths(run_dir, stage.stage_number, stage.stage_id)
        static_manifest = load_input_manifest(stage.input_manifest_path, root=root)
        resolved_manifest, consumed_review_bundle_path = _build_stage_runtime_manifest(
            root=root,
            workflow=workflow,
            stage=stage,
            run_manifest=run_manifest,
            static_manifest=static_manifest,
            runtime=runtime,
            review_bundles=review_bundles,
        )
        rendered_manifest_md = attachments.render_input_manifest_markdown(resolved_manifest)
        artifacts.write_input_manifests(
            stage_paths=stage_paths,
            resolved_manifest=resolved_manifest,
            rendered_markdown=rendered_manifest_md,
        )

        role_blocks: list[dict[str, Any]] = []
        uploads_payload: dict[str, Any] | None = None
        request_payload: dict[str, Any]
        if runtime.dry_run:
            content = [{"type": "input_text", "text": load_text_asset(stage.task_path).strip()}]
            request_payload = _build_request_payload(
                workflow=workflow,
                stage=stage,
                run_manifest=run_manifest,
                runtime=runtime,
                text_config=_build_text_config(root=root, workflow=workflow, stage=stage, runtime=runtime),
                content=content,
                role_blocks=[
                    {
                        "role": "dry_run_preview",
                        "file_paths": unique_strings(
                            [
                                expanded["path"]
                                for field_name in ("primary_job_inputs", "reviewed_handoff_inputs", "attached_repository_files", "reference_context")
                                for entry in resolved_manifest[field_name]
                                for expanded in entry["resolved"]["expanded_paths"]
                            ]
                        ),
                    }
                ],
                tool_settings=_resolve_tool_settings(root, workflow, stage),
            )
            artifacts.write_request_payload(stage_paths=stage_paths, payload=request_payload)
            stage_status = StageStatus.PREPARED.value
            checkpoint = _build_checkpoint(
                root=root,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=stage_status,
                resume_mode=ResumeMode.FRESH_SUBMIT,
                token_preflight={"status": "pending"},
                response_json=None,
                review_bundle_path=consumed_review_bundle_path,
                structured_output_written=False,
                sidecar_written=False,
                uploads_payload_path=None,
            )
            artifacts.write_stage_checkpoint(stage_paths, checkpoint)
            _sync_stage_summary(
                root=root,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=stage_status,
                response_json=None,
                review_bundle_path=consumed_review_bundle_path,
                token_preflight_path=None,
            )
            run_manifest["status"] = RunStatus.CREATED.value
            run_manifest["current_stage_id"] = stage.stage_id
            artifacts.write_run_manifest(run_dir, run_manifest)
            return {
                "run_dir": relpath(root, run_dir),
                "run_manifest_path": relpath(root, artifacts.run_manifest_path(run_dir)),
                "status": run_manifest["status"],
                "stage_id": stage.stage_id,
            }

        if client is None:
            raise SystemExit("A live OpenAI client is required unless --dry-run is used.")

        with tempfile.TemporaryDirectory(prefix=f"{run_manifest['run_id']}-{stage.stage_id}-") as staging_dir_raw:
            staging_dir = Path(staging_dir_raw)
            prepared_uploads = attachments.prepare_upload_plan(
                root=root,
                resolved_manifest=resolved_manifest,
                input_manifest_markdown_path=stage_paths["input_manifest_md"],
                staging_dir=staging_dir,
            )
            manifest_file_id, role_to_file_ids, uploads_payload, resolved_manifest = attachments.upload_prepared_attachments(
                root=root,
                client=client,
                resolved_manifest=resolved_manifest,
                prepared_uploads=prepared_uploads,
                purpose=workflow.request_defaults.file_uploads.purpose,
                file_expiration_policy=_effective_expiration_policy(workflow, runtime),
                delete_uploaded_files_on_complete=_delete_uploads_on_complete(workflow, runtime),
            )
            artifacts.write_input_manifests(
                stage_paths=stage_paths,
                resolved_manifest=resolved_manifest,
                rendered_markdown=rendered_manifest_md,
            )
            uploads_payload_path = artifacts.write_uploads_payload(stage_paths, uploads_payload)
            content, role_blocks = attachments.build_request_input_content(
                task_text=load_text_asset(stage.task_path).strip(),
                input_manifest_file_id=manifest_file_id,
                role_to_file_ids=role_to_file_ids,
            )
            request_payload = _build_request_payload(
                workflow=workflow,
                stage=stage,
                run_manifest=run_manifest,
                runtime=runtime,
                text_config=_build_text_config(root=root, workflow=workflow, stage=stage, runtime=runtime),
                content=content,
                role_blocks=role_blocks,
                tool_settings=_resolve_tool_settings(root, workflow, stage),
            )
            artifacts.write_request_payload(stage_paths=stage_paths, payload=request_payload)
            token_preflight = _token_preflight_state(
                root=root,
                client=client,
                workflow=workflow,
                stage=stage,
                stage_paths=stage_paths,
                payload=request_payload,
                runtime=runtime,
            )
            token_preflight_path = None
            if token_preflight.get("diagnostics_path"):
                token_preflight_path = resolve_under_root(root, token_preflight["diagnostics_path"], must_exist=True)

            response_json = client.create_response(request_payload)
            artifacts.write_response_latest(stage_paths, response_json)
            has_next_stage = workflow.next_stage(stage.stage_id) is not None
            stage_status = _stage_status_from_response(response_json, stage, has_next_stage)
            checkpoint = _build_checkpoint(
                root=root,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=stage_status,
                resume_mode=ResumeMode.FRESH_SUBMIT,
                token_preflight=token_preflight,
                response_json=response_json,
                review_bundle_path=consumed_review_bundle_path,
                structured_output_written=False,
                sidecar_written=False,
                uploads_payload_path=uploads_payload_path,
            )
            artifacts.write_stage_checkpoint(stage_paths, checkpoint)
            _sync_stage_summary(
                root=root,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=stage_status,
                response_json=response_json,
                review_bundle_path=consumed_review_bundle_path,
                token_preflight_path=token_preflight_path,
            )
            run_manifest["status"] = _run_status_after_stage(
                stage_status=stage_status,
                has_next_stage=has_next_stage,
                stage=stage,
            )
            run_manifest["current_stage_id"] = stage.stage_id
            artifacts.write_run_manifest(run_dir, run_manifest)

            if runtime.wait and str(response_json.get("status")) not in TERMINAL_RESPONSE_STATUSES:
                response_json = client.wait_for_terminal_response(
                    str(response_json["id"]),
                    poll_interval=runtime.poll_interval,
                    max_wait_seconds=runtime.max_wait_seconds,
                    checkpoint_callback=lambda polled: artifacts.write_response_latest(stage_paths, polled),
                )
                artifacts.write_response_latest(stage_paths, response_json)

            if str(response_json.get("status")) in TERMINAL_RESPONSE_STATUSES:
                if uploads_payload and uploads_payload.get("delete_uploaded_files_on_complete"):
                    uploads_payload = attachments.cleanup_uploaded_files(
                        client=client,
                        uploads_payload=uploads_payload,
                    )
                    uploads_payload_path = artifacts.write_uploads_payload(stage_paths, uploads_payload)
                structured_output_written, sidecar_written, uploads_payload = _write_stage_artifacts_for_response(
                    root=root,
                    client=client,
                    workflow=workflow,
                    run_manifest=run_manifest,
                    stage=stage,
                    stage_paths=stage_paths,
                    runtime=runtime,
                    response_json=response_json,
                    uploads_payload=uploads_payload,
                )
                if uploads_payload is not None:
                    uploads_payload_path = artifacts.write_uploads_payload(stage_paths, uploads_payload)
                final_stage_status = _stage_status_from_response(
                    response_json,
                    stage,
                    has_next_stage,
                )
                final_checkpoint = _build_checkpoint(
                    root=root,
                    run_manifest=run_manifest,
                    stage=stage,
                    stage_paths=stage_paths,
                    stage_status=final_stage_status,
                    resume_mode=ResumeMode.FRESH_SUBMIT,
                    token_preflight=token_preflight,
                    response_json=response_json,
                    review_bundle_path=consumed_review_bundle_path,
                    structured_output_written=structured_output_written,
                    sidecar_written=sidecar_written,
                    uploads_payload_path=uploads_payload_path,
                )
                artifacts.write_stage_checkpoint(stage_paths, final_checkpoint)
                _sync_stage_summary(
                    root=root,
                    run_manifest=run_manifest,
                    stage=stage,
                    stage_paths=stage_paths,
                    stage_status=final_stage_status,
                    response_json=response_json,
                    review_bundle_path=consumed_review_bundle_path,
                    token_preflight_path=token_preflight_path,
                )
                run_manifest["status"] = _run_status_after_stage(
                    stage_status=final_stage_status,
                    has_next_stage=has_next_stage,
                    stage=stage,
                )
                run_manifest["current_stage_id"] = stage.stage_id
                artifacts.write_run_manifest(run_dir, run_manifest)

                if (
                    final_stage_status == StageStatus.COMPLETED.value
                    and has_next_stage
                    and stage.gate == GateType.AUTO
                    and runtime.stage_id is None
                    and runtime.wait
                ):
                    current_stage = workflow.next_stage(stage.stage_id)
                    if current_stage is None:
                        break
                    continue
            return {
                "run_dir": relpath(root, run_dir),
                "run_manifest_path": relpath(root, artifacts.run_manifest_path(run_dir)),
                "status": run_manifest["status"],
                "stage_id": stage.stage_id,
            }


def _load_existing_workflow_for_run(root: Path, run_manifest: dict[str, Any]):
    return load_workflow_definition(
        run_manifest["workflow_manifest_path"],
        root=root,
    )


def _load_uploads_payload(stage_paths: dict[str, Path]) -> dict[str, Any] | None:
    if not stage_paths["uploads_json"].exists():
        return None
    return load_json(stage_paths["uploads_json"], "uploads payload")


def _merge_uploads_payloads(
    base_payload: dict[str, Any] | None,
    extra_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if base_payload is None:
        return extra_payload
    if extra_payload is None:
        return base_payload
    merged = dict(base_payload)
    merged["delete_uploaded_files_on_complete"] = bool(
        base_payload.get("delete_uploaded_files_on_complete")
        or extra_payload.get("delete_uploaded_files_on_complete")
    )
    if merged.get("file_expiration_policy") is None and extra_payload.get("file_expiration_policy") is not None:
        merged["file_expiration_policy"] = extra_payload.get("file_expiration_policy")
    merged["files"] = list(base_payload.get("files", [])) + list(extra_payload.get("files", []))
    return merged


def resume_stage(
    *,
    run_dir: str | Path,
    stage_id: str,
    wait: bool,
    poll_interval: float,
    max_wait_seconds: float | None,
    client: OpenAIClient,
    root: Path | None = None,
    refresh_status_only: bool = False,
) -> dict[str, Any]:
    """Resume or finalize a previously submitted stage from its stored response id."""

    root = root or repo_root()
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    run_manifest = artifacts.load_run_manifest(root, resolved_run_dir)
    workflow = _load_existing_workflow_for_run(root, run_manifest)
    stage = workflow.stage(stage_id)
    stage_paths = artifacts.build_stage_paths(resolved_run_dir, stage.stage_number, stage.stage_id)
    stage_summary = artifacts.find_stage_summary(run_manifest, stage_id)
    response_id = stage_summary.get("response_id")
    if not response_id:
        raise SystemExit(f"Stage {stage_id} has no stored response_id to resume.")
    response_json = client.retrieve_response(str(response_id))
    artifacts.write_response_latest(stage_paths, response_json)
    if wait and str(response_json.get("status")) not in TERMINAL_RESPONSE_STATUSES:
        response_json = client.wait_for_terminal_response(
            str(response_id),
            poll_interval=poll_interval,
            max_wait_seconds=max_wait_seconds,
            checkpoint_callback=lambda polled: artifacts.write_response_latest(stage_paths, polled),
        )
        artifacts.write_response_latest(stage_paths, response_json)
    uploads_payload = _load_uploads_payload(stage_paths)
    has_next_stage = workflow.next_stage(stage.stage_id) is not None
    structured_output_written = stage_paths["structured_output"].exists()
    sidecar_written = stage_paths["sidecar_response_json"].exists()
    if str(response_json.get("status")) in TERMINAL_RESPONSE_STATUSES:
        if (
            not refresh_status_only
            and uploads_payload
            and uploads_payload.get("delete_uploaded_files_on_complete")
        ):
            uploads_payload = attachments.cleanup_uploaded_files(
                client=client,
                uploads_payload=uploads_payload,
            )
            artifacts.write_uploads_payload(stage_paths, uploads_payload)
        if not refresh_status_only:
            structured_output_written, sidecar_written, uploads_payload = _write_stage_artifacts_for_response(
                root=root,
                client=client,
                workflow=workflow,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                runtime=RuntimeOptions(wait=wait),
                response_json=response_json,
                uploads_payload=uploads_payload,
            )
            if uploads_payload is not None:
                artifacts.write_uploads_payload(stage_paths, uploads_payload)
    token_preflight = {"status": "pending"}
    if stage_paths["token_preflight"].exists():
        token_payload = load_json(stage_paths["token_preflight"], "token preflight")
        token_preflight = {
            "status": "succeeded",
            "attempts": 1,
            "input_tokens": token_payload.get("input_tokens"),
            "diagnostics_path": relpath(root, stage_paths["token_preflight"]),
        }
    elif stage_paths["token_preflight_error"].exists():
        token_payload = load_json(stage_paths["token_preflight_error"], "token preflight error")
        token_preflight = {
            "status": token_payload.get("fallback_decision", "failed_closed"),
            "attempts": int(token_payload.get("attempts", 0)),
            "error_message": token_payload.get("error_message"),
            "diagnostics_path": relpath(root, stage_paths["token_preflight_error"]),
        }
    stage_status = _stage_status_from_response(response_json, stage, has_next_stage)
    checkpoint = _build_checkpoint(
        root=root,
        run_manifest=run_manifest,
        stage=stage,
        stage_paths=stage_paths,
        stage_status=stage_status,
        resume_mode=(
            ResumeMode.REFRESH_STATUS_ONLY if refresh_status_only else ResumeMode.RESUME_RESPONSE_ID
        ),
        token_preflight=token_preflight,
        response_json=response_json,
        review_bundle_path=stage_summary.get("review_bundle_path"),
        structured_output_written=structured_output_written,
        sidecar_written=sidecar_written,
        uploads_payload_path=stage_paths["uploads_json"] if stage_paths["uploads_json"].exists() else None,
    )
    artifacts.write_stage_checkpoint(stage_paths, checkpoint)
    _sync_stage_summary(
        root=root,
        run_manifest=run_manifest,
        stage=stage,
        stage_paths=stage_paths,
        stage_status=stage_status,
        response_json=response_json,
        review_bundle_path=stage_summary.get("review_bundle_path"),
        token_preflight_path=(
            stage_paths["token_preflight"]
            if stage_paths["token_preflight"].exists()
            else stage_paths["token_preflight_error"]
            if stage_paths["token_preflight_error"].exists()
            else None
        ),
    )
    run_manifest["status"] = _run_status_after_stage(
        stage_status=stage_status,
        has_next_stage=has_next_stage,
        stage=stage,
    )
    run_manifest["current_stage_id"] = stage_id
    artifacts.write_run_manifest(resolved_run_dir, run_manifest)
    return {
        "run_dir": relpath(root, resolved_run_dir),
        "run_manifest_path": relpath(root, artifacts.run_manifest_path(resolved_run_dir)),
        "status": run_manifest["status"],
        "stage_id": stage_id,
    }


def refresh_stage(
    *,
    run_dir: str | Path,
    stage_id: str,
    client: OpenAIClient,
    root: Path | None = None,
) -> dict[str, Any]:
    """Refresh remote status for a stage without performing local finalization."""

    return resume_stage(
        run_dir=run_dir,
        stage_id=stage_id,
        wait=False,
        poll_interval=0.0,
        max_wait_seconds=None,
        client=client,
        root=root,
        refresh_status_only=True,
    )
