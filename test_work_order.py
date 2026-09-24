import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import work_order


VALID = {
    "goal": "Add a bounded export feature.",
    "context": "The project has an existing report command.",
    "constraints": ["No new dependencies", "Keep output deterministic"],
    "expected_files_or_subsystems": ["report/export.py", "tests/"],
    "acceptance_criteria": ["CSV is valid UTF-8", "Existing tests pass"],
    "required_verification": ["python3 -m unittest -v"],
    "non_goals": ["Do not redesign the UI", "Do not change the schema"],
    "stop_condition": "Stop after implementation, tests, and docs are complete.",
    "preferred_model": "Luna Medium",
    "escalation_evidence": ["Escalate only after a reproducible blocker and focused retries."],
}


class WorkOrderTests(unittest.TestCase):
    def test_valid_json_and_markdown_rendering(self):
        result = work_order.validate_work_order(VALID)
        rendered = json.loads(work_order.render_json(result))
        self.assertEqual(rendered["work_order"], VALID)
        markdown = work_order.render_markdown(result)
        self.assertIn("# Work Order", markdown)
        self.assertIn("## Acceptance Criteria", markdown)
        self.assertIn("## Escalation Evidence", markdown)

    def test_missing_fields_rejected_without_filling_them(self):
        data = dict(VALID)
        del data["acceptance_criteria"]
        with self.assertRaisesRegex(work_order.WorkOrderError, "acceptance_criteria"):
            work_order.validate_work_order(data)

    def test_malformed_types_and_empty_values_rejected(self):
        for field, value in (("constraints", "not an array"), ("goal", "  "), ("non_goals", ["ok", ""]), ("preferred_model", None)):
            data = dict(VALID)
            data[field] = value
            with self.subTest(field=field), self.assertRaises(work_order.WorkOrderError):
                work_order.validate_work_order(data)
        with self.assertRaisesRegex(work_order.WorkOrderError, "JSON object"):
            work_order.validate_work_order([])

    def test_constraints_and_non_goals_are_preserved_verbatim(self):
        work_order_data = work_order.validate_work_order(VALID)
        output = work_order.render_markdown(work_order_data)
        for item in VALID["constraints"] + VALID["non_goals"]:
            self.assertIn(f"- {item}", output)
        self.assertEqual(work_order_data["stop_condition"], VALID["stop_condition"])

    def test_cli_creates_both_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            source.write_text(json.dumps(VALID), encoding="utf-8")
            json_out, md_out = root / "out" / "order.json", root / "out" / "order.md"
            with patch("sys.stdout"):
                code = work_order.main(["--input", str(source), "--json-out", str(json_out), "--markdown-out", str(md_out)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(json_out.read_text(encoding="utf-8"))["work_order"], VALID)
            self.assertIn("# Work Order", md_out.read_text(encoding="utf-8"))

    def test_cli_malformed_json_reports_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.json"
            source.write_text("{bad", encoding="utf-8")
            with patch("sys.stderr") as stderr:
                code = work_order.main(["--input", str(source), "--json-out", "x.json", "--markdown-out", "x.md"])
            self.assertEqual(code, 2)
            self.assertIn("error", "".join(call.args[0] for call in stderr.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
