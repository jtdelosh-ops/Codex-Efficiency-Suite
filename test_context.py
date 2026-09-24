import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import context


class ContextBuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "context.json").write_text("{}")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def config(self):
        return context.load_config(self.root / "context.json")[0]

    def test_non_git_fallback_and_important_files(self):
        self.write("README.md", "Project guide")
        self.write("src/feature.py", "def feature():\n    return 'needle'\n")
        packet = context.collect(self.root, "feature needle", self.root / "context.json")
        self.assertEqual("content-snapshot", packet["snapshot"]["method"])
        self.assertIn("src/feature.py", packet["changed_files"])
        self.assertIn("README.md", packet["important_files"])

    def test_git_changed_files_includes_untracked_and_modifications(self):
        self.write("app.py", "base\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "add", "app.py", "context.json"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "base"], cwd=self.root, check=True)
        self.write("app.py", "changed\n")
        self.write("new.py", "new\n")
        changed = context.git_changed(self.root)
        self.assertIn("app.py", changed)
        self.assertIn("new.py", changed)
        packet = context.collect(self.root, "", self.root / "context.json")
        self.assertEqual("git", packet["snapshot"]["method"])
        self.assertEqual(["app.py", "new.py"], packet["changed_files"])

    def test_git_deletion_is_reported_without_claiming_file_evidence(self):
        self.write("gone.py", "pass\n")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "add", "gone.py", "context.json"], cwd=self.root, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                        "commit", "-qm", "base"], cwd=self.root, check=True)
        (self.root / "gone.py").unlink()
        packet = context.collect(self.root, "", self.root / "context.json")
        self.assertEqual(["gone.py"], packet["changed_files"])
        self.assertEqual(["gone.py"], packet["deleted_files"])
        self.assertFalse(any(item["path"] == "gone.py" for item in packet["evidence"]))

    def test_include_exclude_and_important_heuristics(self):
        self.write("src/main.py", "pass")
        self.write("secret.env", "secret")
        cfg = {"include": ["src/**"], "exclude": context.DEFAULT_EXCLUDES + ["src/generated/**"],
               "important_files": ["custom.cfg"], "max_files": 5}
        self.assertTrue(context.eligible("src/main.py", cfg))
        self.assertFalse(context.eligible("secret.env", cfg))
        self.assertFalse(context.eligible("src/generated/out.py", cfg))
        self.assertTrue(context.is_important("custom.cfg", cfg))
        self.assertTrue(context.is_important("AGENTS.md", cfg))

    def test_symbol_and_search_evidence(self):
        path = self.write("src/mod.py", "class Widget:\n    pass\n\ndef build_widget():\n    return 'widget'\n")
        symbols, hits = context.source_evidence(path, ["widget"])
        self.assertEqual(["Widget", "build_widget"], [item["name"] for item in symbols])
        self.assertTrue(any("widget" in item["text"] for item in hits))

    def test_relevant_test_discovery(self):
        names = {"src/parser.py", "tests/test_parser.py", "lib/worker.rb", "lib/worker_test.py"}
        self.assertEqual(["lib/worker_test.py", "tests/test_parser.py"],
                         context.discover_tests(self.root, ["src/parser.py", "lib/worker.rb"], names))

    def test_collection_is_fresh_and_emits_packet_fields(self):
        self.write("README.md", "Guide")
        self.write("src/item.py", "def item():\n    pass\n")
        packet = context.collect(self.root, "item", self.root / "context.json")
        self.assertTrue(packet["fresh"])
        self.assertEqual(64, len(packet["snapshot"]["sha256"]))
        self.assertTrue(packet["evidence"])
        self.assertIn("# Repository Context", context.render(packet))

    def test_stale_snapshot_is_never_fresh(self):
        self.write("one.py", "pass")
        actual = context.snapshot
        calls = []
        def changing(root, cfg):
            result = actual(root, cfg)
            calls.append(result)
            if len(calls) == 2:
                return "0" * 64, result[1]
            return result
        with patch.object(context, "snapshot", side_effect=changing):
            packet = context.collect(self.root, "", self.root / "context.json")
        self.assertFalse(packet["fresh"])
        self.assertIn("stale", packet["freshness_note"])

    def test_invalid_configuration_fails_clearly(self):
        self.write("context.json", '{"max_files": 0}')
        with self.assertRaisesRegex(context.ContextError, "positive integer"):
            context.collect(self.root, "", self.root / "context.json")

    def test_cli_writes_json_and_markdown(self):
        self.write("README.md", "Guide")
        result = subprocess.run([sys.executable, str(Path(context.__file__)), "--root", str(self.root),
                                 "--config", "context.json", "--task", "guide",
                                 "--json", ".context-packets/packet.json", "--markdown", ".context-packets/packet.md"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads((self.root / ".context-packets/packet.json").read_text())
        self.assertTrue(data["fresh"])
        self.assertIn("Repository Context", (self.root / ".context-packets/packet.md").read_text())


if __name__ == "__main__":
    unittest.main()
