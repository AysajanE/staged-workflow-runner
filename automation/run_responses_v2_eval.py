#!/usr/bin/env python3
"""Local eval harness for Responses Runner v2 artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent.parent
EVAL_RESULT_SCHEMA_VERSION = "responses_runner_v2.eval_result.v1"
FREEZE_GATE_SCHEMA_VERSION = "responses_runner_v2.eval_freeze_gate.v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _resolve_path(raw_path: str, *, relative_to: Path) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else relative_to / path


def load_eval_dataset(path: str | Path) -> dict[str, Any]:
    dataset_path = Path(path)
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Eval dataset must be a JSON object: {dataset_path}")
    required = {"workflow", "supported_checks", "cases"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"Eval dataset missing keys {sorted(missing)}: {dataset_path}")
    if not isinstance(payload["cases"], list) or not payload["cases"]:
        raise ValueError(f"Eval dataset must define cases: {dataset_path}")
    if not isinstance(payload["workflow"], str) or not payload["workflow"]:
        raise ValueError(f"Eval dataset workflow must be a non-empty string: {dataset_path}")
    supported_checks = payload["supported_checks"]
    if (
        not isinstance(supported_checks, list)
        or not supported_checks
        or any(not isinstance(check_id, str) for check_id in supported_checks)
        or len(supported_checks) != len(set(supported_checks))
        or any(check_id not in CHECK_HANDLERS for check_id in supported_checks)
    ):
        raise ValueError(f"Eval dataset supported_checks is invalid: {dataset_path}")
    case_ids: list[str] = []
    for case in payload["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError(f"Eval dataset case is invalid: {dataset_path}")
        checks = case.get("checks")
        if (
            not isinstance(checks, list)
            or not checks
            or any(not isinstance(check_id, str) for check_id in checks)
            or any(check_id not in supported_checks for check_id in checks)
        ):
            raise ValueError(f"Eval dataset case {case['id']} has invalid checks: {dataset_path}")
        fixture = case.get("fixture")
        if fixture is not None:
            fixture_sha256 = case.get("fixture_sha256")
            if not isinstance(fixture, str) or not fixture or not _is_sha256(fixture_sha256):
                raise ValueError(f"Eval dataset case {case['id']} has an invalid fixture binding: {dataset_path}")
            fixture_path = _resolve_path(fixture, relative_to=dataset_path.parent).resolve()
            if not fixture_path.is_relative_to(dataset_path.parent.resolve()):
                raise ValueError(f"Eval dataset case {case['id']} fixture escapes the dataset directory: {dataset_path}")
            if not fixture_path.is_file() or _sha256_file(fixture_path) != fixture_sha256:
                raise ValueError(f"Eval dataset case {case['id']} fixture hash mismatch: {fixture_path}")
            try:
                fixture_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise ValueError(f"Eval dataset case {case['id']} fixture is invalid: {fixture_path}: {exc}") from exc
            if (
                not isinstance(fixture_payload, dict)
                or fixture_payload.get("schema_version") != "responses_runner_v2.representative_eval_fixture.v1"
                or fixture_payload.get("case_id") != case["id"]
                or fixture_payload.get("task_type") != case.get("representative_task_type")
            ):
                raise ValueError(f"Eval dataset case {case['id']} fixture identity mismatch: {fixture_path}")
            contract = fixture_payload.get("expected_output_contract")
            gold_output = fixture_payload.get("gold_output")
            required_keys = contract.get("required_keys") if isinstance(contract, dict) else None
            if (
                not isinstance(required_keys, list)
                or not required_keys
                or not isinstance(gold_output, dict)
                or any(key not in gold_output for key in required_keys)
            ):
                raise ValueError(f"Eval dataset case {case['id']} gold contract is invalid: {fixture_path}")
            candidate = case.get("candidate")
            candidate_sha256 = case.get("candidate_sha256")
            if not isinstance(candidate, str) or not candidate or not _is_sha256(candidate_sha256):
                raise ValueError(f"Eval dataset case {case['id']} has an invalid candidate binding: {dataset_path}")
            candidate_path = _resolve_path(candidate, relative_to=dataset_path.parent).resolve()
            if not candidate_path.is_relative_to(dataset_path.parent.resolve()):
                raise ValueError(f"Eval dataset case {case['id']} candidate escapes the dataset directory: {dataset_path}")
            if not candidate_path.is_file() or _sha256_file(candidate_path) != candidate_sha256:
                raise ValueError(f"Eval dataset case {case['id']} candidate hash mismatch: {candidate_path}")
            try:
                candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise ValueError(f"Eval dataset case {case['id']} candidate is invalid: {candidate_path}: {exc}") from exc
            if (
                not isinstance(candidate_payload, dict)
                or candidate_payload.get("schema_version") != "responses_runner_v2.representative_eval_candidate.v1"
                or candidate_payload.get("case_id") != case["id"]
                or candidate_payload.get("task_type") != case.get("representative_task_type")
            ):
                raise ValueError(f"Eval dataset case {case['id']} candidate identity mismatch: {candidate_path}")
        case_ids.append(case["id"])
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"Eval dataset case ids must be unique: {dataset_path}")
    payload["_dataset_path"] = str(dataset_path.resolve())
    return payload


def _path_get(payload: dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _make_result(check_id: str, passed: bool, details: str) -> dict[str, Any]:
    return {"id": check_id, "passed": passed, "details": details}


def check_json_required_keys(case: dict[str, Any], artifact_json: dict[str, Any]) -> dict[str, Any]:
    required = case.get("required_keys", [])
    missing = [key for key in required if key not in artifact_json]
    if missing:
        return _make_result("json_required_keys", False, f"Missing keys: {', '.join(missing)}")
    return _make_result("json_required_keys", True, f"Found {len(required)} required keys.")


def check_json_array_min_length(case: dict[str, Any], artifact_json: dict[str, Any]) -> dict[str, Any]:
    array_value = _path_get(artifact_json, str(case.get("array_path")))
    minimum = int(case.get("minimum_length", 0))
    if not isinstance(array_value, list):
        return _make_result("json_array_min_length", False, "Target path did not resolve to a list.")
    if len(array_value) < minimum:
        return _make_result(
            "json_array_min_length",
            False,
            f"List length {len(array_value)} is smaller than required minimum {minimum}.",
        )
    return _make_result(
        "json_array_min_length",
        True,
        f"List length {len(array_value)} meets minimum {minimum}.",
    )


def check_json_path_equals(case: dict[str, Any], artifact_json: dict[str, Any]) -> dict[str, Any]:
    path = str(case.get("json_path"))
    expected = case.get("expected_value")
    actual = _path_get(artifact_json, path)
    if actual != expected:
        return _make_result(
            "json_path_equals",
            False,
            f"Path {path!r} resolved to {actual!r}, expected {expected!r}.",
        )
    return _make_result("json_path_equals", True, f"Path {path!r} matched expected value.")


def check_structured_required_keys(case: dict[str, Any], structured_output: Any | None) -> dict[str, Any]:
    required = case.get("structured_required_keys", [])
    if not isinstance(structured_output, dict):
        return _make_result(
            "structured_required_keys",
            False,
            "Structured artifact JSON object was not provided.",
        )
    missing = [key for key in required if key not in structured_output]
    if missing:
        return _make_result(
            "structured_required_keys",
            False,
            f"Missing structured keys: {', '.join(missing)}",
        )
    return _make_result(
        "structured_required_keys",
        True,
        f"Found {len(required)} structured keys.",
    )


def check_text_required_substrings(case: dict[str, Any], artifact_text: str) -> dict[str, Any]:
    required = case.get("required_substrings", [])
    missing = [item for item in required if item not in artifact_text]
    if missing:
        return _make_result(
            "text_required_substrings",
            False,
            f"Missing required substrings: {', '.join(missing)}",
        )
    return _make_result(
        "text_required_substrings",
        True,
        f"Found {len(required)} required substrings.",
    )


def check_expected_output_contract(case: dict[str, Any], artifact_json: dict[str, Any]) -> dict[str, Any]:
    contract = artifact_json.get("expected_output_contract")
    output = artifact_json.get("expected_output")
    required = contract.get("required_keys") if isinstance(contract, dict) else None
    if (
        not isinstance(output, dict)
        or not isinstance(required, list)
        or not required
        or any(not isinstance(key, str) or not key for key in required)
        or len(required) != len(set(required))
    ):
        return _make_result("expected_output_contract", False, "Expected output contract is missing or invalid.")
    missing = [key for key in required if key not in output]
    if missing:
        return _make_result("expected_output_contract", False, f"Expected output is missing contract keys: {', '.join(missing)}")
    return _make_result("expected_output_contract", True, f"Expected output satisfies {len(required)} required keys.")


def check_citations_grounded(case: dict[str, Any], artifact_json: dict[str, Any]) -> dict[str, Any]:
    frozen_inputs = artifact_json.get("frozen_inputs")
    output = artifact_json.get("expected_output")
    sources = frozen_inputs.get("sources") if isinstance(frozen_inputs, dict) else None
    citations = output.get("citations") if isinstance(output, dict) else None
    claims = output.get("claims") if isinstance(output, dict) else None
    if not isinstance(sources, list) or not sources or not isinstance(citations, list) or not citations or not isinstance(claims, list) or not claims:
        return _make_result("citations_grounded", False, "Sources, citations, and cited claims must be non-empty arrays.")
    source_map: dict[str, str] = {}
    failures: list[str] = []
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str) or not isinstance(source.get("content"), str):
            failures.append("invalid source record")
            continue
        source_id = source["source_id"]
        if not source_id or source_id in source_map:
            failures.append(f"missing or duplicate source_id {source_id!r}")
            continue
        source_map[source_id] = source["content"]
    citation_ids: set[str] = set()
    for citation in citations:
        if not isinstance(citation, dict):
            failures.append("invalid citation record")
            continue
        citation_id = citation.get("citation_id")
        source_id = citation.get("source_id")
        quote = citation.get("quote")
        if not isinstance(citation_id, str) or not citation_id or citation_id in citation_ids:
            failures.append(f"missing or duplicate citation_id {citation_id!r}")
            continue
        citation_ids.add(citation_id)
        if not isinstance(source_id, str) or source_id not in source_map:
            failures.append(f"citation {citation_id} references an unknown source")
        elif not isinstance(quote, str) or not quote or quote not in source_map[source_id]:
            failures.append(f"citation {citation_id} quote is not present in source {source_id}")
    used_ids: set[str] = set()
    for claim in claims:
        claim_ids = claim.get("citation_ids") if isinstance(claim, dict) else None
        if not isinstance(claim_ids, list) or not claim_ids or any(not isinstance(value, str) for value in claim_ids):
            failures.append("claim is missing citation_ids")
            continue
        unknown = sorted(set(claim_ids) - citation_ids)
        if unknown:
            failures.append(f"claim references unknown citations: {', '.join(unknown)}")
        used_ids.update(claim_ids)
    unused = sorted(citation_ids - used_ids)
    if unused:
        failures.append(f"unused citations: {', '.join(unused)}")
    if failures:
        return _make_result("citations_grounded", False, "; ".join(failures))
    return _make_result("citations_grounded", True, f"Resolved {len(citation_ids)} citations against {len(source_map)} frozen sources.")


def check_runner_candidate_provenance(case: dict[str, Any], artifact_json: dict[str, Any]) -> dict[str, Any]:
    producer = artifact_json.get("producer")
    required = ("workflow_id", "run_id", "stage_id", "response_id")
    if (
        not isinstance(producer, dict)
        or producer.get("kind") != "responses_runner_v2_offline_fake_client"
        or any(not isinstance(producer.get(key), str) or not producer[key] for key in required)
    ):
        return _make_result(
            "runner_candidate_provenance",
            False,
            "Candidate lacks complete offline runner provenance.",
        )
    if producer["workflow_id"] != "synthetic_one_pass" or producer["stage_id"] != "draft_summary":
        return _make_result(
            "runner_candidate_provenance",
            False,
            "Candidate provenance identifies the wrong workflow or stage.",
        )
    return _make_result(
        "runner_candidate_provenance",
        True,
        f"Candidate is bound to offline run {producer['run_id']} response {producer['response_id']}.",
    )


CHECK_HANDLERS = {
    "json_required_keys": check_json_required_keys,
    "json_array_min_length": check_json_array_min_length,
    "json_path_equals": check_json_path_equals,
    "structured_required_keys": check_structured_required_keys,
    "text_required_substrings": check_text_required_substrings,
    "expected_output_contract": check_expected_output_contract,
    "citations_grounded": check_citations_grounded,
    "runner_candidate_provenance": check_runner_candidate_provenance,
}


def grade_case(
    dataset: dict[str, Any],
    case_id: str,
    artifact_path: str | Path,
    *,
    structured_artifact_path: str | Path | None = None,
) -> dict[str, Any]:
    case = next((item for item in dataset["cases"] if item["id"] == case_id), None)
    if case is None:
        raise ValueError(f"Unknown case id: {case_id}")
    artifact = Path(artifact_path)
    artifact_text = artifact.read_text(encoding="utf-8")
    artifact_json = None
    if artifact.suffix.lower() == ".json":
        artifact_json = json.loads(artifact_text)
    fixture_path: Path | None = None
    fixture_json: dict[str, Any] | None = None
    if case.get("fixture") is not None:
        fixture_path = _resolve_path(str(case["fixture"]), relative_to=Path(dataset["_dataset_path"]).parent)
        fixture_json = json.loads(fixture_path.read_text(encoding="utf-8"))
    structured_output = None
    if structured_artifact_path is not None:
        structured_output = json.loads(Path(structured_artifact_path).read_text(encoding="utf-8"))
    checks = []
    for check_id in case["checks"]:
        handler = CHECK_HANDLERS[check_id]
        if check_id == "structured_required_keys":
            checks.append(handler(case, structured_output))
        elif check_id == "text_required_substrings":
            checks.append(handler(case, artifact_text))
        else:
            if not isinstance(artifact_json, dict):
                raise ValueError(
                    f"Case {case_id} requires JSON artifact input, got {artifact}"
                )
            check_payload = artifact_json
            if check_id in {"expected_output_contract", "citations_grounded"}:
                if not isinstance(fixture_json, dict):
                    raise ValueError(f"Case {case_id} is missing its frozen grading fixture")
                check_payload = {
                    **artifact_json,
                    "frozen_inputs": fixture_json.get("frozen_inputs"),
                    "expected_output_contract": fixture_json.get("expected_output_contract"),
                    "expected_output": artifact_json.get("output"),
                }
            checks.append(handler(case, check_payload))
    result = {
        "schema_version": EVAL_RESULT_SCHEMA_VERSION,
        "workflow": dataset["workflow"],
        "case_id": case_id,
        "artifact": str(artifact_path),
        "artifact_sha256": _sha256_file(artifact),
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }
    if structured_artifact_path is not None:
        structured_artifact = Path(structured_artifact_path)
        result["structured_artifact"] = str(structured_artifact_path)
        result["structured_artifact_sha256"] = _sha256_file(structured_artifact)
    if fixture_path is not None:
        result["reference_fixture"] = str(fixture_path)
        result["reference_fixture_sha256"] = _sha256_file(fixture_path)
    return result


def write_eval_result(result: dict[str, Any], artifact_path: str | Path) -> Path:
    artifact = Path(artifact_path)
    output_path = artifact.parent / "eval_result.json"
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def _validate_eval_result(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["result must be a JSON object"]
    required = {
        "schema_version",
        "workflow",
        "case_id",
        "artifact",
        "artifact_sha256",
        "passed",
        "checks",
    }
    optional = {
        "structured_artifact",
        "structured_artifact_sha256",
        "reference_fixture",
        "reference_fixture_sha256",
    }
    errors: list[str] = []
    if set(payload) - required - optional:
        errors.append(f"unexpected keys: {sorted(set(payload) - required - optional)}")
    if required - set(payload):
        errors.append(f"missing keys: {sorted(required - set(payload))}")
        return errors
    if payload["schema_version"] != EVAL_RESULT_SCHEMA_VERSION:
        errors.append("schema_version is not supported")
    for key in ("workflow", "case_id", "artifact"):
        if not isinstance(payload[key], str) or not payload[key]:
            errors.append(f"{key} must be a non-empty string")
    if not _is_sha256(payload["artifact_sha256"]):
        errors.append("artifact_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(payload["passed"], bool):
        errors.append("passed must be a boolean")
    checks = payload["checks"]
    if not isinstance(checks, list) or not checks:
        errors.append("checks must be a non-empty list")
    else:
        for index, check in enumerate(checks):
            if not isinstance(check, dict) or set(check) != {"id", "passed", "details"}:
                errors.append(f"checks[{index}] has an invalid shape")
                continue
            if not isinstance(check["id"], str) or not check["id"]:
                errors.append(f"checks[{index}].id must be a non-empty string")
            if not isinstance(check["passed"], bool):
                errors.append(f"checks[{index}].passed must be a boolean")
            if not isinstance(check["details"], str):
                errors.append(f"checks[{index}].details must be a string")
        if isinstance(payload["passed"], bool) and payload["passed"] != all(
            isinstance(check, dict) and check.get("passed") is True for check in checks
        ):
            errors.append("passed does not equal the conjunction of check results")
    has_structured_path = "structured_artifact" in payload
    has_structured_hash = "structured_artifact_sha256" in payload
    if has_structured_path != has_structured_hash:
        errors.append("structured artifact path and hash must be supplied together")
    elif has_structured_path:
        if not isinstance(payload["structured_artifact"], str) or not payload["structured_artifact"]:
            errors.append("structured_artifact must be a non-empty string")
        if not _is_sha256(payload["structured_artifact_sha256"]):
            errors.append("structured_artifact_sha256 must be a lowercase SHA-256 digest")
    has_reference_path = "reference_fixture" in payload
    has_reference_hash = "reference_fixture_sha256" in payload
    if has_reference_path != has_reference_hash:
        errors.append("reference fixture path and hash must be supplied together")
    elif has_reference_path:
        if not isinstance(payload["reference_fixture"], str) or not payload["reference_fixture"]:
            errors.append("reference_fixture must be a non-empty string")
        if not _is_sha256(payload["reference_fixture_sha256"]):
            errors.append("reference_fixture_sha256 must be a lowercase SHA-256 digest")
    return errors


def _check_file_hash(
    *,
    check_id: str,
    raw_path: Any,
    expected_sha256: Any,
    relative_to: Path,
) -> dict[str, Any]:
    if not isinstance(raw_path, str) or not raw_path:
        return _make_result(check_id, False, f"{check_id} path is missing.")
    if not _is_sha256(expected_sha256):
        return _make_result(check_id, False, f"{check_id} SHA-256 is missing or invalid.")
    candidate = _resolve_path(raw_path, relative_to=relative_to)
    if not candidate.is_file():
        return _make_result(check_id, False, f"{candidate} is missing or is not a file.")
    actual_sha256 = _sha256_file(candidate)
    if actual_sha256 != expected_sha256:
        return _make_result(
            check_id,
            False,
            f"SHA-256 mismatch for {candidate}: {actual_sha256} != {expected_sha256}.",
        )
    return _make_result(check_id, True, f"Verified {candidate} at SHA-256 {actual_sha256}.")


def grade_freeze_gate(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_payload, dict):
        raise ValueError(f"Freeze gate manifest must be a JSON object: {manifest_path}")
    checks: list[dict[str, Any]] = []
    manifest_dir = manifest_path.parent
    schema_version = manifest_payload.get("schema_version")
    if schema_version != FREEZE_GATE_SCHEMA_VERSION:
        checks.append(_make_result("schema_version", False, "Unsupported freeze gate schema_version."))
    else:
        checks.append(_make_result("schema_version", True, f"Using {schema_version}."))

    workflow = manifest_payload.get("workflow")
    dataset_file = manifest_payload.get("dataset_file")
    reviewer_notes = manifest_payload.get("reviewer_notes")
    synthetic_example_evidence = manifest_payload.get("synthetic_example_evidence")

    checks.append(
        _check_file_hash(
            check_id="dataset_file",
            raw_path=dataset_file,
            expected_sha256=manifest_payload.get("dataset_sha256"),
            relative_to=manifest_dir,
        )
    )
    checks.append(
        _check_file_hash(
            check_id="reviewer_notes",
            raw_path=reviewer_notes,
            expected_sha256=manifest_payload.get("reviewer_notes_sha256"),
            relative_to=manifest_dir,
        )
    )
    checks.append(
        _check_file_hash(
            check_id="synthetic_example_evidence",
            raw_path=synthetic_example_evidence,
            expected_sha256=manifest_payload.get("synthetic_example_evidence_sha256"),
            relative_to=manifest_dir,
        )
    )

    dataset: dict[str, Any] | None = None
    if checks[-3]["passed"] and isinstance(dataset_file, str):
        try:
            dataset = load_eval_dataset(_resolve_path(dataset_file, relative_to=manifest_dir))
        except (KeyError, TypeError, ValueError) as exc:
            checks.append(_make_result("dataset_schema", False, str(exc)))
        else:
            checks.append(_make_result("dataset_schema", True, "Dataset schema is valid."))
            checks.append(
                _make_result(
                    "workflow_identity",
                    isinstance(workflow, str) and bool(workflow) and dataset["workflow"] == workflow,
                    (
                        "Manifest and dataset workflow identities match."
                        if dataset["workflow"] == workflow and isinstance(workflow, str) and workflow
                        else f"Manifest workflow {workflow!r} does not match dataset workflow {dataset['workflow']!r}."
                    ),
                )
            )

    expected_cases = manifest_payload.get("expected_cases")
    catalog_errors: list[str] = []
    catalog: dict[str, dict[str, Any]] = {}
    if not isinstance(expected_cases, list) or not expected_cases:
        catalog_errors.append("expected_cases must be a non-empty list")
    else:
        required_case_keys = {
            "case_id",
            "result_path",
            "result_sha256",
            "artifact_path",
            "artifact_sha256",
        }
        optional_case_keys = {"structured_artifact_path", "structured_artifact_sha256"}
        for index, entry in enumerate(expected_cases):
            if not isinstance(entry, dict):
                catalog_errors.append(f"expected_cases[{index}] must be an object")
                continue
            if set(entry) - required_case_keys - optional_case_keys or required_case_keys - set(entry):
                catalog_errors.append(f"expected_cases[{index}] has an invalid shape")
                continue
            case_id = entry["case_id"]
            if not isinstance(case_id, str) or not case_id or case_id in catalog:
                catalog_errors.append(f"expected_cases[{index}] has a missing or duplicate case_id")
                continue
            if any(not isinstance(entry[key], str) or not entry[key] for key in ("result_path", "artifact_path")):
                catalog_errors.append(f"expected_cases[{index}] has an invalid path")
            if any(not _is_sha256(entry[key]) for key in ("result_sha256", "artifact_sha256")):
                catalog_errors.append(f"expected_cases[{index}] has an invalid SHA-256")
            has_structured_path = "structured_artifact_path" in entry
            has_structured_hash = "structured_artifact_sha256" in entry
            if has_structured_path != has_structured_hash:
                catalog_errors.append(f"expected_cases[{index}] has an incomplete structured artifact binding")
            elif has_structured_path and (
                not isinstance(entry["structured_artifact_path"], str)
                or not entry["structured_artifact_path"]
                or not _is_sha256(entry["structured_artifact_sha256"])
            ):
                catalog_errors.append(f"expected_cases[{index}] has an invalid structured artifact binding")
            catalog[case_id] = entry
    if dataset is not None:
        dataset_case_ids = [case["id"] for case in dataset["cases"]]
        if set(catalog) != set(dataset_case_ids) or len(catalog) != len(dataset_case_ids):
            catalog_errors.append(
                f"catalog cases {sorted(catalog)} do not exactly match dataset cases {sorted(dataset_case_ids)}"
            )
    checks.append(
        _make_result(
            "expected_case_catalog",
            not catalog_errors,
            "; ".join(catalog_errors) if catalog_errors else f"Catalog binds all {len(catalog)} required cases.",
        )
    )

    if dataset is not None and not catalog_errors:
        for case_id, entry in catalog.items():
            result_path = _resolve_path(entry["result_path"], relative_to=manifest_dir)
            artifact_path = _resolve_path(entry["artifact_path"], relative_to=manifest_dir)
            case_failures: list[str] = []
            for label, candidate, expected_sha256 in (
                ("result", result_path, entry["result_sha256"]),
                ("artifact", artifact_path, entry["artifact_sha256"]),
            ):
                if not candidate.is_file():
                    case_failures.append(f"{label} file is missing: {candidate}")
                elif _sha256_file(candidate) != expected_sha256:
                    case_failures.append(f"{label} SHA-256 mismatch: {candidate}")
            structured_path: Path | None = None
            if "structured_artifact_path" in entry:
                structured_path = _resolve_path(entry["structured_artifact_path"], relative_to=manifest_dir)
                if not structured_path.is_file():
                    case_failures.append(f"structured artifact file is missing: {structured_path}")
                elif _sha256_file(structured_path) != entry["structured_artifact_sha256"]:
                    case_failures.append(f"structured artifact SHA-256 mismatch: {structured_path}")
            result_payload: Any = None
            if result_path.is_file():
                try:
                    result_payload = json.loads(result_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    case_failures.append(f"result JSON is invalid: {exc}")
                else:
                    case_failures.extend(_validate_eval_result(result_payload))
            if isinstance(result_payload, dict) and not _validate_eval_result(result_payload):
                if result_payload["workflow"] != workflow or result_payload["case_id"] != case_id:
                    case_failures.append("result workflow/case identity does not match the catalog")
                if result_payload["artifact_sha256"] != entry["artifact_sha256"]:
                    case_failures.append("result artifact hash does not match the catalog")
                result_artifact = _resolve_path(result_payload["artifact"], relative_to=manifest_dir)
                if result_artifact.resolve() != artifact_path.resolve():
                    case_failures.append("result artifact path does not match the catalog")
                if structured_path is None and "structured_artifact" in result_payload:
                    case_failures.append("result has an unexpected structured artifact")
                elif structured_path is not None:
                    if result_payload.get("structured_artifact_sha256") != entry["structured_artifact_sha256"]:
                        case_failures.append("result structured artifact hash does not match the catalog")
                    raw_structured_path = result_payload.get("structured_artifact")
                    if not isinstance(raw_structured_path, str) or (
                        _resolve_path(raw_structured_path, relative_to=manifest_dir).resolve()
                        != structured_path.resolve()
                    ):
                        case_failures.append("result structured artifact path does not match the catalog")
                case_definition = next(item for item in dataset["cases"] if item["id"] == case_id)
                if case_definition.get("fixture") is not None:
                    expected_fixture = _resolve_path(
                        str(case_definition["fixture"]),
                        relative_to=Path(dataset["_dataset_path"]).parent,
                    )
                    raw_reference = result_payload.get("reference_fixture")
                    if (
                        not isinstance(raw_reference, str)
                        or _resolve_path(raw_reference, relative_to=manifest_dir).resolve()
                        != expected_fixture.resolve()
                        or result_payload.get("reference_fixture_sha256")
                        != case_definition.get("fixture_sha256")
                    ):
                        case_failures.append("result reference fixture path/hash does not match the frozen case")
                elif "reference_fixture" in result_payload:
                    case_failures.append("result has an unexpected reference fixture")
                if not case_failures:
                    recomputed = grade_case(
                        dataset,
                        case_id,
                        artifact_path,
                        structured_artifact_path=structured_path,
                    )
                    if (
                        result_payload["passed"] != recomputed["passed"]
                        or result_payload["checks"] != recomputed["checks"]
                    ):
                        case_failures.append("stored result does not match deterministic re-grading")
                    elif not recomputed["passed"]:
                        case_failures.append("deterministic re-grading failed")
            checks.append(
                _make_result(
                    f"case:{case_id}",
                    not case_failures,
                    "; ".join(case_failures) if case_failures else "Schema, identities, hashes, and grading verified.",
                )
            )

    return {
        "workflow": workflow,
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score Responses Runner v2 artifacts.")
    parser.add_argument("--dataset-file")
    parser.add_argument("--case-id")
    parser.add_argument("--artifact")
    parser.add_argument("--structured-artifact")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--freeze-gate-file")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.freeze_gate_file:
        result = grade_freeze_gate(args.freeze_gate_file)
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1

    if not args.dataset_file:
        raise SystemExit("--dataset-file is required unless --freeze-gate-file is used.")
    dataset = load_eval_dataset(args.dataset_file)

    if args.list_cases:
        print(json.dumps([case["id"] for case in dataset["cases"]], indent=2))
        return 0

    if not args.case_id or not args.artifact:
        raise SystemExit("--case-id and --artifact are required unless --list-cases is used.")
    result = grade_case(
        dataset,
        args.case_id,
        args.artifact,
        structured_artifact_path=args.structured_artifact,
    )
    output_path = write_eval_result(result, args.artifact)
    print(json.dumps(result, indent=2))
    print(output_path)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
