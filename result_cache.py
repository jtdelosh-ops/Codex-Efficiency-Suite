#!/usr/bin/env python3
"""Bounded local cache keyed by explicit source/config/environment fingerprints."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

MAX_RECORDS = 1000
MAX_BYTES = 16384
MAX_VALUE_BYTES = 8192
KEY_FIELDS = ("tool", "source", "configuration", "environment", "dependencies")
SECRET_KEY = re.compile(r"(?i)(secret|password|token|credential|api.?key|authorization)")
SECRET_VALUE = re.compile(r"(?i)(?:password|token|secret|api[_-]?key|authorization)\s*[=:]\s*[^\s,;]+")
FINGERPRINT = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{16,64}$")


class CacheError(ValueError):
    pass


def normalize_key(key: Any) -> dict[str, Any]:
    if not isinstance(key, dict) or set(key) != set(KEY_FIELDS):
        raise CacheError("key must contain exactly: " + ", ".join(KEY_FIELDS))
    if not isinstance(key["tool"], str) or not key["tool"].strip():
        raise CacheError("tool must be a non-empty string")
    for field in KEY_FIELDS[1:]:
        if not isinstance(key[field], str) or not FINGERPRINT.fullmatch(key[field].strip()):
            raise CacheError(f"{field} must be a SHA-256-style hex fingerprint, not a raw input value")
    return {field: key[field].strip() for field in KEY_FIELDS}


def key_id(key: dict[str, Any]) -> str:
    canonical = json.dumps(key, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if not isinstance(k, str) or SECRET_KEY.search(k):
                raise CacheError("cached result contains a secret-like field name")
            result[k] = safe_value(v)
        return result
    if isinstance(value, list):
        if len(value) > 100:
            raise CacheError("cached list exceeds 100 items")
        return [safe_value(v) for v in value]
    if isinstance(value, str):
        if SECRET_VALUE.search(value):
            raise CacheError("cached result appears to contain a secret assignment")
        if len(value.encode("utf-8")) > 2000:
            raise CacheError("cached string exceeds 2000 bytes")
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise CacheError("cached result must contain only JSON values")


def load(path: Path) -> list[dict[str, Any]]:
    if not path.exists(): return []
    try:
        if path.stat().st_size > MAX_RECORDS * MAX_BYTES:
            raise CacheError("cache store exceeds 16 MiB safety limit")
        data = json.loads(path.read_text(encoding="utf-8"))
    except CacheError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheError(f"invalid cache store: {exc}") from exc
    if not isinstance(data, list) or len(data) > MAX_RECORDS or any(not isinstance(x, dict) for x in data):
        raise CacheError("cache store must be an array of at most 1000 records")
    return data


def put(path: Path, key: Any, value: Any, ttl_seconds: int | None = None, now: int | None = None) -> dict[str, Any]:
    normalized = normalize_key(key)
    cleaned = safe_value(value)
    if ttl_seconds is not None and (not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds < 1):
        raise CacheError("ttl_seconds must be a positive integer")
    encoded_value = json.dumps(cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded_value.encode()) > MAX_VALUE_BYTES: raise CacheError("cached value exceeds 8192 bytes")
    records = load(path)
    current = int(time.time()) if now is None else now
    record = {"key": normalized, "key_id": key_id(normalized), "value": cleaned, "created_at": current,
              "expires_at": current + ttl_seconds if ttl_seconds else None}
    records = [r for r in records if r.get("key_id") != record["key_id"]]
    records.append(record)
    records = records[-MAX_RECORDS:]
    data = json.dumps(records, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if len(data.encode()) > MAX_RECORDS * MAX_BYTES:
        raise CacheError("cache store exceeds 16 MiB safety limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return {"status": "STORED", "key_id": record["key_id"], "records": len(records)}


def get(path: Path, key: Any, now: int | None = None) -> dict[str, Any]:
    normalized = normalize_key(key)
    target = key_id(normalized)
    current = int(time.time()) if now is None else now
    for record in reversed(load(path)):
        if record.get("key_id") == target:
            expiry = record.get("expires_at")
            if expiry is not None and (not isinstance(expiry, int) or expiry <= current):
                return {"status": "MISS", "reason": "record expired", "key_id": target}
            return {"status": "HIT", "reason": "all declared key inputs match", "key_id": target, "value": record.get("value")}
    # Report which key component differs without returning either fingerprint.
    records = load(path)
    reasons = []
    for field in KEY_FIELDS:
        if any(isinstance(r.get("key"), dict) and r["key"].get(field) != normalized[field] for r in records):
            reasons.append(field)
    reason = "fingerprint mismatch: " + ", ".join(reasons) if reasons else "no matching record"
    return {"status": "MISS", "reason": reason, "key_id": target}


def query(path: Path, limit: int = 20) -> dict[str, Any]:
    if not 1 <= limit <= 100: raise CacheError("limit must be between 1 and 100")
    rows = load(path)[-limit:]
    return {"count": len(rows), "records": [{"tool": r.get("key", {}).get("tool"), "key_id": r.get("key_id"),
             "created_at": r.get("created_at"), "expires_at": r.get("expires_at")} for r in rows]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="action", required=True)
    for action in ("put", "get"):
        q = sub.add_parser(action); q.add_argument("--store", type=Path, default=Path(".result-cache/cache.json")); q.add_argument("--input", type=Path, required=True)
        if action == "put": q.add_argument("--ttl-seconds", type=int)
    q = sub.add_parser("query"); q.add_argument("--store", type=Path, default=Path(".result-cache/cache.json")); q.add_argument("--limit", type=int, default=20)
    a = p.parse_args(argv)
    try:
        if a.action == "query": result = query(a.store, a.limit)
        else:
            body = json.loads(a.input.read_text(encoding="utf-8"))
            if not isinstance(body, dict): raise CacheError("input must be a JSON object")
            result = put(a.store, body.get("key"), body.get("value"), a.ttl_seconds) if a.action == "put" else get(a.store, body.get("key"))
        print(json.dumps(result, sort_keys=True, indent=2)); return 0
    except (OSError, json.JSONDecodeError, CacheError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}), file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
