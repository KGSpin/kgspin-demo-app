"""Deterministic training/held-out split for Arm-B prompt-design blinders.

Contract: the same ``question_id`` ALWAYS lands in the same split. The
held-out split (20%) must never leak into Arm B prompt engineering.

Usage::

    python -m benchmarks.harness.split \\
        --input benchmarks/questions/financebench-subset.jsonl \\
        --train-out benchmarks/questions/financebench-subset.jsonl \\
        --heldout-out benchmarks/questions/financebench-subset.heldout.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HELDOUT_PCT = 20


def bucket(question_id: str) -> int:
    return int(hashlib.sha1(question_id.encode("utf-8")).hexdigest(), 16) % 100


def is_heldout(question_id: str) -> bool:
    return bucket(question_id) < HELDOUT_PCT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--train-out", type=Path, required=True)
    parser.add_argument("--heldout-out", type=Path, required=True)
    args = parser.parse_args(argv)

    train: list[dict] = []
    heldout: list[dict] = []
    with args.input.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            qid = row.get("question_id") or row.get("financebench_id")
            if not qid:
                continue
            if is_heldout(qid):
                heldout.append(row)
            else:
                train.append(row)

    args.train_out.parent.mkdir(parents=True, exist_ok=True)
    args.heldout_out.parent.mkdir(parents=True, exist_ok=True)
    with args.train_out.open("w") as f:
        for r in train:
            f.write(json.dumps(r) + "\n")
    with args.heldout_out.open("w") as f:
        for r in heldout:
            f.write(json.dumps(r) + "\n")
    print(f"train={len(train)} heldout={len(heldout)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
