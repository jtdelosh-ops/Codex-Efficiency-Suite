# Codex Efficiency Suite

For cross-platform JSON command configurations, use the portable `{python}` token. The runner and preflight resolve it to the active Python interpreter (`sys.executable`), avoiding Windows Store `python3` aliases. When launching the suite itself from a shell, use the interpreter available on that platform.

The suite provides a deterministic Verification Runner, Repository Context Builder, Work-Order Builder, Environment Preflight, Failure History Keeper, Session Handoff Builder, No-Progress Circuit Breaker, and Snapshot-Aware Cache Reuse. See [suite-manifest.json](suite-manifest.json) for each tool's status and contract, and [CODEX-SUITE-GUIDE.md](CODEX-SUITE-GUIDE.md) for when and how Codex should use them. All are local, dependency-free tools; none calls a model or network service.

## No-Progress Circuit Breaker

Use after repeated equivalent failures. Stable signatures detect repeats; only explicit changed evidence/approach identifiers count as materially new, not changed summary wording.

```sh
python3 progress_guard.py --input progress-guard-example.json --json-out .progress-guard/decision.json --markdown-out .progress-guard/decision.md
```

## Snapshot-Aware Cache Reuse

Cache deterministic outputs only when every key input matches: tool, source, configuration, environment, and dependency fingerprints. Never supply raw secrets or unbounded logs.

```sh
python3 result_cache.py put --input result-cache-example.json --store .result-cache/cache.json
python3 result_cache.py get --input result-cache-example.json --store .result-cache/cache.json
python3 result_cache.py query --store .result-cache/cache.json --limit 20
```

## Environment Preflight

Check prerequisites declared in `preflight.json` before implementation:

```sh
python3 preflight.py --root . --config preflight.json
```

It reports PASS/FAIL/ERROR as JSON and Markdown. Environment values are never exposed; optional checks run without a shell under a bounded timeout.

## Failure History Keeper

Append concise outcomes and query bounded recent history:

```sh
python3 failure_history.py append --input failure-history-example.json --store .failure-history/events.jsonl --deduplicate
python3 failure_history.py query --store .failure-history/events.jsonl --limit 20
```

Only normalized bounded summaries are stored; arbitrary payloads and logs are omitted. Review local summaries before sharing.

## Session Handoff Builder

Render structured session state into a bounded handoff:

```sh
python3 session_handoff.py --input session-handoff-example.json
```

The required fields and output limits are documented in [CODEX-SUITE-GUIDE.md](CODEX-SUITE-GUIDE.md). Blockers and failed/unknown checks are preserved; completion is never inferred.

## Work-Order Builder (stable)

Validate a complete structured task brief and render JSON and Markdown without inventing requirements:

```sh
python3 work_order.py --input work-order.json --json-out .work-orders/work-order.json --markdown-out .work-orders/work-order.md
```

All required fields must be supplied; see `work-order.json` for an example. Review both generated outputs before implementation.

## Repository Context Builder

Build task-specific context from direct file evidence, with a snapshot fingerprint:

```sh
python3 context.py --root . --config context.json --task "describe the task"
```

It writes `.context-packets/context-packet.json` and `.context-packets/context-packet.md`. Configure include/exclude patterns and important filenames in `context.json`. Git mode lists tracked working-tree changes and untracked files; deleted tracked paths are reported separately without source evidence. Outside Git it explicitly falls back to listing eligible files under a content snapshot. The packet is marked stale if eligible inputs change during collection.

## Verification runner

This small, deterministic runner executes one explicitly configured command, preserves its full stdout/stderr, and emits JSON plus a concise Markdown report. It has no third-party dependencies and does not use a model to interpret exit status.

## Run it

From the project root:

```sh
python3 verify.py --root . --config verification.json --profile default
```

Edit `verification.json` to define the command as an argument array (never shell text), a positive timeout in seconds, environment variable names relevant to the check, and dependency/lock files whose bytes should be fingerprinted. Commands run with the repository root as their working directory and inherit the current environment. Declared environment values are hashed, never copied into reports. Required declared dependency files must exist. Keep the command explicit and safe for local execution.

Each invocation creates a unique directory under `.verification-runs/` containing `report.json`, `report.md`, `stdout.log`, and `stderr.log`. Retain this directory as local evidence; logs can contain sensitive output, so do not publish them without review. The report includes repository path, Git HEAD when available, a content hash covering tracked, modified, and untracked files, command/configuration and environment/dependency fingerprints, exit status, complete log paths, and bounded diagnostic excerpts. Report output itself is excluded from the source fingerprint.

Exit codes are `0` for `PASS`, `1` for `FAIL`, `2` for `TIMEOUT`, and `3` for setup/execution `ERROR`. A nonzero command exit is always a failure; missing/invalid configuration, missing declared inputs, unavailable commands, incomplete log collection, or a changed source tree invalidate success. Do not reuse old results as current: compare the report's source and environment fingerprints with the candidate being assessed, and rerun final acceptance checks on the final candidate. The MVP deliberately does not cache results or claim immutable-snapshot isolation.

## Configure a project

```json
{
  "profiles": {
    "default": {
      "command": ["{python}", "-m", "unittest", "discover", "-v"],
      "timeout_seconds": 120,
      "environment": [],
      "dependency_files": ["requirements.lock"]
    }
  }
}
```

Use a platform-appropriate executable. Add only environment names and dependency files that can affect the check. This MVP runs one configured command per invocation; profile orchestration, test discovery, browser automation, concurrent runs, result reuse, and cross-platform installation are intentionally out of scope.

## Tests

Run the runner's own deterministic suite with:

```sh
python3 -m unittest -v
```

It exercises successful verification, intentional failure, timeout, command/setup errors, missing dependency input, full diagnostic preservation, snapshot freshness, and report generation.

The suite also includes a no-progress circuit breaker (`progress_guard.py`) and snapshot-aware result cache (`result_cache.py`). See `CODEX-SUITE-GUIDE.md` and `suite-manifest.json` for their safe invocation and adoption rules.
