# Codex Efficiency Suite

The current suite provides a deterministic verification runner, a lightweight repository context builder, and an experimental Work-Order Builder. See [suite-manifest.json](suite-manifest.json) for tool status/contracts and [CODEX-SUITE-GUIDE.md](CODEX-SUITE-GUIDE.md) for direct Codex usage instructions.

## Work-Order Builder (experimental)

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
      "command": ["python3", "-m", "unittest", "discover", "-v"],
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
