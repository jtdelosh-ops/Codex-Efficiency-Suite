import json
import tempfile
import unittest
from pathlib import Path

import session_handoff as handoff


VALID = {"goal": "Ship export", "completed_work": ["Added writer"], "changed_files": ["a.py"],
         "verification_results": [{"status": "PASS", "command": "tests"}], "blockers": [],
         "unresolved_issues": [], "decisions": [], "next_action": "Review diff"}


class SessionHandoffTests(unittest.TestCase):
    def test_valid_json_and_markdown(self):
        result = handoff.validate(VALID)
        self.assertEqual(result["overall_status"], "READY")
        self.assertIn("## Next Action", handoff.render_markdown(result))
        self.assertIn("not_asserted", result["completion_claim"])

    def test_missing_fields_rejected(self):
        with self.assertRaisesRegex(handoff.HandoffError, "next_action"):
            handoff.validate({k: v for k, v in VALID.items() if k != "next_action"})

    def test_failed_verification_and_blockers_preserved(self):
        result = handoff.validate({**VALID, "verification_results": [{"status": "FAIL"}], "blockers": ["Need credentials"]})
        self.assertEqual(result["overall_status"], "BLOCKED")
        self.assertEqual(result["blockers"], ["Need credentials"])

    def test_evidence_truncated_and_item_count_bounded(self):
        result = handoff.validate({**VALID, "completed_work": ["x" * 2000 for _ in range(35)]})
        self.assertEqual(len(result["completed_work"]), 31)
        self.assertTrue(result["completed_work"][0].endswith("[truncated]"))
        self.assertIn("additional items omitted", result["completed_work"][-1])

    def test_cli_writes_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "in.json"
            source.write_text(json.dumps(VALID))
            self.assertEqual(handoff.main(["--input", str(source), "--json-out", str(root / "out.json"), "--markdown-out", str(root / "out.md")]), 0)
            self.assertEqual(json.loads((root / "out.json").read_text())["overall_status"], "READY")
            self.assertIn("Session Handoff", (root / "out.md").read_text())


if __name__ == "__main__":
    unittest.main()
