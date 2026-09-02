from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import (
    ASSURANCE_PROFILES,
    AttachmentEntry,
    CarryForwardConfig,
    FileUploadPolicy,
    GateType,
    INPUT_MANIFEST_SCHEMA_VERSION,
    ModelRole,
    ModelRoleProfile,
    OutputConfig,
    PostOutputValidator,
    RequestDefaults,
    ROLE_TO_FIELD,
    RuntimeInputBinding,
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
    ReviewConfig,
    REVIEWERS,
    REVIEW_EFFORTS,
)
from .schema_validation import (
    persisted_schema_filename,
    validate_contract,
    workflow_schema_filename,
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
    reasoning_mode = payload.get("reasoning_mode")
    prompt_cache_mode = payload.get("prompt_cache_mode")
    prompt_cache_ttl = payload.get("prompt_cache_ttl")
    if base_model_name(model).startswith("gpt-5.5") and prompt_cache_retention != "24h":
        raise SystemExit(
            f"GPT-5.5-family model role {model!r} must explicitly set prompt_cache_retention=24h."
        )
    if base_model_name(model).startswith("gpt-5.6"):
        if prompt_cache_retention is not None:
            raise SystemExit(
                f"GPT-5.6-family model role {model!r} must not use prompt_cache_retention."
            )
        if prompt_cache_mode not in {None, "implicit", "explicit"}:
            raise SystemExit("prompt_cache_mode must be implicit or explicit.")
        if prompt_cache_ttl not in {None, "30m"}:
            raise SystemExit("GPT-5.6 currently supports prompt_cache_ttl=30m only.")
    return ModelRoleProfile(
        model=model,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        reasoning_mode=str(reasoning_mode) if reasoning_mode is not None else None,
        prompt_cache_mode=(str(prompt_cache_mode) if prompt_cache_mode is not None else None),
        prompt_cache_ttl=(str(prompt_cache_ttl) if prompt_cache_ttl is not None else None),
        prompt_cache_retention=prompt_cache_retention,
    )


def _parse_review_config(
    payload: dict[str, Any] | None,
    *,
    base: ReviewConfig | None = None,
) -> ReviewConfig:
    """Parse a workflow-level or stage-level `review` block over optional defaults."""

    base = base or ReviewConfig()
    payload = payload or {}
    if not isinstance(payload, dict):
        raise SystemExit("review configuration must be an object when present.")
    reviewer = str(payload.get("reviewer", base.reviewer))
    if reviewer not in REVIEWERS:
        raise SystemExit(f"review.reviewer must be one of {REVIEWERS}; got {reviewer!r}.")
    effort = payload.get("effort", base.effort)
    if effort is not None and str(effort) not in REVIEW_EFFORTS:
        raise SystemExit(f"review.effort must be one of {REVIEW_EFFORTS}; got {effort!r}.")
    timeout_seconds = int(payload.get("timeout_seconds", base.timeout_seconds))
    if timeout_seconds <= 0:
        raise SystemExit("review.timeout_seconds must be positive.")
    max_revisions = int(payload.get("max_revisions", base.max_revisions))
    if max_revisions < 0:
        raise SystemExit("review.max_revisions must be zero or more.")
    model = payload.get("model", base.model)
    return ReviewConfig(
        reviewer=reviewer,
        model=str(model) if model is not None else None,
        effort=str(effort) if effort is not None else None,
        timeout_seconds=timeout_seconds,
        max_revisions=max_revisions,
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
    if primary_format == "json_schema" and model_role != ModelRole.STRUCTURAL_PROCESSING:
        raise SystemExit(
            "Direct json_schema stages must use model_role=structural_processing in v2."
        )
    if payload.get("sidecar") is not None:
        raise SystemExit(
            "stage.output.sidecar is no longer supported; request structured output from the "
            "primary stage with primary_format=json_schema or post-process artifact.md."
        )
    return OutputConfig(
        primary_format=primary_format,
        schema_file=str(schema_file) if schema_file is not None else None,
        schema_name=str(schema_name) if schema_name is not None else None,
        schema_path=schema_path,
    )


def _parse_stage(
    root: Path,
    base_dir: Path,
    payload: dict[str, Any],
    *,
    legacy_v1_defaults: bool = False,
    review_defaults: ReviewConfig | None = None,
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
    handoff_source = carry_forward_payload.get("handoff_from_stage_id")
    legacy_source = carry_forward_payload.get("review_bundle_from_stage_id")
    if handoff_source is not None and legacy_source is not None and handoff_source != legacy_source:
        raise SystemExit(
            "stage.carry_forward names different handoff_from_stage_id and legacy "
            "review_bundle_from_stage_id values; keep only handoff_from_stage_id."
        )
    if handoff_source is None and legacy_source is not None:
        # Legacy review bundles are gone; the bundle source stage becomes the handoff source.
        handoff_source = legacy_source
    carry_forward = CarryForwardConfig(
        reference_context_from_stage_ids=tuple(
            str(item)
            for item in carry_forward_payload.get("reference_context_from_stage_ids", [])
        ),
        handoff_from_stage_id=str(handoff_source) if handoff_source is not None else None,
    )
    stage_review = (
        _parse_review_config(payload["review"], base=review_defaults)
        if payload.get("review") is not None
        else None
    )
    validator_payloads = payload.get("post_output_validators", [])
    post_output_validators = tuple(
        PostOutputValidator(
            validator_id=str(item["validator_id"]),
            gate=str(item.get("gate", "blocking")),
            timeout_seconds=float(item.get("timeout_seconds", 10.0)),
        )
        for item in validator_payloads
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
        # `review_required` was the manual review-bundle gate; it now means "stop for a human".
        gate=GateType("human" if str(payload["gate"]) == "review_required" else str(payload["gate"])),
        carry_forward=carry_forward,
        output=output,
        post_output_validators=post_output_validators,
        citation_policy=dict(payload.get("citation_policy", {})),
        review=stage_review,
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
    validate_contract(
        payload,
        workflow_schema_filename(payload.get("schema_version")),
        label=f"workflow manifest {workflow_path}",
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
    review_defaults = _parse_review_config(defaults_payload.get("review"))
    base_dir = workflow_path.parent
    stages_payload = payload["stages"]
    if not isinstance(stages_payload, list) or not stages_payload:
        raise SystemExit("workflow.stages must be a non-empty list.")
    stages = tuple(
        _parse_stage(
            root,
            base_dir,
            item,
            legacy_v1_defaults=(
                payload.get("schema_version") == "responses_runner_v2.workflow_manifest.v1"
            ),
            review_defaults=review_defaults,
        )
        for item in stages_payload
    )
    stage_ids = {stage.stage_id for stage in stages}
    if len(stage_ids) != len(stages):
        raise SystemExit("workflow stages must have unique stage_id values.")
    stage_numbers = [stage.stage_number for stage in stages]
    if len(set(stage_numbers)) != len(stage_numbers):
        raise SystemExit("workflow stages must have unique stage_number values.")
    expected_stage_numbers = list(range(1, len(stages) + 1))
    if stage_numbers != expected_stage_numbers:
        raise SystemExit(
            f"workflow stage_number values must be exactly 1..N in order; got {stage_numbers}."
        )

    workflow_mode = str(payload["workflow_mode"])
    if workflow_mode == "one_pass" and len(stages) != 1:
        raise SystemExit("workflow_mode=one_pass requires exactly one stage.")
    if workflow_mode == "two_pass" and len(stages) != 2:
        raise SystemExit("workflow_mode=two_pass requires exactly two stages.")
    if workflow_mode == "reviewed_three_stage" and len(stages) != 3:
        raise SystemExit("workflow_mode=reviewed_three_stage requires exactly three stages.")

    stage_number_by_id = {stage.stage_id: stage.stage_number for stage in stages}
    for stage in stages:
        for source_stage_id in stage.carry_forward.reference_context_from_stage_ids:
            if source_stage_id not in stage_ids:
                raise SystemExit(
                    f"stage {stage.stage_id} references unknown carry-forward stage {source_stage_id!r}"
                )
            if stage_number_by_id[source_stage_id] >= stage.stage_number:
                raise SystemExit(
                    f"stage {stage.stage_id} carry-forward dependency {source_stage_id!r} must point backward."
                )
        handoff_source = stage.carry_forward.handoff_from_stage_id
        if handoff_source is not None:
            if handoff_source not in stage_ids:
                raise SystemExit(
                    f"stage {stage.stage_id} references unknown handoff stage {handoff_source!r}"
                )
            if stage_number_by_id[handoff_source] >= stage.stage_number:
                raise SystemExit(
                    f"stage {stage.stage_id} handoff dependency {handoff_source!r} must point backward."
                )
            source_gate = next(item.gate for item in stages if item.stage_id == handoff_source)
            if source_gate not in {GateType.REVIEWED, GateType.HUMAN}:
                raise SystemExit(
                    f"stage {stage.stage_id} handoff source {handoff_source!r} must use a "
                    "`reviewed` or `human` gate."
                )
        role_profile = model_roles[stage.model_role.value]
        validate_model_options(
            model=role_profile.model,
            max_output_tokens=stage.max_output_tokens or 128000,
            prompt_cache_retention=role_profile.prompt_cache_retention,
            prompt_cache_ttl=role_profile.prompt_cache_ttl,
            reasoning_mode=role_profile.reasoning_mode,
            text_format=stage.output.primary_format,
        )

        static_manifest = load_input_manifest(stage.input_manifest_path, root=root)
        if payload.get("schema_version") == WORKFLOW_SCHEMA_VERSION:
            if static_manifest.get("workflow_id") not in {None, str(payload["workflow_id"])}:
                raise SystemExit(
                    f"input manifest for stage {stage.stage_id} has workflow_id "
                    f"{static_manifest.get('workflow_id')!r}, expected {payload['workflow_id']!r}."
                )
            if static_manifest.get("stage_id") not in {None, stage.stage_id}:
                raise SystemExit(
                    f"input manifest for stage {stage.stage_id} has stage_id "
                    f"{static_manifest.get('stage_id')!r}."
                )

    operator_requirements = payload.get("operator_requirements") or {}
    if not isinstance(operator_requirements, dict):
        raise SystemExit("workflow.operator_requirements must be an object when present.")

    assurance_profile = str(payload.get("assurance_profile", "critical"))
    if assurance_profile not in ASSURANCE_PROFILES:
        raise SystemExit(f"Unknown assurance_profile {assurance_profile!r}.")
    data_policy = ASSURANCE_PROFILES[assurance_profile]["data_handling"]
    if request_defaults.store and not data_policy["api_store_allowed"]:
        raise SystemExit(
            f"assurance_profile={assurance_profile} does not allow API store=true."
        )
    if request_defaults.file_uploads.purpose != data_policy["file_purpose"]:
        raise SystemExit(
            f"assurance_profile={assurance_profile} requires file purpose "
            f"{data_policy['file_purpose']!r}."
        )
    if (
        data_policy["delete_uploaded_files_on_complete"]
        and not request_defaults.file_uploads.delete_on_completion
    ):
        raise SystemExit(
            f"assurance_profile={assurance_profile} requires uploaded-file deletion on completion."
        )
    if (
        payload.get("schema_version") == WORKFLOW_SCHEMA_VERSION
        and ASSURANCE_PROFILES[assurance_profile]["require_input_budget"]
    ):
        missing_budgets = [stage.stage_id for stage in stages if stage.max_input_tokens is None]
        if missing_budgets:
            raise SystemExit(
                f"assurance_profile={assurance_profile} requires max_input_tokens for every stage; "
                f"missing: {', '.join(missing_budgets)}."
            )

    return WorkflowDefinition(
        schema_version=str(payload["schema_version"]),
        workflow_id=str(payload["workflow_id"]),
        workflow_name=str(payload.get("workflow_name") or payload["workflow_id"]),
        workflow_mode=workflow_mode,
        description=str(payload["description"]),
        assurance_profile=assurance_profile,
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
        review_defaults=review_defaults,
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
    validate_contract(
        payload,
        persisted_schema_filename("input_manifest", payload.get("schema_version")),
        label=f"input manifest {manifest_path}",
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


def load_runtime_input_bindings(
    binding_file: str | Path,
    *,
    workflow: WorkflowDefinition,
    root: Path | None = None,
) -> list[RuntimeInputBinding]:
    root = root or repo_root()
    path = resolve_under_root(root, binding_file, must_exist=True)
    payload = load_json(path, "runtime input bindings")
    validate_contract(
        payload,
        "runtime_input_bindings.schema.json",
        label=f"runtime input bindings {path}",
    )
    known_stages = {stage.stage_id for stage in workflow.stages}
    bindings: list[RuntimeInputBinding] = []
    seen_ids: set[str] = set()
    for raw in payload["bindings"]:
        binding_id = str(raw["binding_id"])
        if binding_id in seen_ids:
            raise SystemExit(f"Duplicate runtime binding_id {binding_id!r}.")
        seen_ids.add(binding_id)
        scope = raw["scope"]
        stage_ids = tuple(str(item) for item in scope.get("stage_ids", ()))
        unknown = sorted(set(stage_ids) - known_stages)
        if unknown:
            raise SystemExit(
                f"Runtime binding {binding_id!r} references unknown stages: {', '.join(unknown)}."
            )
        if scope["type"] == "workflow":
            stage_ids = ()
        resolve_under_root(root, str(raw["path"]), must_exist=True)
        bindings.append(
            RuntimeInputBinding(
                binding_id=binding_id,
                path=str(raw["path"]),
                authority=str(raw["authority"]),
                stage_ids=stage_ids,
            )
        )
    return bindings


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
