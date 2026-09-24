#!/usr/bin/env python3
"""Run one explicitly configured verification command and preserve its evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERSION = "0.1.0"
EXIT_CODES = {"PASS": 0, "FAIL": 1, "TIMEOUT": 2, "ERROR": 3}


class RunnerError(Exception):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_config(path: Path, profile: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        config = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"Cannot read configuration {path}: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("profiles"), dict):
        raise RunnerError("Configuration must contain an object named 'profiles'.")
    selected = config["profiles"].get(profile)
    if not isinstance(selected, dict):
        raise RunnerError(f"Profile {profile!r} is missing or is not an object.")
    command = selected.get("command")
    timeout = selected.get("timeout_seconds")
    env_names = selected.get("environment", [])
    dependency_files = selected.get("dependency_files", [])
    if (not isinstance(command, list) or not command or
            not all(isinstance(part, str) and part for part in command)):
        raise RunnerError("Profile 'command' must be a non-empty array of non-empty strings.")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise RunnerError("Profile 'timeout_seconds' must be a positive number.")
    if not isinstance(env_names, list) or not all(isinstance(item, str) for item in env_names):
        raise RunnerError("Profile 'environment' must be an array of variable names.")
    if not isinstance(dependency_files, list) or not all(isinstance(item, str) for item in dependency_files):
        raise RunnerError("Profile 'dependency_files' must be an array of paths.")
    return {"command": command, "timeout_seconds": timeout,
            "environment": sorted(set(env_names)),
            "dependency_files": sorted(set(dependency_files))}, raw


def git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def source_snapshot(root: Path, excluded: Path) -> dict[str, Any]:
    """Hash file contents and symlink targets; fail closed on unreadable source."""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root)
        if relative.parts[0] == ".git":
            continue
        try:
            path.resolve().relative_to(excluded.resolve())
            continue
        except ValueError:
            pass
        except OSError as exc:
            raise RunnerError(f"Cannot resolve {relative}: {exc}") from exc
        try:
            if path.is_symlink():
                payload = b"symlink\0" + os.readlink(path).encode("utf-8", "surrogateescape")
            elif path.is_file():
                payload = b"file\0" + path.read_bytes()
            else:
                continue
        except OSError as exc:
            raise RunnerError(f"Cannot read source path {relative}: {exc}") from exc
        encoded_path = relative.as_posix().encode("utf-8", "surrogateescape")
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        count += 1
    return {"tree_sha256": digest.hexdigest(), "file_count": count,
            "git_head": git_head(root)}


def environment_fingerprint(root: Path, config: dict[str, Any], config_bytes: bytes,
                            command: list[str]) -> dict[str, Any]:
    dependencies: dict[str, str] = {}
    for item in config["dependency_files"]:
        path = (root / item).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise RunnerError(f"Dependency input escapes repository root: {item}") from exc
        if not path.is_file():
            raise RunnerError(f"Declared dependency input is missing: {item}")
        try:
            dependencies[item] = sha256(path.read_bytes())
        except OSError as exc:
            raise RunnerError(f"Cannot read dependency input {item}: {exc}") from exc
    env = {name: (sha256(os.environ[name].encode()) if name in os.environ else None)
           for name in config["environment"]}
    return {
        "runner_version": VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "command": command,
        "config_sha256": sha256(config_bytes),
        "environment_value_sha256": env,
        "dependency_files_sha256": dependencies,
    }


def excerpt(text: str, limit: int = 1600) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return "[excerpt truncated]\n" + cleaned[-limit:]


def render_markdown(result: dict[str, Any]) -> str:
    status = result["status"]
    snapshot = result.get("snapshot") or {}
    lines = [f"# Verification: {status}", "",
             f"- Run ID: `{result['run_id']}`",
             f"- Profile: `{result['profile']}`",
             f"- Repository: `{result['repository']}`",
             f"- Snapshot: `{snapshot.get('tree_sha256', 'unavailable')}`",
             f"- Command: `{shlex.join(result.get('command', [])) or 'unavailable'}`",
             f"- Exit code: `{result.get('exit_code')}`",
             f"- Duration: `{result.get('duration_seconds', 0):.3f}s`",
             f"- Diagnostics: `{result.get('artifacts', {}).get('stdout', 'unavailable')}`, `"
             f"{result.get('artifacts', {}).get('stderr', 'unavailable')}`", ""]
    if result.get("error"):
        lines += ["## Error", "", result["error"], ""]
    for key in ("stdout_excerpt", "stderr_excerpt"):
        if result.get(key):
            label = "Standard output" if key.startswith("stdout") else "Standard error"
            lines += [f"## {label} excerpt", "", "```text", result[key], "```", ""]
    if result.get("snapshot_changed"):
        lines += ["Source changed during verification; this run is not valid acceptance evidence.", ""]
    return "\n".join(lines)


def run(root: Path, config_path: Path, profile: str, report_dir: Path) -> tuple[dict[str, Any], Path]:
    root = root.resolve()
    report_dir = report_dir.resolve()
    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_dir = report_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    result: dict[str, Any] = {
        "schema_version": 1, "runner_version": VERSION, "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "repository": str(root), "profile": profile, "status": "ERROR",
        "command": [], "exit_code": None, "duration_seconds": 0,
        "snapshot": None, "environment_fingerprint": None,
        "artifacts": {"stdout": "stdout.log", "stderr": "stderr.log"},
        "stdout_excerpt": "", "stderr_excerpt": "", "error": None,
        "snapshot_changed": False,
    }
    stdout_path, stderr_path = run_dir / "stdout.log", run_dir / "stderr.log"
    try:
        if not root.is_dir():
            raise RunnerError(f"Repository root is not a directory: {root}")
        cfg_path = config_path.resolve()
        try:
            cfg_path.relative_to(root)
        except ValueError as exc:
            raise RunnerError("Configuration file must be inside the repository root.") from exc
        config, config_bytes = load_config(cfg_path, profile)
        result["command"] = config["command"]
        result["snapshot"] = source_snapshot(root, report_dir)
        result["environment_fingerprint"] = environment_fingerprint(
            root, config, config_bytes, config["command"])
        (run_dir / "stdout.log").write_bytes(b"")
        (run_dir / "stderr.log").write_bytes(b"")
        try:
            completed = subprocess.run(
                config["command"], cwd=root, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=config["timeout_seconds"],
                check=False, shell=False,
            )
            stdout_path.write_bytes(completed.stdout)
            stderr_path.write_bytes(completed.stderr)
            result["exit_code"] = completed.returncode
            result["status"] = "PASS" if completed.returncode == 0 else "FAIL"
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_bytes(exc.stdout or b"")
            stderr_path.write_bytes(exc.stderr or b"")
            result["status"] = "TIMEOUT"
            result["error"] = f"Command exceeded timeout of {config['timeout_seconds']} seconds."
        except OSError as exc:
            result["status"] = "ERROR"
            result["error"] = f"Could not execute command: {exc}"
        after = source_snapshot(root, report_dir)
        result["snapshot_after"] = after
        result["snapshot_changed"] = after != result["snapshot"]
        if result["snapshot_changed"] and result["status"] == "PASS":
            result["status"] = "ERROR"
            result["error"] = "Source snapshot changed during verification; result is invalid."
    except RunnerError as exc:
        result["status"] = "ERROR"
        result["error"] = str(exc)
        # Evidence files exist even for setup failures.
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
    except OSError as exc:
        result["status"] = "ERROR"
        result["error"] = f"Runner setup failed: {exc}"
        stdout_path.touch(exist_ok=True)
        stderr_path.touch(exist_ok=True)
    result["duration_seconds"] = time.monotonic() - start
    try:
        stdout_text = stdout_path.read_text(errors="replace")
        stderr_text = stderr_path.read_text(errors="replace")
    except OSError as exc:
        result["status"] = "ERROR"
        result["error"] = f"Could not collect diagnostic artifacts: {exc}"
        stdout_text = stderr_text = ""
    result["stdout_excerpt"] = excerpt(stdout_text)
    result["stderr_excerpt"] = excerpt(stderr_text)
    result["artifacts"] = {
        "stdout": str(stdout_path.relative_to(run_dir)),
        "stderr": str(stderr_path.relative_to(run_dir)),
    }
    (run_dir / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    (run_dir / "report.md").write_text(render_markdown(result))
    return result, run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--config", type=Path, default=Path("verification.json"),
                        help="JSON configuration (must be inside --root)")
    parser.add_argument("--profile", default="default", help="configured profile name")
    parser.add_argument("--report-dir", type=Path, default=Path(".verification-runs"),
                        help="directory for durable run reports and complete logs")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config = args.config if args.config.is_absolute() else root / args.config
    report_dir = args.report_dir if args.report_dir.is_absolute() else root / args.report_dir
    try:
        result, run_dir = run(root, config, args.profile, report_dir)
    except OSError as exc:
        print(f"ERROR: cannot create report directory: {exc}", file=sys.stderr)
        return EXIT_CODES["ERROR"]
    print(f"{result['status']}: {shlex.join(result['command']) or 'setup'}")
    print(f"Report: {run_dir / 'report.md'}")
    if result.get("error"):
        print(f"Detail: {result['error']}")
    if result.get("stdout_excerpt"):
        print("stdout: " + result["stdout_excerpt"].splitlines()[-1][:240])
    if result.get("stderr_excerpt"):
        print("stderr: " + result["stderr_excerpt"].splitlines()[-1][:240])
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
