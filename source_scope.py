"""Select repository inputs without traversing generated output trees."""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path


GENERATED_DIRS = frozenset({
    ".git", ".verification-runs", ".context-packets", ".failure-history",
    ".preflight", ".result-cache", ".work-orders", ".session-handoff",
    ".progress-guard", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv", "node_modules", "target", "dist",
    "build", "test-results", "coverage", ".next",
})


class ScopeError(Exception):
    pass


def git_paths(root: Path) -> list[str] | None:
    """Return tracked and nonignored untracked paths, or None outside Git."""
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
            capture_output=True, timeout=3, check=False,
        )
    except FileNotFoundError:
        return None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScopeError(f"Cannot inspect Git worktree: {exc}") from exc
    if probe.returncode != 0 or probe.stdout.strip() != b"true":
        return None
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScopeError(f"Cannot list Git source paths: {exc}") from exc
    if listed.returncode:
        raise ScopeError("Cannot list Git source paths: " +
                         listed.stderr.decode(errors="replace").strip()[:300])
    paths = {os.fsdecode(item) for item in listed.stdout.split(b"\0") if item}
    for name in paths:
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ScopeError(f"Git returned a path outside the requested root: {name}")
    return sorted(paths)


def excluded(rel: str, extra_patterns: tuple[str, ...] = (),
             excluded_roots: tuple[str, ...] = ()) -> bool:
    parts = Path(rel).parts
    if any(part in GENERATED_DIRS for part in parts[:-1]):
        return True
    if any(rel == root or rel.startswith(root + "/") for root in excluded_roots):
        return True
    return any(fnmatch.fnmatchcase(rel, pattern) or
               fnmatch.fnmatchcase("/" + rel, pattern) for pattern in extra_patterns)


def source_paths(root: Path, extra_patterns: tuple[str, ...] = (),
                 excluded_roots: tuple[str, ...] = ()) -> tuple[str, list[Path]]:
    """List eligible inputs; preserve missing tracked paths as tombstones in Git mode."""
    names = git_paths(root)
    if names is not None:
        return "git", [root / name for name in names
                       if not excluded(Path(name).as_posix(), extra_patterns, excluded_roots)]
    found: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        kept_dirs = []
        for name in dirs:
            path = parent / name
            rel = path.relative_to(root).as_posix()
            if excluded(rel + "/_", extra_patterns, excluded_roots):
                continue
            if path.is_symlink():
                found.append(path)
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            path = parent / name
            if not excluded(path.relative_to(root).as_posix(), extra_patterns, excluded_roots):
                found.append(path)
    return "filesystem", sorted(found, key=lambda p: p.relative_to(root).as_posix())
