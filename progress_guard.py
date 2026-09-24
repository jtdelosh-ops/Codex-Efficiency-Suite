#!/usr/bin/env python3
"""Decide whether repeated equivalent failures should stop a task."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MAX_ATTEMPTS = 100
MAX_TEXT = 500
MAX_IDENTIFIER = 1024


class GuardError(ValueError):
    pass


def decide(payload: Any, threshold: int | None = None, limit: int = MAX_ATTEMPTS) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("attempts"), list):
        raise GuardError("input must be an object with an attempts array")
    if not 1 <= limit <= MAX_ATTEMPTS:
        raise GuardError(f"limit must be between 1 and {MAX_ATTEMPTS}")
    configured = payload.get("threshold", 3) if threshold is None else threshold
    if not isinstance(configured, int) or isinstance(configured, bool) or not 1 <= configured <= MAX_ATTEMPTS:
        raise GuardError("threshold must be an integer between 1 and 100")
    attempts = payload["attempts"]
    if len(attempts) > MAX_ATTEMPTS:
        raise GuardError(f"attempt history exceeds {MAX_ATTEMPTS} entries")
    for item in attempts:
        if not isinstance(item, dict) or not isinstance(item.get("signature"), str) or not item["signature"].strip():
            raise GuardError("each attempt requires a non-empty stable signature")
        for key in ("evidence_id", "approach_id", "summary"):
            if key in item and not isinstance(item[key], str):
                raise GuardError(f"{key} must be a string")
            if key in ("evidence_id", "approach_id") and key in item and len(item[key]) > MAX_IDENTIFIER:
                raise GuardError(f"{key} exceeds {MAX_IDENTIFIER} characters")
    recent = attempts[-limit:]
    if not recent:
        return {"decision": "CONTINUE", "reason": "No failure attempts recorded.", "matched_history": [],
                "threshold": configured, "next_action": "Proceed with the task and record concise evidence if a failure occurs."}
    latest = recent[-1]
    sig = latest["signature"]
    matched = []
    newer = None
    for row in reversed(recent):
        if row["signature"] != sig:
            break
        if newer is not None and any(row.get(field) != newer.get(field)
                                      for field in ("evidence_id", "approach_id")
                                      if row.get(field) is not None or newer.get(field) is not None):
            # Reset the equivalent-failure streak at an explicitly changed investigation input.
            break
        matched.append({"signature": sig, **{
            k: (row[k][:MAX_TEXT] if k == "summary" else row[k])
            for k in ("summary", "evidence_id", "approach_id") if k in row
        }})
        newer = row
    matched.reverse()
    count = len(matched)
    if count >= configured:
        decision, reason = "STOP", f"The same failure signature repeated {count} consecutive times without materially new evidence or approach."
        action = "Stop retrying. Gather different evidence, change the approach, reduce scope, or request review/escalation."
    else:
        decision, reason = "CONTINUE", f"The same failure signature has occurred {count} consecutive time(s), below threshold {configured}."
        action = "Make a materially different attempt; do not count cosmetic text or wording changes as progress."
    return {"decision": decision, "reason": reason, "matched_history": matched[-limit:], "threshold": configured,
            "next_action": action}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--threshold", type=int)
    p.add_argument("--limit", type=int, default=MAX_ATTEMPTS)
    p.add_argument("--json-out", type=Path)
    p.add_argument("--markdown-out", type=Path)
    a = p.parse_args(argv)
    try:
        result = decide(json.loads(a.input.read_text(encoding="utf-8")), a.threshold, a.limit)
        data = json.dumps(result, sort_keys=True, indent=2) + "\n"
        if a.json_out:
            a.json_out.parent.mkdir(parents=True, exist_ok=True); a.json_out.write_text(data, encoding="utf-8")
        if a.markdown_out:
            a.markdown_out.parent.mkdir(parents=True, exist_ok=True)
            a.markdown_out.write_text(f"# Progress Guard: {result['decision']}\n\n{result['reason']}\n\nThreshold: {result['threshold']}\n\nNext action: {result['next_action']}\n\nMatched history: {len(result['matched_history'])} attempt(s).\n", encoding="utf-8")
        print(data, end="")
        return 0
    except (OSError, json.JSONDecodeError, GuardError) as exc:
        print(json.dumps({"decision": "ERROR", "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
