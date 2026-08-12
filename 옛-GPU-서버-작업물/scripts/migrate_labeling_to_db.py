from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.db import Database, utcnow
from app.publisher import export_manifest


def read_jsonl(path: Path) -> list[tuple[int, str, dict[str, Any]]]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as stream:
        for number, raw in enumerate(stream, 1):
            raw = raw.rstrip("\r\n")
            if raw.strip():
                rows.append((number, raw, json.loads(raw)))
    return rows


def canonical_sha(row: dict[str, Any]) -> str:
    encoded = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_time(value: Any) -> datetime:
    if not value:
        return utcnow()
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


def object_signature(rows: list[dict[str, Any]], manifest: bool = False) -> list[tuple[Any, ...]]:
    result = []
    for row in rows:
        bbox = row.get("bbox") if manifest else [row.get("bbox_x"), row.get("bbox_y"), row.get("bbox_width"), row.get("bbox_height")]
        result.append((
            str(row.get("object_key")), str(row.get("label")), str(row.get("role")),
            *(round(float(value), 7) for value in bbox), row.get("piece_path"),
        ))
    return sorted(result)


def load_production(database: Database) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    questions: dict[str, dict[str, Any]] = {}
    source_map: dict[str, str] = {}
    with database.connection(True) as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM captcha_questions")
        for row in cur.fetchall():
            cur.execute("SELECT * FROM captcha_objects WHERE question_id=%s ORDER BY id", (row["id"],))
            row["objects"] = cur.fetchall()
            questions[row["id"]] = row
            if row.get("source_question_id"):
                source_map[str(row["source_question_id"])] = row["id"]
    return questions, source_map


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    return {row["challenge_id"]: row for _, _, row in read_jsonl(path)}


