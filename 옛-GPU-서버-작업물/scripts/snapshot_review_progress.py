from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import database, queue_rows  # noqa: E402


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def main() -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_dir = ROOT / "data" / "snapshots" / f"review-progress-{stamp}"
    snapshot_dir.mkdir(parents=True, exist_ok=False)

    sources = {
        "labeling_queue.jsonl": ROOT / "data" / "labeling" / "queue.jsonl",
        "labeling_reviewed.jsonl": ROOT / "data" / "labeling" / "reviewed.jsonl",
        "relation_candidates_all.jsonl": ROOT / "data" / "labeling" / "relation_candidates_all.jsonl",
        "final_challenges.jsonl": ROOT / "data" / "final" / "challenges.jsonl",
    }
    copied: dict[str, int] = {}
    for destination_name, source in sources.items():
        if source.exists():
            destination = snapshot_dir / destination_name
            shutil.copy2(source, destination)
            copied[destination_name] = destination.stat().st_size

    with database.connection(True) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) AS n FROM captcha_questions "
            "WHERE status='active' AND review_status='approved'"
        )
        active_approved = int(cursor.fetchone()["n"])
        cursor.execute("SELECT COUNT(*) AS n FROM captcha_questions")
        total_db_questions = int(cursor.fetchone()["n"])

    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshot_dir": str(snapshot_dir),
        "queue": {
            "pending": len(queue_rows("pending")),
            "approved": len(queue_rows("approved")),
            "rejected": len(queue_rows("rejected")),
        },
        "database": {
            "active_approved_questions": active_approved,
            "total_questions": total_db_questions,
        },
        "final_manifest_rows": count_jsonl(ROOT / "data" / "final" / "challenges.jsonl"),
        "copied_files_bytes": copied,
    }
    (snapshot_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
