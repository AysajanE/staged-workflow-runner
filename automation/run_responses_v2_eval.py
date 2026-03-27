#!/usr/bin/env python3
"""Local eval harness for Responses Runner v2 artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent.parent


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


CHECK_HANDLERS = {
    "json_required_keys": check_json_required_keys,
    "json_array_min_length": check_json_array_min_length,
    "json_path_equals": check_json_path_equals,
    "structured_required_keys": check_structured_required_keys,
    "text_required_substrings": check_text_required_substrings,
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
            checks.append(handler(case, artifact_json))
    return {
        "workflow": dataset["workflow"],
        "case_id": case_id,
        "artifact": str(artifact_path),
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def write_eval_result(result: dict[str, Any], artifact_path: str | Path) -> Path:
    artifact = Path(artifact_path)
    output_path = artifact.parent / "eval_result.json"
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def grade_freeze_gate(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_payload, dict):
        raise ValueError(f"Freeze gate manifest must be a JSON object: {manifest_path}")
    checks: list[dict[str, Any]] = []
    dataset_file = manifest_payload.get("dataset_file")
    reviewer_notes = manifest_payload.get("reviewer_notes")
    synthetic_example_evidence = manifest_payload.get("synthetic_example_evidence")
    eval_result_paths = manifest_payload.get("eval_result_paths")

    if not isinstance(dataset_file, str) or not Path(dataset_file).exists():
        checks.append(_make_result("dataset_file", False, "dataset_file is missing or does not exist."))
    else:
        checks.append(_make_result("dataset_file", True, "dataset_file exists."))

    if not isinstance(reviewer_notes, str) or not Path(reviewer_notes).exists():
        checks.append(_make_result("reviewer_notes", False, "reviewer_notes is missing or does not exist."))
    else:
        checks.append(_make_result("reviewer_notes", True, "reviewer_notes exists."))

    if not isinstance(synthetic_example_evidence, str) or not Path(synthetic_example_evidence).exists():
        checks.append(
            _make_result(
                "synthetic_example_evidence",
                False,
                "synthetic_example_evidence is missing or does not exist.",
            )
        )
    else:
        checks.append(_make_result("synthetic_example_evidence", True, "synthetic example evidence exists."))

    if not isinstance(eval_result_paths, list) or not eval_result_paths:
        checks.append(_make_result("eval_result_paths", False, "eval_result_paths must be a non-empty list."))
    else:
        failures: list[str] = []
        for raw_path in eval_result_paths:
            candidate = Path(str(raw_path))
            if not candidate.exists():
                failures.append(f"missing {candidate}")
                continue
            eval_result_payload = json.loads(candidate.read_text(encoding="utf-8"))
            if not isinstance(eval_result_payload, dict) or not eval_result_payload.get("passed"):
                failures.append(f"failed {candidate}")
        checks.append(
            _make_result(
                "eval_result_paths",
                not failures,
                "; ".join(failures) if failures else "All eval result files passed.",
            )
        )

    return {
        "workflow": manifest_payload.get("workflow", "responses_runner_v2"),
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
