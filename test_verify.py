import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import verify


class VerificationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "verification.json"
        self.reports = self.root / ".verification-runs"
        (self.root / "tracked.txt").write_text("initial\n")

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, command, timeout=2, dependencies=None):
        self.config.write_text(json.dumps({"profiles": {"default": {
            "command": command, "timeout_seconds": timeout,
            "environment": ["VERIFY_TEST_INPUT"],
            "dependency_files": dependencies or [],
        }}}))

    def invoke(self):
        return verify.run(self.root, self.config, "default", self.reports)

    def test_pass_preserves_stdout_and_snapshot(self):
        self.configure([sys.executable, "-c", "print('verified')"])
        result, directory = self.invoke()
        self.assertEqual("PASS", result["status"])
        self.assertEqual("verified\n", (directory / "stdout.log").read_text())
        self.assertEqual(0, result["exit_code"])
        self.assertEqual(verify.source_snapshot(self.root, self.reports), result["snapshot"])
        self.assertTrue((directory / "report.json").is_file())
        self.assertTrue((directory / "report.md").is_file())

    def test_nonzero_exit_is_failure_and_stderr_is_preserved(self):
        self.configure([sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(7)"])
        result, directory = self.invoke()
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(7, result["exit_code"])
        self.assertIn("bad", (directory / "stderr.log").read_text())

    def test_timeout_is_not_pass_and_keeps_partial_output(self):
        self.configure([sys.executable, "-c", "import time; print('started', flush=True); time.sleep(2)"], timeout=.1)
        result, directory = self.invoke()
        self.assertEqual("TIMEOUT", result["status"])
        self.assertIn("timeout", result["error"].lower())
        self.assertIn("started", (directory / "stdout.log").read_text())

    def test_missing_executable_is_execution_error(self):
        self.configure([str(self.root / "missing-executable")])
        result, _ = self.invoke()
        self.assertEqual("ERROR", result["status"])
        self.assertIn("Could not execute", result["error"])

    def test_invalid_config_is_setup_error_and_writes_report(self):
        self.config.write_text("{")
        result, directory = self.invoke()
        self.assertEqual("ERROR", result["status"])
        self.assertIn("Cannot read configuration", result["error"])
        self.assertTrue((directory / "report.json").is_file())

    def test_missing_declared_dependency_is_setup_error(self):
        self.configure([sys.executable, "-c", "pass"], dependencies=["absent.lock"])
        result, _ = self.invoke()
        self.assertEqual("ERROR", result["status"])
        self.assertIn("dependency input is missing", result["error"])

    def test_working_tree_change_invalidates_zero_exit(self):
        script = ("from pathlib import Path; import time; time.sleep(.15); "
                  "Path('tracked.txt').write_text('changed\\n')")
        self.configure([sys.executable, "-c", script])
        result, _ = self.invoke()
        self.assertEqual("ERROR", result["status"])
        self.assertTrue(result["snapshot_changed"])

    def test_snapshot_tracks_untracked_files_and_ignores_reports(self):
        before = verify.source_snapshot(self.root, self.reports)
        (self.root / "new-untracked.txt").write_text("new\n")
        after = verify.source_snapshot(self.root, self.reports)
        self.assertNotEqual(before["tree_sha256"], after["tree_sha256"])

    def test_config_must_be_within_repository(self):
        outside = self.root.parent / "outside-verification-config.json"
        try:
            outside.write_text(json.dumps({"profiles": {"default": {
                "command": [sys.executable, "-c", "pass"], "timeout_seconds": 2}}}))
            result, _ = verify.run(self.root, outside, "default", self.reports)
            self.assertEqual("ERROR", result["status"])
            self.assertIn("inside the repository", result["error"])
        finally:
            outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
