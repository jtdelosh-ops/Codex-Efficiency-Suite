#!/usr/bin/env python3
"""Validate structured task briefs and render deterministic work orders."""

import argparse
import json
import sys
from pathlib import Path


REQUIRED_FIELDS = (
    "goal",
    "context",
    "constraints",
    "expected_files_or_subsystems",
    "acceptance_criteria",
    "required_verification",
    "non_goals",
    "stop_condition",
    "preferred_model",
    "escalation_evidence",
)
LIST_FIELDS = {
    "constraints",
    "expected_files_or_subsystems",
    "acceptance_criteria",
    "required_verification",
    "non_goals",
    "escalation_evidence",
}


class WorkOrderError(ValueError):
    """Raised when input is not a complete, well-formed work order."""


def validate_work_order(data):
    if not isinstance(data, dict):
        raise WorkOrderError("input must be a JSON object")
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise WorkOrderError("missing required field(s): " + ", ".join(missing))
    errors = []
    for field in REQUIRED_FIELDS:
        value = data[field]
        if field in LIST_FIELDS:
            if not isinstance(value, list) or not value:
                errors.append(f"{field} must be a non-empty array of non-empty strings")
            elif any(not isinstance(item, str) or not item.strip() for item in value):
                errors.append(f"{field} must contain only non-empty strings")
        elif not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string")
    if errors:
        raise WorkOrderError("; ".join(errors))
    # Copy only the documented fields; output is stable and does not pass through
    # unrelated input keys that could be mistaken for instructions.
    return {field: data[field] for field in REQUIRED_FIELDS}


def render_json(work_order):
    return json.dumps({"schema_version": 1, "work_order": work_order}, indent=2, ensure_ascii=False) + "\n"


def _markdown_value(value):
    return value.replace("\r\n", "\n").replace("\r", "\n")


def render_markdown(work_order):
    lines = ["# Work Order", "", "## Goal", "", _markdown_value(work_order["goal"]), ""]
    for title, field in (
        ("Context", "context"),
        ("Preferred Model", "preferred_model"),
        ("Stop Condition", "stop_condition"),
    ):
        lines.extend((f"## {title}", "", _markdown_value(work_order[field]), ""))
    for title, field in (
        ("Constraints", "constraints"),
        ("Expected Files or Subsystems", "expected_files_or_subsystems"),
        ("Acceptance Criteria", "acceptance_criteria"),
        ("Required Verification", "required_verification"),
        ("Non-goals", "non_goals"),
        ("Escalation Evidence", "escalation_evidence"),
    ):
        lines.extend((f"## {title}", ""))
        lines.extend(f"- {_markdown_value(item)}" for item in work_order[field])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="JSON task brief")
    parser.add_argument("--json-out", required=True, help="path for machine-readable work order")
    parser.add_argument("--markdown-out", required=True, help="path for human-readable work order")
    args = parser.parse_args(argv)
    try:
        source = Path(args.input).read_text(encoding="utf-8")
        data = json.loads(source)
        work_order = validate_work_order(data)
        json_path, markdown_path = Path(args.json_out), Path(args.markdown_out)
        if json_path.resolve() == markdown_path.resolve():
            raise WorkOrderError("JSON and Markdown output paths must be different")
        json_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(render_json(work_order), encoding="utf-8")
        markdown_path.write_text(render_markdown(work_order), encoding="utf-8")
    except (OSError, json.JSONDecodeError, WorkOrderError) as exc:
        print(f"work_order: error: {exc}", file=sys.stderr)
        return 2
    print(f"Work order written: {json_path} and {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
