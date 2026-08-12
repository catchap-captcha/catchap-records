from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LABELING = ROOT / "data" / "labeling"
STAMP = "20260722T025754Z"


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def main() -> None:
    queue = LABELING / "queue.jsonl"
    all_path = LABELING / "relation_candidates_all.jsonl"
    reviewed_path = LABELING / "reviewed.jsonl"
    backup_queue = LABELING / f"queue.before-retranslation-{STAMP}.jsonl"
    backup_all = LABELING / f"relation_candidates_all.before-retranslation-{STAMP}.jsonl"
    backup_map = {str(row["queue_id"]): row["instruction_ko"] for row in read(backup_queue)}
    latest: dict[str, dict] = {}
    for row in read(reviewed_path):
        latest[str(row["queue_id"])] = row
    excluded = {qid for qid, row in latest.items() if row.get("review_status") in {"approved", "rejected"}}
    pending_ids = set(backup_map) - excluded
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(queue, queue.with_name(f"queue.before-translation-restore-{now_stamp}.jsonl"))
    shutil.copy2(all_path, all_path.with_name(f"relation_candidates_all.before-translation-restore-{now_stamp}.jsonl"))
    queue_rows = read(queue)
    all_rows = read(all_path)
    for rows in (queue_rows, all_rows):
        for row in rows:
            qid = str(row.get("queue_id"))
            if qid in pending_ids:
                row["instruction_ko"] = backup_map[qid]
                row["translation_status"] = "restored_pretranslation"
    write(queue, queue_rows)
    write(all_path, all_rows)
    appended = 0
    with reviewed_path.open("a", encoding="utf-8") as fp:
        for qid in sorted(pending_ids):
            review = latest.get(qid)
            if not review or review.get("review_status") in {"approved", "rejected"}:
                continue
            restored = {**review, "instruction_ko": backup_map[qid], "translation_status": "restored_pretranslation", "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
            fp.write(json.dumps(restored, ensure_ascii=False) + "\n")
            appended += 1
    print(json.dumps({"restored_pending": len(pending_ids), "preserved_approved_or_rejected": len(excluded), "review_snapshots_appended": appended, "source_backup": STAMP}, ensure_ascii=False))


if __name__ == "__main__":
    main()
