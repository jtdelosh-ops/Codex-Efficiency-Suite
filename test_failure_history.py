import json
import tempfile
import unittest
from pathlib import Path

import failure_history as history


class FailureHistoryTests(unittest.TestCase):
    def test_append_query_and_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "events.jsonl"
            self.assertEqual(history.append(store, [{"tool": "verify", "status": "FAIL", "summary": "broken"}]), 1)
            self.assertEqual(history.append(store, [{"tool": "context", "status": "ERROR", "summary": "stale"}]), 1)
            rows = history.query(history.read_store(store), tool="verify", status="FAIL")
            self.assertEqual(len(rows), 1)

    def test_stable_signature_and_deduplication(self):
        event = {"tool": "verify", "status": "FAIL", "summary": "same"}
        self.assertEqual(history.normalize(event)["signature"], history.normalize(event)["signature"])
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "events.jsonl"
            self.assertEqual(history.append(store, [event, event], deduplicate=True), 1)

    def test_secret_redaction_and_no_raw_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "events.jsonl"
            history.append(store, [{"tool": "x", "status": "FAIL", "summary": "TOKEN=supersecret", "stdout": "unbounded log"}])
            raw = store.read_text()
            self.assertNotIn("supersecret", raw)
            self.assertNotIn("unbounded log", raw)
            self.assertIn("[REDACTED]", raw)

    def test_malformed_input_and_store_rejected(self):
        with self.assertRaises(history.HistoryError):
            history.normalize({"tool": ""})
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "bad.jsonl"
            store.write_text("{broken\n")
            with self.assertRaises(history.HistoryError):
                history.read_store(store)

    def test_recent_output_is_bounded(self):
        rows = [{"tool": "x", "status": "FAIL", "signature": str(i)} for i in range(250)]
        self.assertEqual(len(history.query(rows, limit=12)), 12)
        with self.assertRaises(history.HistoryError):
            history.query(rows, limit=101)

    def test_cli_append_and_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "in.json"
            source.write_text(json.dumps({"tool": "sample", "status": "FAIL", "summary": "bad"}))
            self.assertEqual(history.main(["append", "--store", str(root / "h.jsonl"), "--input", str(source)]), 0)
            self.assertEqual(history.main(["query", "--store", str(root / "h.jsonl"), "--tool", "sample", "--format", "json"]), 0)


if __name__ == "__main__":
    unittest.main()
