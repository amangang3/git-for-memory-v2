"""Offline checks: no Claude calls, no network. Run with `python3 -m unittest -v`."""
import tempfile
import unittest
from pathlib import Path

from gfm.redact import redact
from gfm.store import Store
from gfm.traces import read_turns
from gfm.viewer import build

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-session.jsonl"


class ReaderTests(unittest.TestCase):
    def test_demo_session_splits_into_turns(self):
        turns = read_turns(DEMO)
        self.assertEqual(len(turns), 4)
        self.assertIn("Taskly", turns[0].prompt)

    def test_errors_and_rejections_are_kept(self):
        turns = read_turns(DEMO)
        self.assertTrue(any(e.startswith("[TOOL ERROR Bash]") for e in turns[1].events))
        self.assertTrue(any(e.startswith("[USER REJECTED Edit]") for e in turns[2].events))

    def test_session_folder_is_hidden(self):
        text = "\n".join(t.render() for t in read_turns(DEMO))
        self.assertNotIn("/home/demo/taskly", text)
        self.assertIn("[session folder]", text)

    def test_resume_reads_only_new_turns(self):
        turns = read_turns(DEMO)
        later = read_turns(DEMO, start_line=turns[1].end_line)
        self.assertEqual([t.index for t in later], [3, 4])


class RedactTests(unittest.TestCase):
    def test_secrets_are_masked(self):
        text = redact("key sk-ant-abc123def456ghi789 and password=hunter22 and AKIAABCDEFGHIJKLMNOP")
        self.assertNotIn("sk-ant-abc123", text)
        self.assertNotIn("hunter22", text)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", text)


class StoreTests(unittest.TestCase):
    def test_update_supersedes_and_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "memory")
            store.init()
            add = store.apply([{"op": "ADD", "subject": "Taskly deploy target", "project": "taskly",
                                "statement": "Taskly deploys to AWS EC2.", "confidence": 0.9}],
                              source="demo-session#L1", when="2026-10-01T09:00:00Z")
            store.commit(add, "demo-session#L1", "turn 1")
            upd = store.apply([{"op": "UPDATE", "target_id": "f-0001", "subject": "Taskly deploy target",
                                "project": "taskly", "statement": "Taskly deploys to Fly.io.", "confidence": 0.95}],
                              source="demo-session#L5", when="2026-10-08T14:00:00Z")
            store.commit(upd, "demo-session#L5", "turn 2")

            old, new = store.get("f-0001"), store.get("f-0002")
            self.assertEqual(old.status, "superseded")
            self.assertEqual(old.superseded_by, "f-0002")
            self.assertEqual(new.supersedes, "f-0001")
            self.assertEqual([f.id for f in store.active()], ["f-0002"])
            log = store._git("log", "--format=%s")
            self.assertIn("~1 updated", log)

            page = build(store, Path(tmp) / "view.html").read_text()
            self.assertIn("Taskly deploys to Fly.io.", page)


if __name__ == "__main__":
    unittest.main()
