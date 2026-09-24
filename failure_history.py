#!/usr/bin/env python3
"""Append and query a bounded, local history of concise failure events."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_FIELD = 1000
MAX_EVENTS = 10000
SECRET = re.compile(r"(?i)([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL)[A-Z0-9_]*\s*[=:]\s*)([^\s,;]+)")


class HistoryError(Exception):
    pass


def clean_text(value: Any, limit: int = MAX_FIELD) -> str:
    if not isinstance(value, str):
        raise HistoryError("event text fields must be strings")
    text = SECRET.sub(r"\1[REDACTED]", value)
    return text[:limit] + ("…[truncated]" if len(text) > limit else "")


def normalize(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise HistoryError("each event must be a JSON object")
    tool = event.get("tool")
    status = event.get("status")
    if not isinstance(tool, str) or not tool.strip() or not isinstance(status, str) or not status.strip():
        raise HistoryError("event requires non-empty tool and status")
    allowed = {"tool", "status", "signature", "summary", "timestamp", "source"}
    record = {"tool": tool.strip()[:120], "status": status.strip().upper()[:40],
              "summary": clean_text(event.get("summary", "")),
              "source": clean_text(event.get("source", "explicit"), 120)}
    if "timestamp" in event:
        record["timestamp"] = clean_text(event["timestamp"], 80)
    canonical = json.dumps({"tool": record["tool"], "status": record["status"], "summary": record["summary"]}, sort_keys=True, separators=(",", ":"))
    signature = event.get("signature") or hashlib.sha256(canonical.encode()).hexdigest()[:24]
    if not isinstance(signature, str) or not signature.strip():
        raise HistoryError("signature must be a non-empty string")
    record["signature"] = clean_text(signature, 120)
    if set(event) - allowed:
        # Arbitrary payloads may contain logs or secrets; never persist them.
        record["ignored_fields"] = sorted(str(key)[:80] for key in set(event) - allowed)[:20]
    return record


def read_store(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("not an object")
            records.append(row)
            if len(records) > MAX_EVENTS:
                raise HistoryError(f"history exceeds safety limit of {MAX_EVENTS} entries")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise HistoryError(f"invalid history store: {exc}") from exc
    return records


def append(path: Path, events: list[Any], deduplicate: bool = False) -> int:
    normalized = [normalize(event) for event in events]
    existing = read_store(path)
    known = {row.get("signature") for row in existing}
    fresh = []
    for row in normalized:
        if deduplicate and row["signature"] in known:
            continue
        fresh.append(row)
        known.add(row["signature"])
    if len(existing) + len(fresh) > MAX_EVENTS:
        raise HistoryError(f"append would exceed safety limit of {MAX_EVENTS} entries")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in fresh:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    return len(fresh)


def query(records: list[dict[str, Any]], tool: str | None = None, status: str | None = None,
          signature: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise HistoryError("limit must be between 1 and 100")
    selected = [row for row in records if (not tool or row.get("tool") == tool)
                and (not status or row.get("status", "").upper() == status.upper())
                and (not signature or row.get("signature") == signature)]
    return selected[-limit:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    ingest = sub.add_parser("append")
    ingest.add_argument("--store", type=Path, default=Path(".failure-history/events.jsonl"))
    ingest.add_argument("--input", type=Path, required=True)
    ingest.add_argument("--deduplicate", action="store_true")
    show = sub.add_parser("query")
    show.add_argument("--store", type=Path, default=Path(".failure-history/events.jsonl"))
    show.add_argument("--tool")
    show.add_argument("--status")
    show.add_argument("--signature")
    show.add_argument("--limit", type=int, default=20)
    show.add_argument("--format", choices=("json", "markdown"), default="markdown")
    args = parser.parse_args(argv)
    try:
        if args.action == "append":
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            events = payload if isinstance(payload, list) else [payload]
            count = append(args.store, events, args.deduplicate)
            print(json.dumps({"status": "PASS", "appended": count, "store": str(args.store)}))
        else:
            rows = query(read_store(args.store), args.tool, args.status, args.signature, args.limit)
            result = {"count": len(rows), "events": rows}
            if args.format == "json":
                print(json.dumps(result, indent=2))
            else:
                print("# Failure History\n\n" + f"Matching events: {len(rows)}\n")
                for row in rows:
                    print(f"- **{row['tool']} / {row['status']}** (`{row['signature']}`): {row['summary']}")
            
        return 0
    except (OSError, json.JSONDecodeError, HistoryError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