def find_existing_id(row: dict[str, Any], questions: dict[str, Any], source_map: dict[str, str]) -> str | None:
    explicit = row.get("existing_question_id")
    if explicit and explicit in questions:
        return str(explicit)
    generated = "tq_" + str(row.get("question_id"))
    if generated in questions:
        return generated
    return source_map.get(str(row.get("question_id")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Import append-only JSON labeling history into MySQL")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quarantine-conflicts", action="store_true")
    args = parser.parse_args()

    database = Database(settings)
    database.initialize()
    relation_path = settings.labeling_dir / "relation_candidates_all.jsonl"
    queue_path = settings.labeling_dir / "queue.jsonl"
    review_path = settings.labeling_dir / "reviewed.jsonl"
    relation_rows = read_jsonl(relation_path)
    queue_rows = read_jsonl(queue_path)
    review_rows = read_jsonl(review_path)
    manifest = load_manifest(settings.final_dir / "challenges.jsonl")
    questions, source_map = load_production(database)

    relation = {str(row["queue_id"]): (line, raw, row) for line, raw, row in relation_rows}
    queue = {str(row["queue_id"]): (line, raw, row) for line, raw, row in queue_rows}
    reviews: dict[str, list[tuple[int, str, dict[str, Any]]]] = defaultdict(list)
    for line, raw, row in review_rows:
        reviews[str(row["queue_id"])].append((line, raw, row))
    queue_ids = set(relation) | set(queue) | set(reviews)
    legacy_only = sorted(set(reviews) - set(relation) - set(queue))

    manifest_conflicts: set[str] = set()
    bbox_conflicts: set[str] = set()
    missing_asset_conflicts: set[str] = set()
    for question_id, question in questions.items():
        manifest_row = manifest.get(question_id)
        if manifest_row and object_signature(question["objects"]) != object_signature(manifest_row.get("objects", []), manifest=True):
            manifest_conflicts.add(question_id)
        if question["status"] != "active" or question["review_status"] != "approved":
            continue
        for obj in question["objects"]:
            x, y = float(obj["bbox_x"]), float(obj["bbox_y"])
            width, height = float(obj["bbox_width"]), float(obj["bbox_height"])
            if obj["role"] != "invalid" and (x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001):
                bbox_conflicts.add(question_id)
            if obj["role"] in {"target", "decoy"}:
                piece_path = obj.get("piece_path")
                if not piece_path or not (settings.final_dir / piece_path).is_file():
                    missing_asset_conflicts.add(question_id)

    prepared = []
    reconciliation_question_ids: set[str] = set()
    quarantine_question_ids: set[str] = set()
    counts: defaultdict[str, int] = defaultdict(int)
    for queue_id in sorted(queue_ids):
        if queue_id in relation:
            source_line, source_raw, source = relation[queue_id]
            source_file = str(relation_path)
        elif queue_id in queue:
            source_line, source_raw, source = queue[queue_id]
            source_file = str(queue_path)
        else:
            source_line, source_raw, source = reviews[queue_id][0]
            source_file = str(review_path)
        history = reviews.get(queue_id, [])
        current = history[-1][2] if history else source
        existing_id = find_existing_id(current, questions, source_map) or find_existing_id(source, questions, source_map)
        latest_status = str(current.get("review_status", "pending")) if history else "pending"
        status_map = {"approved": "published", "rejected": "rejected", "labeled": "draft", "pending": "pending"}
        task_status = status_map.get(latest_status, "draft")
        reasons = []
        production = questions.get(existing_id) if existing_id else None
        if latest_status == "rejected" and production and production["status"] == "active" and production["review_status"] == "approved":
            reasons.append("latest_json_rejected_but_database_active")
            quarantine_question_ids.add(existing_id)
        if existing_id in manifest_conflicts:
            reasons.append("manifest_and_database_objects_differ")
            quarantine_question_ids.add(existing_id)
        if existing_id in bbox_conflicts:
            reasons.append("bbox_outside_image")
        if existing_id in missing_asset_conflicts:
            reasons.append("missing_piece_asset")
            quarantine_question_ids.add(existing_id)
        reasons = list(dict.fromkeys(reasons))
        if reasons:
            task_status = "needs_reconciliation"
            reconciliation_question_ids.add(existing_id)
        if queue_id in legacy_only and not reasons:
            task_status = "legacy_imported_" + task_status
        counts[task_status] += 1
        prepared.append({
            "queue_id": queue_id, "source": source, "current": current, "history": history,
            "existing_id": existing_id, "status": task_status, "reasons": reasons,
            "current_batch": queue_id in queue, "source_file": source_file,
            "source_line": source_line, "source_sha": canonical_sha(source),
        })

    expected_reviews = len(review_rows)
    expected_tasks = len(queue_ids)
    now = utcnow()
    with database.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) count FROM label_tasks")
        if int(cur.fetchone()["count"]):
            raise RuntimeError("label_tasks is not empty; migration intentionally refuses to overwrite imported work")
        task_ids: dict[str, int] = {}
        for item in prepared:
            cur.execute("""INSERT INTO label_tasks
              (queue_id,question_id,existing_question_id,source_payload,current_payload,status,current_batch,
               expected_target_count,reconciliation_reason,source_file,source_line,source_sha256,created_at,updated_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                item["queue_id"], str(item["source"].get("question_id")), item["existing_id"],
                json.dumps(item["source"], ensure_ascii=False), json.dumps(item["current"], ensure_ascii=False),
                item["status"], item["current_batch"], int(item["source"].get("expected_target_count", 1)),
                json.dumps(item["reasons"], ensure_ascii=False) if item["reasons"] else None,
                item["source_file"], item["source_line"], item["source_sha"], now, now,
            ))
            task_id = int(cur.lastrowid); task_ids[item["queue_id"]] = task_id
            for version, (line, raw, review) in enumerate(item["history"], 1):
                cur.execute("""INSERT INTO label_revisions
                  (task_id,version,actor_id,action,payload,note,imported,source_file,source_line,source_sha256,created_at)
                  VALUES(%s,%s,NULL,%s,%s,%s,1,%s,%s,%s,%s)""", (
                    task_id, version, str(review.get("review_status", "labeled")),
                    json.dumps(review, ensure_ascii=False), "Imported reviewer: " + str(review.get("reviewer", "unknown")),
                    str(review_path), line, hashlib.sha256(raw.encode("utf-8")).hexdigest(), parse_time(review.get("reviewed_at")),
                ))
            if item["history"]:
                cur.execute("UPDATE label_tasks SET version=%s WHERE id=%s", (len(item["history"]), task_id))
        if args.quarantine_conflicts:
            for question_id in sorted(value for value in quarantine_question_ids if value):
                cur.execute("""UPDATE captcha_questions SET status='inactive',review_status='needs_revision'
                  WHERE id=%s""", (question_id,))
        cur.execute("SELECT COUNT(*) count FROM label_tasks")
        inserted_tasks = int(cur.fetchone()["count"])
        cur.execute("SELECT COUNT(*) count FROM label_revisions WHERE imported=1")
        inserted_reviews = int(cur.fetchone()["count"])
        if inserted_tasks != expected_tasks or inserted_reviews != expected_reviews:
            conn.rollback()
            raise RuntimeError(f"Count mismatch tasks={inserted_tasks}/{expected_tasks} reviews={inserted_reviews}/{expected_reviews}")
        if args.dry_run:
            conn.rollback()
        else:
            cur.execute("""INSERT INTO label_events(event_type,details,created_at)
              VALUES('migration_completed',JSON_OBJECT('tasks',%s,'reviews',%s,'conflicts',%s,'legacy_only',%s),%s)""",
              (inserted_tasks, inserted_reviews, len(reconciliation_question_ids), len(legacy_only), now))
            conn.commit()

    manifest_count = None
    if not args.dry_run and args.quarantine_conflicts:
        manifest_count = export_manifest(database, settings)
    result = {
        "dry_run": args.dry_run, "tasks": expected_tasks, "relation_candidates": len(relation_rows),
        "current_queue": len(queue_rows), "review_events": expected_reviews,
        "unique_reviewed": len(reviews), "extra_revisions": expected_reviews - len(reviews),
        "legacy_only_tasks": len(legacy_only), "status_counts": dict(sorted(counts.items())),
        "reconciliation_items": len(reconciliation_question_ids),
        "reconciliation_question_ids": sorted(value for value in reconciliation_question_ids if value),
        "quarantine_items": len(quarantine_question_ids),
        "quarantine_question_ids": sorted(value for value in quarantine_question_ids if value),
        "quarantined": bool(args.quarantine_conflicts and not args.dry_run), "active_manifest_count": manifest_count,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()