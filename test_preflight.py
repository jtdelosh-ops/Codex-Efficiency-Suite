import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import preflight


class PreflightTests(unittest.TestCase):
    def test_missing_requirements_fail_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            result = preflight.run({"executables": ["not-a-real-executable-xyz"], "paths": [{"path": "absent"}], "environment": ["PREFLIGHT_SECRET_TEST"]}, Path(directory))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual([c["status"] for c in result["checks"]], ["FAIL", "FAIL", "FAIL"])

    def test_python_version_constraints(self):
        self.assertTrue(preflight.satisfies("Python 3.12.1", ">=3.10"))
        self.assertFalse(preflight.satisfies("3.9.9", ">=3.10"))
        with self.assertRaises(ValueError):
            preflight.satisfies("3.12", "~=3.10")

    def test_environment_records_presence_only(self):
        with patch.dict(os.environ, {"PREFLIGHT_SECRET_TEST": "never-write-this"}):
            result = preflight.run({"environment": ["PREFLIGHT_SECRET_TEST"]}, Path.cwd())
        rendered = json.dumps(result)
        self.assertEqual(result["status"], "PASS")
        self.assertNotIn("never-write-this", rendered)

    def test_command_failure_fails(self):
        result = preflight.run({"commands": [{"command": ["python3", "-c", "raise SystemExit(2)"]}]}, Path.cwd())
        self.assertEqual(result["status"], "FAIL")

    def test_malformed_configuration_errors(self):
        with self.assertRaises(preflight.PreflightError):
            preflight.run({"paths": "bad"}, Path.cwd())

    def test_cli_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = root / "cfg.json"
            cfg.write_text('{"paths": [{"path": "cfg.json"}]}', encoding="utf-8")
            with patch("sys.stdout"):
                code = preflight.main(["--root", str(root), "--config", str(cfg), "--json-out", str(root / "out.json"), "--markdown-out", str(root / "out.md")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads((root / "out.json").read_text())["status"], "PASS")
            self.assertIn("Environment Preflight: PASS", (root / "out.md").read_text())


if __name__ == "__main__":
    unittest.main()
