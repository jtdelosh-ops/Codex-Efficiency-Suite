#!/usr/bin/env python3
"""Check explicitly declared local environment prerequisites."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


class PreflightError(Exception):
    pass


def version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)*", value)
    if not match:
        raise ValueError("no numeric version found")
    return tuple(int(part) for part in match.group().split("."))


def satisfies(actual: str, constraint: str) -> bool:
    match = re.fullmatch(r"\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+)*)\s*", constraint)
    if not match:
        raise ValueError("constraint must be one of >=, <=, ==, >, < followed by a dotted version")
    left, right = version_tuple(actual), version_tuple(match.group(2))
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return {">=": left >= right, "<=": left <= right, "==": left == right,
            ">": left > right, "<": left < right}[match.group(1)]


def validate(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise PreflightError("Configuration must be a JSON object.")
    for key in ("executables", "runtime_versions", "paths", "environment", "commands"):
        if key in config and not isinstance(config[key], list):
            raise PreflightError(f"{key} must be an array.")
    return config


def run(config: Any, root: Path) -> dict[str, Any]:
    config = validate(config)
    checks: list[dict[str, str]] = []

    def add(label: str, status: str, detail: str) -> None:
        checks.append({"requirement": label, "status": status, "detail": detail[:400]})

    for item in config.get("executables", []):
        if isinstance(item, str):
            name, constraint = item, None
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            name, constraint = item["name"], item.get("version")
        else:
            raise PreflightError("Each executable must be a name or {name, version?} object.")
        found = shutil.which(name)
        if not found:
            add(f"executable:{name}", "FAIL", "not found on PATH")
        elif constraint:
            try:
                result = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=5, check=False)
                output = (result.stdout + " " + result.stderr).strip()
                ok = result.returncode == 0 and satisfies(output, str(constraint))
                add(f"executable:{name}", "PASS" if ok else "FAIL", f"version check {'matched' if ok else 'did not match'} {constraint}; {output[:180]}")
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                add(f"executable:{name}", "ERROR", f"version check failed: {type(exc).__name__}")
        else:
            add(f"executable:{name}", "PASS", "found on PATH")

    for item in config.get("runtime_versions", []):
        if not isinstance(item, dict) or item.get("runtime") != "python" or not isinstance(item.get("constraint"), str):
            raise PreflightError("runtime_versions entries require runtime='python' and a version constraint.")
        try:
            actual = ".".join(map(str, sys.version_info[:3]))
            ok = satisfies(actual, item["constraint"])
            add("runtime:python", "PASS" if ok else "FAIL", f"{actual} {'satisfies' if ok else 'does not satisfy'} {item['constraint']}")
        except ValueError as exc:
            raise PreflightError(str(exc)) from exc

    for item in config.get("paths", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise PreflightError("paths entries require a path string and optional type=file|directory.")
        path = (root / item["path"]).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise PreflightError(f"Required path escapes root: {item['path']}") from exc
        kind = item.get("type", "file")
        if kind not in ("file", "directory"):
            raise PreflightError("path type must be file or directory")
        ok = path.is_file() if kind == "file" else path.is_dir()
        add(f"path:{item['path']}", "PASS" if ok else "FAIL", f"required {kind} {'exists' if ok else 'is missing'}")

    for name in config.get("environment", []):
        if not isinstance(name, str) or not name:
            raise PreflightError("environment entries must be non-empty variable names.")
        present = name in os.environ and bool(os.environ[name])
        add(f"environment:{name}", "PASS" if present else "FAIL", "present" if present else "missing or empty; value not recorded")

    for item in config.get("commands", []):
        if not isinstance(item, dict) or not isinstance(item.get("command"), list) or not item["command"] or not all(isinstance(arg, str) and arg for arg in item["command"]):
            raise PreflightError("commands entries require a non-empty argument-array 'command'.")
        cmd = item["command"]
        label = str(item.get("name", "command:" + Path(cmd[0]).name))
        timeout = item.get("timeout_seconds", 5)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0 or timeout > 30:
            raise PreflightError("command timeout_seconds must be >0 and <=30")
        try:
            completed = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout, check=False, shell=False)
            output = (completed.stdout + " " + completed.stderr).strip()
            wanted = item.get("version")
            ok = completed.returncode == 0 and (not wanted or satisfies(output, str(wanted)))
            add(label, "PASS" if ok else "FAIL", f"exit {completed.returncode}; {output[:180] or 'no output'}")
        except subprocess.TimeoutExpired:
            add(label, "ERROR", "command timed out")
        except OSError as exc:
            add(label, "ERROR", f"command could not run: {type(exc).__name__}")

    statuses = {item["status"] for item in checks}
    status = "ERROR" if "ERROR" in statuses else "FAIL" if "FAIL" in statuses else "PASS"
    return {"schema_version": 1, "status": status, "checks": checks,
            "summary": {key: sum(item["status"] == key for item in checks) for key in ("PASS", "FAIL", "ERROR")}}


def render_markdown(result: dict[str, Any]) -> str:
    lines = [f"# Environment Preflight: {result['status']}", "", "| Requirement | Status | Detail |", "|---|---|---|"]
    lines.extend(f"| {c['requirement']} | {c['status']} | {c['detail'].replace('|', '\\|')} |" for c in result["checks"])
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("preflight.json"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--json-out", type=Path, default=Path(".preflight/preflight.json"))
    parser.add_argument("--markdown-out", type=Path, default=Path(".preflight/preflight.md"))
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        result = run(config, args.root.resolve())
        for path, content in ((args.json_out, json.dumps(result, indent=2) + "\n"), (args.markdown_out, render_markdown(result))):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        print(json.dumps(result, indent=2))
        return {"PASS": 0, "FAIL": 1, "ERROR": 2}[result["status"]]
    except (OSError, json.JSONDecodeError, PreflightError) as exc:
        print(json.dumps({"schema_version": 1, "status": "ERROR", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
