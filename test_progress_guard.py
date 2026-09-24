import unittest

from progress_guard import GuardError, decide


class ProgressGuardTests(unittest.TestCase):
    def test_first_failure_continues(self):
        self.assertEqual(decide({"attempts": [{"signature": "x"}]})["decision"], "CONTINUE")

    def test_repeated_same_failure_stops_at_threshold_even_if_text_changes(self):
        rows = [{"signature": "x", "summary": f"retry wording {i}"} for i in range(3)]
        result = decide({"attempts": rows})
        self.assertEqual(result["decision"], "STOP")
        self.assertEqual(len(result["matched_history"]), 3)

    def test_new_evidence_or_approach_continues(self):
        rows = [{"signature": "x", "evidence_id": "a", "approach_id": "one"},
                {"signature": "x", "evidence_id": "b", "approach_id": "one"}]
        result = decide({"attempts": rows})
        self.assertEqual(result["decision"], "CONTINUE")
        self.assertEqual(len(result["matched_history"]), 1)
        rows[1] = {"signature": "x", "evidence_id": "a", "approach_id": "two"}
        self.assertEqual(decide({"attempts": rows})["decision"], "CONTINUE")

    def test_threshold_override(self):
        self.assertEqual(decide({"attempts": [{"signature": "x"}]}, threshold=1)["decision"], "STOP")

    def test_malformed_input(self):
        with self.assertRaises(GuardError): decide({"attempts": [{"summary": "no signature"}]})
        with self.assertRaises(GuardError): decide({"attempts": []}, threshold=0)

    def test_history_and_output_are_bounded(self):
        result = decide({"attempts": [{"signature": "x", "summary": "z" * 900} for _ in range(100)]}, limit=4)
        self.assertEqual(len(result["matched_history"]), 4)
        self.assertLessEqual(len(result["matched_history"][0]["summary"]), 500)
        with self.assertRaises(GuardError): decide({"attempts": [{"signature": "x"}] * 101})

    def test_long_identifiers_are_compared_in_full(self):
        first = "e" * 600 + "a"
        second = "e" * 600 + "b"
        rows = [{"signature": "x", "evidence_id": first},
                {"signature": "x", "evidence_id": second}]
        result = decide({"attempts": rows}, threshold=2)
        self.assertEqual(result["decision"], "CONTINUE")
        self.assertEqual(result["matched_history"], [{"signature": "x", "evidence_id": second}])

    def test_oversized_identifier_is_rejected(self):
        with self.assertRaises(GuardError):
            decide({"attempts": [{"signature": "x", "evidence_id": "e" * 1025}]})


if __name__ == "__main__": unittest.main()
