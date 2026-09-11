from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


context = load("get_context", ROOT / "bin/get_context.py")


class OpenCodeTests(unittest.TestCase):
    def test_skips_streaming_zero_output_and_uses_tui_formula(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "opencode.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE message(id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)")
            complete = {
                "role": "assistant",
                "tokens": {"input": 10, "output": 5, "reasoning": 3, "cache": {"read": 7, "write": 2}},
                "providerID": "p", "modelID": "m", "time": {"completed": 1000},
            }
            streaming = {
                "role": "assistant",
                "tokens": {"input": 999, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                "providerID": "p", "modelID": "new-model",
            }
            conn.execute("INSERT INTO message VALUES(?,?,?,?)", ("a", "ses", 1, json.dumps(complete)))
            conn.execute("INSERT INTO message VALUES(?,?,?,?)", ("b", "ses", 2, json.dumps(streaming)))
            conn.commit()
            conn.close()
            result = context.get_opencode("ses", db, 100)
            self.assertEqual(result["total"], 27)
            self.assertEqual(result["source"], "estimated")
            self.assertEqual(result["freshness"], "stale-model-change")


class CodexTests(unittest.TestCase):
    def test_uses_last_usage_not_cumulative_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "rollout.jsonl"
            event = {
                "timestamp": "2026-01-01T00:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {"total_tokens": 120},
                        "total_token_usage": {"total_tokens": 900000},
                        "model_context_window": 1000,
                    },
                },
            }
            rollout.write_text(json.dumps(event) + "\n", encoding="utf-8")
            db = root / "state.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, rollout_path TEXT)")
            session_id = "12345678-1234-1234-1234-123456789abc"
            conn.execute("INSERT INTO threads VALUES(?,?)", (session_id, str(rollout)))
            conn.commit()
            conn.close()
            result = context.get_codex(session_id, db, None)
            self.assertEqual(result["total"], 120)
            self.assertEqual(result["window"], 1000)
            self.assertEqual(result["source"], "precise")


class PiHelperTests(unittest.TestCase):
    def test_active_branch_ignores_abandoned_high_usage(self):
        fixture = ROOT / "tests/fixtures/pi-branched.jsonl"
        proc = subprocess.run(
            ["node", str(ROOT / "bin/pi_context.mjs"), str(fixture)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["total"], 1234)
        self.assertEqual(result["freshness"], "completed-call")


class PiWindowTests(unittest.TestCase):
    def test_parses_human_readable_sizes(self):
        self.assertEqual(context.parse_model_size("1M"), 1024 * 1024)
        self.assertEqual(context.parse_model_size("128K"), 128 * 1024)
        self.assertEqual(context.parse_model_size("272K"), 272 * 1024)
        self.assertEqual(context.parse_model_size("1.5M"), int(1.5 * 1024 * 1024))
        self.assertIsNone(context.parse_model_size("large"))
        self.assertIsNone(context.parse_model_size(""))

    def test_uses_pi_catalog_when_the_session_registry_has_no_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / "session.jsonl"
            session.write_text("{}\n", encoding="utf-8")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_pi = fake_bin / "pi"
            fake_pi.write_text(
                "#!/usr/bin/env python3\n"
                "print('provider      model                         context  max-out  thinking  images')\n"
                "print('opencode-go   deepseek-v4.1-flash           1M       384K     yes       yes')\n",
                encoding="utf-8",
            )
            fake_pi.chmod(0o755)
            helper = root / "helper.mjs"
            helper.write_text(
                "process.stdout.write(JSON.stringify({total: 10, window: null, "
                "freshness: 'completed-call', observed_at: '2026-01-01T00:00:00Z', "
                "provider: 'opencode-go', model: 'deepseek-v4.1-flash'}));\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["PI_SESSION_DIR"] = str(root)
            with unittest.mock.patch.dict(os.environ, env, clear=True):
                result = context.get_pi(str(session), helper, None)
            self.assertEqual(result["window"], 1024 * 1024)
            self.assertEqual(result["window_source"], "pi --list-models")
            self.assertEqual(result["source"], "precise")

            with unittest.mock.patch.dict(os.environ, env, clear=True):
                explicit = context.get_pi(str(session), helper, 5000)
            self.assertEqual(explicit["window"], 5000)
            self.assertEqual(explicit["window_source"], "explicit --window")
            self.assertEqual(explicit["source"], "estimated")


if __name__ == "__main__":
    unittest.main()
