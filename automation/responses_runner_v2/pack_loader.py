from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import (
    AttachmentEntry,
    CarryForwardConfig,
    FileUploadPolicy,
    GateType,
    INPUT_MANIFEST_SCHEMA_VERSION,
    ModelRole,
    ModelRoleProfile,
    OutputConfig,
    OutputSidecarConfig,
    RequestDefaults,
    ROLE_TO_FIELD,
    TokenPreflightPolicy,
    WORKFLOW_SCHEMA_VERSION,
    base_model_name,
    WorkflowDefinition,
    StageDefinition,
    load_json,
    read_text,
    repo_root,
    require_keys,
    resolve_under_root,
    validate_model_options,
)


def _resolve_asset_path(root: Path, base_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    raw = Path(value)
    resolved = raw if raw.is_absolute() else base_dir / raw
    return resolve_under_root(root, resolved, must_exist=True)


def _parse_model_role_profile(
    payload: dict[str, Any],
    *,
    override_model: str | None = None,
) -> ModelRoleProfile:
    require_keys(payload, ["model", "reasoning_effort", "verbosity"], "model role profile")
    model = override_model or str(payload["model"])
    reasoning_effort = str(payload["reasoning_effort"])
    verbosity = str(payload["verbosity"])
    prompt_cache_retention = payload.get("prompt_cache_retention")
    if prompt_cache_retention is not None:
        prompt_cache_retention = str(prompt_cache_retention)
    if base_model_name(model).startswith("gpt-5.5") and prompt_cache_retention != "24h":
        raise SystemExit(
            f"GPT-5.5-family model role {model!r} must explicitly set prompt_cache_retention=24h."
        )
    return ModelRoleProfile(
        model=model,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        prompt_cache_retention=prompt_cache_retention,
    )


def _parse_request_defaults(payload: dict[str, Any]) -> RequestDefaults:
    require_keys(
        payload,
        [
            "background",
            "store",
            "parallel_tool_calls",
            "max_tool_calls",
            "token_preflight",
            "file_uploads",
        ],
        "request defaults",
    )
    token_preflight_payload = payload["token_preflight"]
    if not isinstance(token_preflight_payload, dict):
        raise SystemExit("request.token_preflight must be an object.")
    file_uploads_payload = payload["file_uploads"]
    if not isinstance(file_uploads_payload, dict):
        raise SystemExit("request.file_uploads must be an object.")
    token_preflight = TokenPreflightPolicy(
        enabled=bool(token_preflight_payload["enabled"]),
        max_retries=int(token_preflight_payload["max_retries"]),
        retryable_http_status_codes=tuple(
            int(code) for code in token_preflight_payload["retryable_http_status_codes"]
        ),
        on_retryable_service_failure=str(
            token_preflight_payload["on_retryable_service_failure"]
        ),
    )
    file_uploads = FileUploadPolicy(
        purpose=str(file_uploads_payload["purpose"]),
        delete_on_completion=bool(file_uploads_payload["delete_on_completion"]),
        expires_after_seconds=(
            int(file_uploads_payload["expires_after_seconds"])
            if file_uploads_payload.get("expires_after_seconds") is not None
            else None
        ),
    )
    background = bool(payload["background"])
    store = bool(payload["store"])
    if background and not store:
        raise SystemExit("Workflow request defaults cannot set background=true with store=false.")
    return RequestDefaults(
        background=background,
        store=store,
        parallel_tool_calls=bool(payload["parallel_tool_calls"]),
        max_tool_calls=int(payload["max_tool_calls"]),
        temperature=(
            float(payload["temperature"]) if payload.get("temperature") is not None else None
        ),
        service_tier=(
            str(payload["service_tier"]) if payload.get("service_tier") is not None else None
        ),
        safety_identifier=(
            str(payload["safety_identifier"])
            if payload.get("safety_identifier") is not None
            else None
        ),
        token_preflight=token_preflight,
        file_uploads=file_uploads,
    )


def _parse_output_config(
    root: Path,
    base_dir: Path,
    payload: dict[str, Any],
    model_role: ModelRole,
) -> OutputConfig:
    require_keys(payload, ["primary_format"], "stage output config")
    primary_format = str(payload["primary_format"])
    schema_file = payload.get("schema_file")
    schema_name = payload.get("schema_name")
    schema_path = _resolve_asset_path(root, base_dir, str(schema_file)) if schema_file else None
    sidecar = None
    if primary_format == "json_schema" and model_role != ModelRole.STRUCTURAL_PROCESSING:
        raise SystemExit(
            "Direct json_schema stages must use model_role=structural_processing in v2."
        )
    sidecar_payload = payload.get("sidecar")
    if sidecar_payload is not None:
        if not isinstance(sidecar_payload, dict):
            raise SystemExit("stage.output.sidecar must be an object.")
        require_keys(sidecar_payload, ["schema_file", "schema_name"], "stage output sidecar")
        sidecar = OutputSidecarConfig(
            schema_file=str(sidecar_payload["schema_file"]),
            schema_name=str(sidecar_payload["schema_name"]),
            schema_path=_resolve_asset_path(
                root,
                base_dir,
                str(sidecar_payload["schema_file"]),
            ),
        )
    return OutputConfig(
        primary_format=primary_format,
        schema_file=str(schema_file) if schema_file is not None else None,
        schema_name=str(schema_name) if schema_name is not None else None,
        schema_path=schema_path,
        sidecar=sidecar,
    )


def _parse_stage(
    root: Path,
    base_dir: Path,
    payload: dict[str, Any],
) -> StageDefinition:
    require_keys(
        payload,
        [
            "stage_id",
            "stage_number",
            "title",
            "task_file",
            "input_manifest_file",
            "model_role",
            "gate",
            "output",
        ],
        "stage definition",
    )
    model_role = ModelRole(str(payload["model_role"]))
    carry_forward_payload = payload.get("carry_forward") or {}
    if not isinstance(carry_forward_payload, dict):
        raise SystemExit("stage.carry_forward must be an object when present.")
    carry_forward = CarryForwardConfig(
        reference_context_from_stage_ids=tuple(
            str(item)
            for item in carry_forward_payload.get("reference_context_from_stage_ids", [])
        ),
        review_bundle_from_stage_id=(
            str(carry_forward_payload["review_bundle_from_stage_id"])
            if carry_forward_payload.get("review_bundle_from_stage_id") is not None
            else None
        ),
        review_bundle_include_response_artifact_json=bool(
            carry_forward_payload.get("review_bundle_include_response_artifact_json", True)
        ),
    )
    output = _parse_output_config(root, base_dir, payload["output"], model_role)
    return StageDefinition(
        stage_id=str(payload["stage_id"]),
        stage_number=int(payload["stage_number"]),
        title=str(payload["title"]),
        task_file=str(payload["task_file"]),
        task_path=_resolve_asset_path(root, base_dir, str(payload["task_file"])),
        stage_instructions_file=(
            str(payload["stage_instructions_file"])
            if payload.get("stage_instructions_file") is not None
            else None
        ),
        stage_instructions_path=(
            _resolve_asset_path(root, base_dir, str(payload["stage_instructions_file"]))
            if payload.get("stage_instructions_file") is not None
            else None
        ),
        input_manifest_file=str(payload["input_manifest_file"]),
        input_manifest_path=_resolve_asset_path(root, base_dir, str(payload["input_manifest_file"])),
        tool_profile_file=(
            str(payload["tool_profile_file"])
            if payload.get("tool_profile_file") is not None
            else None
        ),
        tool_profile_path=(
            _resolve_asset_path(root, base_dir, str(payload["tool_profile_file"]))
            if payload.get("tool_profile_file") is not None
            else None
        ),
        model_role=model_role,
        reasoning_effort=(
            str(payload["reasoning_effort"])
            if payload.get("reasoning_effort") is not None
            else None
        ),
        verbosity=str(payload["verbosity"]) if payload.get("verbosity") is not None else None,
        max_input_tokens=(
            int(payload["max_input_tokens"])
            if payload.get("max_input_tokens") is not None
            else None
        ),
        max_output_tokens=(
            int(payload["max_output_tokens"])
            if payload.get("max_output_tokens") is not None
            else None
        ),
        gate=GateType(str(payload["gate"])),
        carry_forward=carry_forward,
        output=output,
    )


def load_workflow_definition(
    workflow_file: str | Path,
    *,
    root: Path | None = None,
    primary_model_override: str | None = None,
    structural_model_override: str | None = None,
) -> WorkflowDefinition:
    root = root or repo_root()
    workflow_path = resolve_under_root(root, workflow_file, must_exist=True)
    payload = load_json(workflow_path, "workflow manifest")
    if payload.get("schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise SystemExit(
            f"Unexpected workflow schema_version in {workflow_path}: {payload.get('schema_version')!r}"
        )
    require_keys(
        payload,
        [
            "schema_version",
            "workflow_id",
            "workflow_mode",
            "description",
            "shared_instructions_file",
            "defaults",
            "stages",
        ],
        "workflow manifest",
    )
    defaults_payload = payload["defaults"]
    if not isinstance(defaults_payload, dict):
        raise SystemExit("workflow.defaults must be an object.")
    model_roles_payload = defaults_payload.get("model_roles")
    if not isinstance(model_roles_payload, dict):
        raise SystemExit("workflow.defaults.model_roles must be an object.")
    request_payload = defaults_payload.get("request")
    if not isinstance(request_payload, dict):
        raise SystemExit("workflow.defaults.request must be an object.")

    model_roles = {
        "primary_generation": _parse_model_role_profile(
            model_roles_payload["primary_generation"],
            override_model=primary_model_override,
        ),
        "structural_processing": _parse_model_role_profile(
            model_roles_payload["structural_processing"],
            override_model=structural_model_override,
        ),
    }
    request_defaults = _parse_request_defaults(request_payload)
    base_dir = workflow_path.parent
    stages_payload = payload["stages"]
    if not isinstance(stages_payload, list) or not stages_payload:
        raise SystemExit("workflow.stages must be a non-empty list.")
    stages = tuple(_parse_stage(root, base_dir, item) for item in stages_payload)
    stage_ids = {stage.stage_id for stage in stages}
    if len(stage_ids) != len(stages):
        raise SystemExit("workflow stages must have unique stage_id values.")
    stage_numbers = [stage.stage_number for stage in stages]
    if len(set(stage_numbers)) != len(stage_numbers):
        raise SystemExit("workflow stages must have unique stage_number values.")
    if stage_numbers != sorted(stage_numbers):
        raise SystemExit("workflow stages must be ordered by ascending stage_number.")

    workflow_mode = str(payload["workflow_mode"])
    if workflow_mode == "one_pass" and len(stages) != 1:
        raise SystemExit("workflow_mode=one_pass requires exactly one stage.")
    if workflow_mode == "two_pass" and len(stages) != 2:
        raise SystemExit("workflow_mode=two_pass requires exactly two stages.")
    if workflow_mode == "reviewed_three_stage" and len(stages) != 3:
        raise SystemExit("workflow_mode=reviewed_three_stage requires exactly three stages.")

    for stage in stages:
        for source_stage_id in stage.carry_forward.reference_context_from_stage_ids:
            if source_stage_id not in stage_ids:
                raise SystemExit(
                    f"stage {stage.stage_id} references unknown carry-forward stage {source_stage_id!r}"
                )
        source_bundle_stage = stage.carry_forward.review_bundle_from_stage_id
        if source_bundle_stage is not None and source_bundle_stage not in stage_ids:
            raise SystemExit(
                f"stage {stage.stage_id} references unknown review-bundle stage {source_bundle_stage!r}"
            )
        role_profile = model_roles[stage.model_role.value]
        validate_model_options(
            model=role_profile.model,
            max_output_tokens=stage.max_output_tokens or 128000,
            prompt_cache_retention=role_profile.prompt_cache_retention,
            text_format=stage.output.primary_format,
        )

    operator_requirements = payload.get("operator_requirements") or {}
    if not isinstance(operator_requirements, dict):
        raise SystemExit("workflow.operator_requirements must be an object when present.")

    return WorkflowDefinition(
        schema_version=str(payload["schema_version"]),
        workflow_id=str(payload["workflow_id"]),
        workflow_name=str(payload.get("workflow_name") or payload["workflow_id"]),
        workflow_mode=workflow_mode,
        description=str(payload["description"]),
        workflow_file=workflow_path,
        shared_instructions_file=str(payload["shared_instructions_file"]),
        shared_instructions_path=_resolve_asset_path(
            root,
            base_dir,
            str(payload["shared_instructions_file"]),
        ),
        operator_requirements=operator_requirements,
        model_roles=model_roles,
        request_defaults=request_defaults,
        stages=stages,
    )


def validate_operator_inputs(
    workflow: WorkflowDefinition,
    *,
    primary_job_inputs: list[str],
    reference_context: list[str],
) -> None:
    minimum = workflow.operator_requirements.get("minimum_primary_job_inputs")
    maximum = workflow.operator_requirements.get("maximum_primary_job_inputs")
    allow_reference_context = workflow.operator_requirements.get("allow_reference_context", True)
    expected_primary_job_input_paths = workflow.operator_requirements.get("expected_primary_job_input_paths")
    if minimum is not None and len(primary_job_inputs) < int(minimum):
        raise SystemExit(
            f"workflow requires at least {minimum} primary job input(s), got {len(primary_job_inputs)}."
        )
    if maximum is not None and len(primary_job_inputs) > int(maximum):
        raise SystemExit(
            f"workflow allows at most {maximum} primary job input(s), got {len(primary_job_inputs)}."
        )
    if expected_primary_job_input_paths is not None:
        expected = [Path(str(path)).as_posix() for path in expected_primary_job_input_paths]
        received = [Path(str(path)).as_posix() for path in primary_job_inputs]
        if received != expected:
            raise SystemExit(
                "workflow requires exact primary job input path(s): "
                + ", ".join(expected)
                + f"; got: {', '.join(received) if received else '<none>'}."
            )
    if not bool(allow_reference_context) and reference_context:
        raise SystemExit("workflow does not allow operator-supplied reference context.")


def load_input_manifest(
    input_manifest_file: str | Path,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or repo_root()
    manifest_path = resolve_under_root(root, input_manifest_file, must_exist=True)
    payload = load_json(manifest_path, "input manifest")
    if payload.get("schema_version") != INPUT_MANIFEST_SCHEMA_VERSION:
        raise SystemExit(
            f"Unexpected input manifest schema_version in {manifest_path}: {payload.get('schema_version')!r}"
        )
    require_keys(
        payload,
        [
            "schema_version",
            "manifest_id",
            "primary_job_inputs",
            "reviewed_handoff_inputs",
            "attached_repository_files",
            "reference_context",
        ],
        "input manifest",
    )

    def parse_entries(raw_value: object, label: str) -> list[AttachmentEntry]:
        if not isinstance(raw_value, list):
            raise SystemExit(f"{label} must be a list.")
        entries: list[AttachmentEntry] = []
        for item in raw_value:
            if not isinstance(item, dict):
                raise SystemExit(f"{label} entries must be objects.")
            require_keys(item, ["path", "kind"], label)
            entries.append(
                AttachmentEntry(
                    path=str(item["path"]),
                    kind=str(item["kind"]),
                    required=bool(item.get("required", True)),
                    exclude_globs=tuple(str(glob) for glob in item.get("exclude_globs", [])),
                    notes=str(item["notes"]) if item.get("notes") is not None else None,
                )
            )
        return entries

    return {
        "schema_version": payload["schema_version"],
        "manifest_id": payload["manifest_id"],
        "workflow_id": payload.get("workflow_id"),
        "stage_id": payload.get("stage_id"),
        "description": payload.get("description"),
        "primary_job_inputs": parse_entries(payload["primary_job_inputs"], "primary_job_inputs"),
        "reviewed_handoff_inputs": parse_entries(
            payload["reviewed_handoff_inputs"],
            "reviewed_handoff_inputs",
        ),
        "attached_repository_files": parse_entries(
            payload["attached_repository_files"],
            "attached_repository_files",
        ),
        "reference_context": parse_entries(payload["reference_context"], "reference_context"),
    }


def normalize_tool(tool: object) -> object:
    if not isinstance(tool, dict):
        return tool
    normalized = dict(tool)
    tool_type = normalized.get("type")
    domains = normalized.pop("domains", None)
    if tool_type in {"web_search", "web_search_preview"}:
        normalized["type"] = "web_search"
    if normalized.get("type") == "web_search" and isinstance(domains, list) and domains:
        filters = dict(normalized.get("filters", {})) if isinstance(normalized.get("filters"), dict) else {}
        existing = filters.get("allowed_domains")
        merged = []
        if isinstance(existing, list):
            merged.extend(str(item) for item in existing)
        merged.extend(str(item) for item in domains)
        filters["allowed_domains"] = list(dict.fromkeys(merged))
        normalized["filters"] = filters
    return normalized


def load_tool_profile(tool_profile_file: str | Path | None, *, root: Path | None = None) -> dict[str, Any]:
    if tool_profile_file is None:
        return {}
    root = root or repo_root()
    path = resolve_under_root(root, tool_profile_file, must_exist=True)
    raw_text = read_text(path, "tool profile")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid tool profile JSON: {path}: {exc}") from exc
    if isinstance(payload, list):
        tools = [normalize_tool(item) for item in payload]
        return {"tools": [tool for tool in tools if tool]}
    if not isinstance(payload, dict):
        raise SystemExit("tool profile must be a JSON object or array.")
    normalized = dict(payload)
    if isinstance(normalized.get("tools"), list):
        normalized["tools"] = [normalize_tool(item) for item in normalized["tools"] if item]
        if not normalized["tools"]:
            normalized.pop("tools")
    return normalized


def load_schema_json(schema_file: str | Path, *, root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    path = resolve_under_root(root, schema_file, must_exist=True)
    return load_json(path, "schema file")


def load_text_asset(path: Path) -> str:
    return read_text(path, f"text asset {path.name}")
