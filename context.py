#!/usr/bin/env python3
"""Build a small, evidence-based repository context packet."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
DEFAULT_EXCLUDES = [".git/**", ".verification-runs/**", ".context-packets/**", "__pycache__/**",
                    "**/__pycache__/**", ".env", ".env.*", "**/.env", "**/.env.*",
                    "*.pyc", "**/*.pyc", "*.pem", "**/*.pem", "*.p12", "**/*.p12",
                    "*.pfx", "**/*.pfx", "*.key", "**/*.key", "credentials.*", "**/credentials.*"]
IMPORTANT_NAMES = {"AGENTS.md", "README.md", "pyproject.toml", "package.json",
                   "Cargo.toml", "go.mod", "requirements.txt", "Makefile"}
SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
                   ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt"}
SYMBOL_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)|^\s*(?:export\s+)?(?:function|class|interface|type|const|let|var)\s+([A-Za-z_$][\w$]*)", re.M)


class ContextError(Exception):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_config(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        obj = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextError(f"Cannot read context configuration {path}: {exc}") from exc
    if not isinstance(obj, dict):
        raise ContextError("Configuration must be a JSON object.")
    for key in ("include", "exclude", "important_files"):
        if key in obj and (not isinstance(obj[key], list) or not all(isinstance(x, str) for x in obj[key])):
            raise ContextError(f"Configuration '{key}' must be an array of strings.")
    if "max_files" in obj and (isinstance(obj["max_files"], bool) or not isinstance(obj["max_files"], int) or obj["max_files"] < 1):
        raise ContextError("Configuration 'max_files' must be a positive integer.")
    return {"include": obj.get("include", []), "exclude": DEFAULT_EXCLUDES + obj.get("exclude", []),
            "important_files": obj.get("important_files", []), "max_files": obj.get("max_files", 80)}, raw


def git_changed(root: Path) -> list[str] | None:
    """Return changed tracked and untracked paths; None means Git is unavailable."""
    try:
        check = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                               capture_output=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if check.returncode or Path(os.fsdecode(check.stdout).strip()).resolve() != root.resolve():
        return None
    try:
        tracked = subprocess.run(["git", "diff", "HEAD", "--name-only", "-z"], cwd=root,
                                 capture_output=True, timeout=5, check=False)
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root,
                                   capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if tracked.returncode or untracked.returncode:
        return None
    names = set(os.fsdecode(x) for x in tracked.stdout.split(b"\0") if x)
    names.update(os.fsdecode(x) for x in untracked.stdout.split(b"\0") if x)
    return sorted(names)


def matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase("/" + path, pattern)


def eligible(rel: str, cfg: dict[str, Any]) -> bool:
    if any(matches(rel, p) for p in cfg["exclude"]):
        return False
    return not cfg["include"] or any(matches(rel, p) for p in cfg["include"])


def snapshot(root: Path, cfg: dict[str, Any]) -> tuple[str, dict[str, str]]:
    records: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        rel = path.relative_to(root).as_posix()
        if not eligible(rel, cfg) or not path.is_file() or path.is_symlink():
            continue
        try:
            records[rel] = sha(path.read_bytes())
        except OSError as exc:
            raise ContextError(f"Cannot read repository file {rel}: {exc}") from exc
    digest = hashlib.sha256()
    for rel, value in records.items():
        digest.update(rel.encode("utf-8", "surrogateescape") + b"\0" + value.encode() + b"\n")
    return digest.hexdigest(), records


def is_important(rel: str, cfg: dict[str, Any]) -> bool:
    name = Path(rel).name
    return name in IMPORTANT_NAMES or name in cfg["important_files"] or name.startswith("AGENTS.")


def source_evidence(path: Path, terms: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if path.suffix.lower() not in SOURCE_SUFFIXES and path.name not in {"AGENTS.md", "README.md"}:
        return [], []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []
    symbols = [{"name": a or b, "line": text.count("\n", 0, m.start()) + 1}
               for m in SYMBOL_RE.finditer(text) for a, b in [m.groups()]]
    hits: list[dict[str, Any]] = []
    if terms:
        lowered = [term.casefold() for term in terms]
        for number, line in enumerate(text.splitlines(), 1):
            if any(term in line.casefold() for term in lowered):
                hits.append({"line": number, "text": line.strip()[:280]})
                if len(hits) >= 8:
                    break
    return symbols[:80], hits


def task_terms(task: str) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", task)
    stop = {"the", "and", "for", "with", "from", "that", "this", "into", "when", "how", "use", "add", "fix"}
    seen: set[str] = set()
    return [w for w in words if w.casefold() not in stop and not (w.casefold() in seen or seen.add(w.casefold()))][:12]


def discover_tests(root: Path, changed: list[str], all_paths: set[str]) -> list[str]:
    found: set[str] = set()
    for rel in changed:
        stem = Path(rel).stem
        base = stem[5:] if stem.startswith("test_") else stem[:-5] if stem.endswith("_test") else stem
        candidates = {f"test_{base}.py", f"{base}_test.py", f"tests/test_{base}.py",
                      f"tests/{base}_test.py", f"{Path(rel).parent.as_posix()}/test_{base}.py",
                      f"{Path(rel).parent.as_posix()}/{base}_test.py"}
        for candidate in candidates:
            if candidate in all_paths and (candidate.startswith("test_") or "/test_" in candidate or candidate.endswith("_test.py")):
                found.add(candidate)
    return sorted(found)


def collect(root: Path, task: str, config_path: Path) -> dict[str, Any]:
    root = root.resolve()
    try:
        cfgpath = config_path.resolve()
        cfgpath.relative_to(root)
    except ValueError as exc:
        raise ContextError("Configuration file must be inside repository root.") from exc
    cfg, config_bytes = load_config(cfgpath)
    before, records = snapshot(root, cfg)
    git_paths = git_changed(root)
    source = "git" if git_paths is not None else "content-snapshot"
    changed = [p for p in (git_paths if git_paths is not None else list(records))
               if eligible(p, cfg)]
    deleted = [p for p in changed if p not in records]
    important = [p for p in records if is_important(p, cfg)]
    terms = task_terms(task)
    selected = list(dict.fromkeys(changed + important))[:cfg["max_files"]]
    evidence = []
    for rel in selected:
        if rel not in records:
            continue
        symbols, hits = source_evidence(root / rel, terms)
        evidence.append({"path": rel, "sha256": records[rel], "symbols": symbols, "search_hits": hits})
    all_paths = set(records)
    likely_tests = discover_tests(root, changed, all_paths)
    tests = []
    for rel in likely_tests:
        symbols, hits = source_evidence(root / rel, terms)
        tests.append({"path": rel, "sha256": records[rel], "symbols": symbols, "search_hits": hits})
    after, _ = snapshot(root, cfg)
    fresh = before == after
    return {"schema_version": 1, "tool": "repository-context-builder", "tool_version": VERSION,
            "repository": str(root), "snapshot": {"sha256": before, "method": source,
            "file_count": len(records)}, "fresh": fresh,
            "freshness_note": "Inputs were unchanged during collection." if fresh else "Inputs changed during collection; packet is stale.",
            "config_sha256": sha(config_bytes), "task": task, "retrieval_terms": terms,
            "changed_files": changed, "deleted_files": deleted,
            "important_files": important, "likely_tests": tests,
            "evidence": evidence}


def render(packet: dict[str, Any]) -> str:
    lines = ["# Repository Context", "", f"- Snapshot: `{packet['snapshot']['sha256']}` ({packet['snapshot']['method']})",
             f"- Fresh: **{str(packet['fresh']).lower()}** — {packet['freshness_note']}",
             f"- Changed files: {len(packet['changed_files'])}", "", "## Changed files", ""]
    lines += [f"- `{p}`" for p in packet["changed_files"]] or ["- None detected"]
    lines += ["", "## Deleted files", ""]
    lines += [f"- `{p}`" for p in packet.get("deleted_files", [])] or ["- None detected"]
    lines += ["", "## Important files", ""]
    lines += [f"- `{p}`" for p in packet["important_files"]] or ["- None selected"]
    lines += ["", "## Likely tests", ""]
    lines += [f"- `{x['path']}`" for x in packet["likely_tests"]] or ["- None found"]
    lines += ["", "## Source evidence", ""]
    for item in packet["evidence"]:
        lines += [f"### `{item['path']}`", ""]
        if item["symbols"]:
            lines += ["Symbols: " + ", ".join(f"`{s['name']}` (line {s['line']})" for s in item["symbols"]), ""]
        for hit in item["search_hits"]:
            lines += [f"- L{hit['line']}: {hit['text']}"]
        if item["search_hits"]:
            lines.append("")
    lines += ["## Retrieval", "", "Task: " + (packet["task"] or "(not supplied)"),
              "Terms: " + (", ".join(packet["retrieval_terms"]) or "(none)"), ""]
    if not packet["fresh"]:
        lines += ["**Do not treat this packet as current. Regenerate it.**", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("context.json"))
    parser.add_argument("--task", default="", help="brief task description used for direct text search")
    parser.add_argument("--json", type=Path, default=Path(".context-packets/context-packet.json"))
    parser.add_argument("--markdown", type=Path, default=Path(".context-packets/context-packet.md"))
    args = parser.parse_args(argv)
    try:
        packet = collect(args.root, args.task, args.config if args.config.is_absolute() else args.root / args.config)
        for output in (args.json, args.markdown):
            target = output if output.is_absolute() else args.root / output
            target.parent.mkdir(parents=True, exist_ok=True)
        json_path = args.json if args.json.is_absolute() else args.root / args.json
        md_path = args.markdown if args.markdown.is_absolute() else args.root / args.markdown
        json_path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(render(packet), encoding="utf-8")
        print(f"Context packet: {'FRESH' if packet['fresh'] else 'STALE'} {packet['snapshot']['sha256']}")
        return 0 if packet["fresh"] else 2
    except (ContextError, OSError) as exc:
        print(f"context: error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
