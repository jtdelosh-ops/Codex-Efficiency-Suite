#!/usr/bin/env python3
"""Render structured session state as a bounded handoff packet."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED = ("goal", "completed_work", "changed_files", "verification_results", "blockers",
            "unresolved_issues", "decisions", "next_action")
MAX_TEXT = 1200
MAX_ITEMS = 30


class HandoffError(Exception):
    pass


def bounded(value: Any) -> str:
    text = value.strip()
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…[truncated]"


def validate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise HandoffError("session state must be a JSON object")
    missing = [field for field in REQUIRED if field not in data]
    if missing:
        raise HandoffError("missing required fields: " + ", ".join(missing))
    result: dict[str, Any] = {"schema_version": 1}
    for field in ("goal", "next_action"):
        value = data[field]
        if not isinstance(value, str) or not value.strip():
            raise HandoffError(f"{field} must be a non-empty string")
        result[field] = bounded(value)
    for field in ("completed_work", "changed_files", "verification_results", "blockers", "unresolved_issues", "decisions"):
        values = data[field]
        if not isinstance(values, list) or not all(isinstance(item, (str, dict)) for item in values):
            raise HandoffError(f"{field} must be an array of strings or objects")
        clean = []
        for item in values[:MAX_ITEMS]:
            if isinstance(item, str):
                clean.append(bounded(item))
            else:
                clean.append({str(k)[:80]: bounded(v) if isinstance(v, str) else v for k, v in list(item.items())[:20]})
        if len(values) > MAX_ITEMS:
            clean.append(f"…[{len(values) - MAX_ITEMS} additional items omitted]")
        result[field] = clean
    failed = any(isinstance(item, dict) and str(item.get("status", "")).upper() not in ("PASS", "SKIP", "NOT_RUN")
                 or isinstance(item, str) and any(token in item.upper() for token in ("FAIL", "ERROR", "TIMEOUT"))
                 for item in result["verification_results"])
    result["overall_status"] = "BLOCKED" if result["blockers"] or failed else "READY"
    result["completion_claim"] = "not_asserted; verify listed evidence independently"
    return result


def render_markdown(data: dict[str, Any]) -> str:
    lines = ["# Session Handoff", "", f"**State:** {data['overall_status']}", "", "## Goal", "", data["goal"], ""]
    labels = (("completed_work", "Completed Work"), ("changed_files", "Changed Files"),
              ("verification_results", "Verification Results"), ("blockers", "Blockers"),
              ("unresolved_issues", "Unresolved Issues"), ("decisions", "Decisions"))
    for key, label in labels:
        lines.extend([f"## {label}", ""])
        lines.extend(("- " + (json.dumps(item, sort_keys=True) if isinstance(item, dict) else item) for item in data[key]) or ["- None recorded"])
        lines.append("")
    lines.extend(["## Next Action", "", data["next_action"], "", f"> {data['completion_claim']}", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, default=Path(".session-handoff/handoff.json"))
    parser.add_argument("--markdown-out", type=Path, default=Path(".session-handoff/handoff.md"))
    args = parser.parse_args(argv)
    try:
        data = validate(json.loads(args.input.read_text(encoding="utf-8")))
        outputs = ((args.json_out, json.dumps(data, indent=2, ensure_ascii=False) + "\n"),
                   (args.markdown_out, render_markdown(data)))
        if args.input.resolve() in {path.resolve() for path, _ in outputs}:
            raise HandoffError("output paths must not overwrite input")
        for path, content in outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        print(json.dumps({"status": data["overall_status"], "json": str(args.json_out), "markdown": str(args.markdown_out)}))
        return 1 if data["overall_status"] == "BLOCKED" else 0
    except (OSError, json.JSONDecodeError, HandoffError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
