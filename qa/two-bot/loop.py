#!/usr/bin/env python3
"""Offline two-bot queue ledger. No network, deployment, or agent execution."""

import argparse
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

STATES = {"queued", "posted", "acked", "done", "cancelled"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def issue_url(value, repo, comment=False):
    suffix = r"#issuecomment-[1-9][0-9]*" if comment else ""
    return (
        isinstance(value, str)
        and re.fullmatch(
            r"https://github\.com/" + re.escape(repo) + r"/issues/[1-9][0-9]*" + suffix,
            value,
        )
        is not None
    )


def validate(queue):
    require(queue["version"] == 1, "unsupported queue version")
    require(re.fullmatch(r"[\w.-]+/[\w.-]+", queue["repository"]), "invalid repository")
    bots = queue["bots"]
    require(set(bots) == {"bot-1", "bot-2"}, "exactly two bots required")
    prefixes = [bot["prefix"] for bot in bots.values()]
    require(
        all(nonempty(p) for p in prefixes) and len(set(prefixes)) == 2,
        "bot prefixes must be unique",
    )
    require(
        not any(a.startswith(b) for a in prefixes for b in prefixes if a != b),
        "bot prefixes must not overlap",
    )
    for bot in bots.values():
        require(type(bot["paused"]) is bool, "paused must be boolean")
        require(isinstance(bot["protected_fixtures"], list), "protected fixtures must be a list")
    ids = set()
    for batch in queue["batches"]:
        require(nonempty(batch["id"]) and batch["id"] not in ids, "duplicate/empty batch ID")
        ids.add(batch["id"])
        require(batch["bot"] in bots, "unknown bot")
        require(batch["state"] in STATES, "unknown batch state")
        require(sha(batch["commit"]), "batch must pin a full commit")
        pins = batch["release_pins"]
        require(
            isinstance(pins, dict)
            and {"frontend", "backend"} <= pins.keys()
            and all(sha(value) for value in pins.values()),
            "frontend/backend and paired component pins must be full commits",
        )
        cases = batch["cases"]
        require(10 <= len(cases) <= 15, "batches require 10-15 cases")
        require(len({case["id"] for case in cases}) == len(cases), "duplicate case ID")
        require(
            all(
                nonempty(c["id"])
                and nonempty(c["steps"])
                and nonempty(c["expected"])
                and nonempty(c["source"])
                for c in cases
            ),
            "case details/source required",
        )
        require(
            sum(c.get("action") is True for c in cases) * 2 >= len(cases),
            "at least half of cases must act",
        )
        require(sum(c.get("edge") is True for c in cases) >= 2, "two edge cases required")
        require(
            cases[-2].get("kind") == "cleanup" and cases[-1].get("kind") == "inventory",
            "finish with cleanup and inventory",
        )
    for name in bots:
        require(
            sum(b["bot"] == name and b["state"] in {"posted", "acked"} for b in queue["batches"])
            <= 1,
            "only one active batch per bot",
        )


def target_ready(queue, name):
    bot = queue["bots"][name]
    require(not bot["paused"], "queue is paused")
    for field in ("target", "tenant", "identity", "isolation_evidence", "build_evidence"):
        require(nonempty(bot.get(field)), "activation requires " + field)
    require(issue_url(bot.get("queue_issue"), queue["repository"]), "queue issue required")
    other = queue["bots"]["bot-2" if name == "bot-1" else "bot-1"]
    require(bot["tenant"] != other.get("tenant"), "bots must use distinct tenants")
    require(bot["identity"] != other.get("identity"), "bots must use distinct identities")
    # These are owner-reviewed evidence references, not automatic isolation proof.


def result_valid(queue, batch, result):
    require(
        result["batch"] == batch["id"] and result["bot"] == batch["bot"], "result identity mismatch"
    )
    require(
        result["start_commit"] == result["end_commit"] == batch["commit"],
        "build changed or does not match pin; stop and requeue",
    )
    require(
        result["start_pins"] == result["end_pins"] == batch["release_pins"],
        "component build changed or does not match release pins",
    )
    require(result["method"] == "browser", "HTTP evidence cannot replace browser evidence")
    require(issue_url(result["comment_url"], queue["repository"], True), "result comment required")
    rows = result["cases"]
    expected = {c["id"] for c in batch["cases"]}
    require(
        len(rows) == len(expected) and {r["id"] for r in rows} == expected,
        "result must contain exactly one row per case",
    )
    counts = {s: 0 for s in ("PASS", "FAIL", "BLOCKED")}
    for row in rows:
        require(row["status"] in counts, "invalid result status")
        counts[row["status"]] += 1
        require(nonempty(row["observed"]), "observed outcome required")
        require(isinstance(row["record_ids"], list), "record IDs must be a list")
        require(nonempty(row["evidence"]), "sanitized browser evidence reference required")
        if row["status"] == "FAIL":
            require(issue_url(row.get("defect"), queue["repository"]), "FAIL requires defect issue")
        if row["status"] == "BLOCKED":
            require(
                nonempty(row.get("owner")) and nonempty(row.get("next_action")),
                "BLOCKED requires owner and next action",
            )
    require(result["counts"] == counts, "counts do not match result rows")
    require(isinstance(result["remaining_fixtures"], list), "final fixture inventory required")
    require(nonempty(result["reviewer"]), "coordinator evidence reviewer required")


def now():
    return datetime.now(UTC).isoformat()


@contextmanager
def locked(path):
    # OS releases the lock on exit/crash; never steal a live lock based on age.
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def save(path, queue):
    validate(queue)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".queue-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(queue, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(args):
    path = Path(args.queue)
    with locked(path):
        queue = json.loads(path.read_text())
        validate(queue)
        if args.command == "validate":
            print("Queue valid")
            return
        if args.command in {"pause", "resume"}:
            bot = queue["bots"][args.subject]
            require(nonempty(args.note), "pause/resume needs a reason or evidence reference")
            bot["paused"] = args.command == "pause"
            if args.command == "resume":
                target_ready(queue, args.subject)
            bot["events"].append({"event": args.command, "at": now(), "note": args.note})
            save(path, queue)
            return
        batch = next(b for b in queue["batches"] if b["id"] == args.subject)
        if args.command == "cancel":
            require(batch["state"] in {"queued", "posted", "acked"}, "batch already terminal")
            require(nonempty(args.note), "cancellation requires reason and fixture disposition")
            batch.update(state="cancelled", cancelled_at=now(), cancellation=args.note)
            save(path, queue)
            return
        bot = queue["bots"][batch["bot"]]
        if args.command == "handoff":
            target_ready(queue, batch["bot"])
            require(batch["state"] == "queued", "batch already handed off")
            require(
                not any(
                    b["bot"] == batch["bot"] and b["state"] in {"posted", "acked"}
                    for b in queue["batches"]
                ),
                "bot already has active work",
            )
            print(f"[Coordinator] handoff | {batch['id']} | {batch['bot']}")
            print(f"Commit: {batch['commit']}\nTarget: {bot['target']}\nTenant: {bot['tenant']}")
            print(
                f"Prefix: {bot['prefix']} YYYYMMDD; protected fixtures: {bot['protected_fixtures']}"
            )
            print(f"Component release pins: {batch['release_pins']}")
            print("Stop on build drift. Synthetic data only; follow qa/two-bot/BRIEFS.md.")
            for case in batch["cases"]:
                print(f"\n{case['id']}: {case['steps']}\nExpected: {case['expected']}")
            return
        if args.command == "posted":
            require(batch["state"] == "queued", "only queued batches can be posted")
            require(
                issue_url(args.note, queue["repository"], True)
                and args.note.split("#")[0] == bot["queue_issue"],
                "wrong queue comment URL",
            )
            batch.update(state="posted", handoff_url=args.note)
        elif args.command == "ack":
            require(batch["state"] == "posted", "ACK requires posted batch")
            require(
                issue_url(args.note, queue["repository"], True)
                and args.note.split("#")[0] == bot["queue_issue"],
                "wrong ACK URL",
            )
            batch.update(state="acked", ack_at=now(), ack_url=args.note)
        elif args.command == "nudge":
            require(batch["state"] == "acked", "nudge requires ACK")
            elapsed = datetime.now(UTC) - datetime.fromisoformat(batch["ack_at"])
            if elapsed.total_seconds() < 5400 or batch.get("nudged_at"):
                return
            require(
                issue_url(args.note, queue["repository"], True)
                and args.note.split("#")[0] == bot["queue_issue"],
                "posted nudge URL required",
            )
            batch.update(nudged_at=now(), nudge_url=args.note)
        elif args.command == "record":
            require(batch["state"] == "acked", "result requires ACK")
            result = json.loads(Path(args.note).read_text())
            result_valid(queue, batch, result)
            require(result["comment_url"].split("#")[0] == bot["queue_issue"], "wrong result queue")
            batch.update(state="done", result=result)
        batch.setdefault("events", []).append({"event": args.command, "at": now()})
        save(path, queue)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "validate",
            "handoff",
            "posted",
            "ack",
            "record",
            "pause",
            "resume",
            "nudge",
            "cancel",
        ],
    )
    parser.add_argument("subject", nargs="?", help="batch ID, or bot ID for pause/resume")
    parser.add_argument("--note", default="", help="comment URL, result JSON path, or pause reason")
    parser.add_argument("--queue", default=str(Path(__file__).with_name("queue.json")))
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, KeyError, TypeError, StopIteration, OSError) as error:
        parser.exit(1, f"QA loop rejected operation: {error}\n")


if __name__ == "__main__":
    main()
