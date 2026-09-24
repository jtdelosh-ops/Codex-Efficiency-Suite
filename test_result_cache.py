import json
import tempfile
import unittest
from pathlib import Path

from result_cache import CacheError, get, put, query


KEY = {"tool": "ctx", "source": "a" * 64, "configuration": "b" * 64,
       "environment": "c" * 64, "dependencies": "d" * 64}


class ResultCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / "cache.json"

    def tearDown(self): self.temp.cleanup()

    def test_hit(self):
        put(self.path, KEY, {"answer": "ok"}, now=10)
        result = get(self.path, KEY, now=11)
        self.assertEqual(result["status"], "HIT"); self.assertEqual(result["value"], {"answer": "ok"})

    def test_missing_key(self):
        self.assertEqual(get(self.path, KEY)["reason"], "no matching record")

    def test_source_config_environment_and_dependency_changes_miss(self):
        put(self.path, KEY, {"answer": "ok"}, now=10)
        for field in ("source", "configuration", "environment", "dependencies"):
            altered = dict(KEY, **{field: ("e" if KEY[field][0] != "e" else "f") * 64})
            result = get(self.path, altered, now=11)
            self.assertEqual(result["status"], "MISS", field)
            self.assertIn(field, result["reason"], field)

    def test_expiry(self):
        put(self.path, KEY, {"answer": "ok"}, ttl_seconds=5, now=10)
        self.assertEqual(get(self.path, KEY, now=14)["status"], "HIT")
        self.assertEqual(get(self.path, KEY, now=15)["reason"], "record expired")

    def test_bounds(self):
        for i in range(1002):
            put(self.path, dict(KEY, source=f"{i:064x}"), {"n": i}, now=1)
        self.assertEqual(len(json.loads(self.path.read_text())), 1000)
        self.assertEqual(query(self.path, 100)["count"], 100)
        with self.assertRaises(CacheError): query(self.path, 101)
        put(self.path, KEY, {"large": "x" * 9000}, now=2)
        self.assertLessEqual(len(json.dumps(get(self.path, KEY, now=2)["value"]).encode()), 2100)

    def test_malformed_input_and_store(self):
        with self.assertRaises(CacheError): put(self.path, {"tool": "ctx"}, {})
        self.path.write_text("not json")
        with self.assertRaises(CacheError): get(self.path, KEY)

    def test_secret_fields_and_assignments_never_leak(self):
        with self.assertRaises(CacheError): put(self.path, KEY, {"api_token": "do-not-store"})
        with self.assertRaises(CacheError): put(self.path, KEY, {"note": "password=hunter2"})
        self.assertFalse(self.path.exists())

    def test_key_order_serialization_is_deterministic(self):
        other = dict(reversed(list(KEY.items())))
        put(self.path, KEY, {"z": 1, "a": 2}, now=5)
        self.assertEqual(get(self.path, other, now=5)["status"], "HIT")


if __name__ == "__main__": unittest.main()
