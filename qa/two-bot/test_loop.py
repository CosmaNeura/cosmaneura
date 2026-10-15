"""Regression tests for the offline ledger's acceptance and state boundaries."""

import importlib.util
import json
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("qa_loop", Path(__file__).with_name("loop.py"))
loop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loop)


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "queue.json"
        self.queue = json.loads(Path(__file__).with_name("queue.json").read_text())
        self.repo = self.queue["repository"]
        for i, bot in enumerate(self.queue["bots"].values(), 1):
            bot.update(
                paused=False,
                target=f"https://bot-{i}.invalid",
                tenant=f"tenant-{i}",
                identity=f"identity-{i}",
                isolation_evidence="reviewed synthetic isolation",
                build_evidence="observed UI/backend pins",
                queue_issue=self.url(i),
            )
        self.batch = json.loads(Path(__file__).with_name("batch.example.json").read_text())
        self.batch["commit"] = "a" * 40
        self.batch["release_pins"] = {"frontend": "a" * 40, "backend": "b" * 40}
        self.queue["batches"] = [self.batch]
        self.write()

    def url(self, issue=1, comment=None):
        url = f"https://github.com/{self.repo}/issues/{issue}"
        return url + (f"#issuecomment-{comment}" if comment else "")

    def write(self):
        self.path.write_text(json.dumps(self.queue))

    def read(self):
        return json.loads(self.path.read_text())

    def call(self, command, note="", subject="PILOT-B1"):
        loop.run(SimpleNamespace(command=command, subject=subject, note=note, queue=str(self.path)))

    def activate(self):
        self.call("posted", self.url(comment=1))
        self.call("ack", self.url(comment=2))

    def result(self):
        result = json.loads(Path(__file__).with_name("result.example.json").read_text())
        result.update(start_commit="a" * 40, end_commit="a" * 40, comment_url=self.url(comment=3))
        result.update(
            start_pins=self.batch["release_pins"].copy(), end_pins=self.batch["release_pins"].copy()
        )
        return result

    def test_valid_queue(self):
        loop.validate(self.queue)

    def test_rejects_invalid_batch_structure(self):
        for mutate in (
            lambda b: b.update(commit="main"),
            lambda b: b["cases"].pop(),
            lambda b: b["cases"][0].update(id="C02"),
            lambda b: [c.update(action=False) for c in b["cases"]],
            lambda b: [c.update(edge=False) for c in b["cases"]],
            lambda b: b["cases"][-1].update(kind="case"),
        ):
            queue = deepcopy(self.queue)
            mutate(queue["batches"][0])
            with self.assertRaises(ValueError):
                loop.validate(queue)

    def test_pause_and_resume_fail_closed(self):
        self.call("pause", "owner: isolation not ready", "bot-1")
        with self.assertRaises(ValueError):
            self.call("handoff")
        self.queue = self.read()
        self.queue["bots"]["bot-1"]["isolation_evidence"] = ""
        self.write()
        with self.assertRaises(ValueError):
            self.call("resume", "ready", "bot-1")
        self.assertTrue(self.read()["bots"]["bot-1"]["paused"])

    def test_tenant_and_identity_collision(self):
        for field in ("tenant", "identity"):
            queue = deepcopy(self.queue)
            queue["bots"]["bot-1"][field] = queue["bots"]["bot-2"][field]
            with self.assertRaises(ValueError):
                loop.target_ready(queue, "bot-1")

    def test_wrong_order_and_wrong_queue_do_not_change_file(self):
        before = self.path.read_bytes()
        for command, note in (("ack", self.url(comment=1)), ("posted", self.url(2, 1))):
            with self.assertRaises(ValueError):
                self.call(command, note)
            self.assertEqual(before, self.path.read_bytes())

    def test_only_one_active_batch(self):
        self.activate()
        self.queue = self.read()
        other = deepcopy(self.batch)
        other.update(id="OTHER", state="posted")
        self.queue["batches"].append(other)
        with self.assertRaises(ValueError):
            loop.validate(self.queue)

    def test_complete_and_reject_duplicate_result(self):
        self.activate()
        path = self.path.with_name("result.json")
        path.write_text(json.dumps(self.result()))
        self.call("record", str(path))
        self.assertEqual("done", self.read()["batches"][0]["state"])
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.call("record", str(path))
        self.assertEqual(before, self.path.read_bytes())

    def test_rejects_drift_http_missing_rows_counts_and_evidence(self):
        for mutate in (
            lambda r: r.update(end_commit="b" * 40),
            lambda r: r.update(method="http"),
            lambda r: r["end_pins"].update(backend="c" * 40),
            lambda r: r["cases"].pop(),
            lambda r: r["cases"][0].update(id="C02"),
            lambda r: r["counts"].update(PASS=9),
            lambda r: r["cases"][0].update(evidence=""),
            lambda r: r["cases"][0].update(status="FAIL"),
            lambda r: r["cases"][0].update(status="BLOCKED"),
        ):
            result = self.result()
            mutate(result)
            with self.assertRaises(ValueError):
                loop.result_valid(self.queue, self.batch, result)

    def test_accepts_evidenced_fail_and_named_blocker(self):
        result = self.result()
        result["cases"][0].update(status="FAIL", defect=self.url(4))
        result["cases"][1].update(
            status="BLOCKED", owner="release owner", next_action="reconcile deployed pin"
        )
        result["counts"] = {"PASS": 8, "FAIL": 1, "BLOCKED": 1}
        loop.result_valid(self.queue, self.batch, result)

    def test_nudge_once_after_ninety_minutes(self):
        self.activate()
        before = self.path.read_bytes()
        self.call("nudge", self.url(comment=4))
        self.assertEqual(before, self.path.read_bytes())
        self.queue = self.read()
        self.queue["batches"][0]["ack_at"] = (datetime.now(UTC) - timedelta(minutes=91)).isoformat()
        self.write()
        self.call("nudge", self.url(comment=4))
        before = self.path.read_bytes()
        self.call("nudge", self.url(comment=5))
        self.assertEqual(before, self.path.read_bytes())

    def test_drift_recovery_keeps_history_while_paused(self):
        self.activate()
        self.call("pause", "owner: build drift", "bot-1")
        self.call("cancel", "Build drift; retained owned synthetic fixtures in inventory")
        batch = self.read()["batches"][0]
        self.assertEqual("cancelled", batch["state"])
        self.assertEqual(self.url(comment=1), batch["handoff_url"])
        with self.assertRaises(ValueError):
            self.call("cancel", "again")

    def test_reconcile_existing_comments_while_paused(self):
        self.call("posted", self.url(comment=1))
        self.call("pause", "owner: pause new postings", "bot-1")
        self.call("ack", self.url(comment=2))
        path = self.path.with_name("result.json")
        path.write_text(json.dumps(self.result()))
        self.call("record", str(path))
        self.assertEqual("done", self.read()["batches"][0]["state"])
        self.assertTrue(self.read()["bots"]["bot-1"]["paused"])

    def test_concurrent_writer_is_rejected(self):
        with loop.locked(self.path):
            with self.assertRaises(BlockingIOError):
                self.call("posted", self.url(comment=1))
        self.call("posted", self.url(comment=1))


if __name__ == "__main__":
    unittest.main()
