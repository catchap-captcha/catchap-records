from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import Database, utcnow


STAGING_SCHEMA = "captcha_gpu_snapshot_20260727_1030"
CANONICAL_SCHEMA = "captcha_ms"

MIGRATION_KEY = "db-gpu-merge-20260727"

def json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, str)):
        return json.loads(value)
    return value


def json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def payload_for(question: dict[str, Any], objects: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "instruction_ko": question["instruction_ko"],
        "difficulty": int(question["difficulty"]),
        "objects": [
            {
                "object_key": str(row["object_key"]),
                "label": str(row["label"]),
                "x": float(row["bbox_x"]),
                "y": float(row["bbox_y"]),
                "width": float(row["bbox_width"]),
                "height": float(row["bbox_height"]),
                "role": str(row["role"]),
            }
            for row in sorted(objects, key=lambda item: str(item["object_key"]))
        ],
    }


def signature(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=json_default
    )


def load_questions(cur, schema: str) -> dict[str, dict[str, Any]]:
    cur.execute(f"SELECT * FROM `{schema}`.captcha_questions ORDER BY id")
    questions = {row["id"]: row for row in cur.fetchall()}
    cur.execute(
        f"""SELECT question_id,object_key,label,bbox_x,bbox_y,bbox_width,bbox_height,role
        FROM `{schema}`.captcha_objects ORDER BY question_id,id"""
    )
    objects: dict[str, list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        objects.setdefault(row["question_id"], []).append(row)
    for question_id, question in questions.items():
        question["merge_payload"] = payload_for(question, objects.get(question_id, []))
    return questions


def task_for_question(cur, question: dict[str, Any]) -> dict[str, Any] | None:
    source_question_id = question.get("source_question_id") or question["id"].removeprefix("tq_")
    cur.execute(
        """SELECT * FROM label_tasks
        WHERE existing_question_id=%s OR question_id=%s
        ORDER BY existing_question_id=%s DESC,id DESC LIMIT 1 FOR UPDATE""",
        (question["id"], str(source_question_id), question["id"]),
    )
    return cur.fetchone()


def conflict_context(
    question_id: str,
    canonical: dict[str, Any],
    gpu: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "db_server_gpu",
        "migration_key": MIGRATION_KEY,
        "question_id": question_id,
        "image_path": canonical["image_path"],
        "variants": [
            {
                "key": "db_server",
                "label": f"DB \uc11c\ubc84 (ms \uae30\uc874 \uc791\uc5c5) [{canonical['status']}/{canonical['review_status']}]",
                "reviewer": canonical.get("reviewer"),
                "status": canonical.get("status"),
                "review_status": canonical.get("review_status"),
                "reviewed_at": canonical.get("reviewed_at"),
                "payload": canonical["merge_payload"],
            },
            {
                "key": "gpu",
                "label": f"GPU \uc11c\ubc84 (\ud300 \uc791\uc5c5) [{gpu['status']}/{gpu['review_status']}]",
                "reviewer": gpu.get("reviewer"),
                "status": gpu.get("status"),
                "review_status": gpu.get("review_status"),
                "reviewed_at": gpu.get("reviewed_at"),
                "payload": gpu["merge_payload"],
            },
        ],
    }


def create_conflict_tasks(
    database: Database, dry_run: bool = False
) -> tuple[int, int, list[str]]:
    updated = 0
    inserted = 0
    conflict_ids: list[str] = []
    with database.connection() as conn, conn.cursor() as cur:
        canonical = load_questions(cur, CANONICAL_SCHEMA)
        gpu = load_questions(cur, STAGING_SCHEMA)
        for question_id in sorted(canonical.keys() & gpu.keys()):
            db_question = canonical[question_id]
            gpu_question = gpu[question_id]
            content_diff = signature(db_question["merge_payload"]) != signature(
                gpu_question["merge_payload"]
            )
            state_diff = (db_question["status"], db_question["review_status"]) != (
                gpu_question["status"], gpu_question["review_status"]
            )
            if not content_diff and not state_diff:
                continue

            conflict_ids.append(question_id)
            task = task_for_question(cur, db_question)
            comparison = conflict_context(question_id, db_question, gpu_question)
            canonical_payload = db_question["merge_payload"]
            reasons = []
            if content_diff: reasons.append("DB \uc11c\ubc84\uc640 GPU \uc11c\ubc84\uc758 \ub77c\ubca8 \ub0b4\uc6a9\uc774 \ub2e4\ub985\ub2c8\ub2e4.")
            if state_diff: reasons.append("DB \uc11c\ubc84\uc640 GPU \uc11c\ubc84\uc758 \uc2b9\uc778 \uc0c1\ud0dc\uac00 \ub2e4\ub985\ub2c8\ub2e4.")
            reason = json.dumps(reasons, ensure_ascii=False)
            now = utcnow()

            cur.execute(
                "UPDATE captcha_questions SET status='inactive',review_status='needs_reconciliation' WHERE id=%s",
                (question_id,),
            )
            if task:
                source = json_value(task["source_payload"])
                if source.get("merge_conflict", {}).get("migration_key") == MIGRATION_KEY:
                    continue
                source["merge_conflict"] = comparison
                next_version = int(task["version"]) + 1
                cur.execute(
                    """INSERT INTO label_revisions
                    (task_id,version,actor_id,action,payload,note,imported,source_file,created_at)
                    VALUES(%s,%s,NULL,'merge_conflict_created',%s,%s,TRUE,%s,%s)""",
                    (
                        task["id"],
                        next_version,
                        json.dumps(canonical_payload, ensure_ascii=False),
                        "DB \uc11c\ubc84\uc640 GPU \uc11c\ubc84\uc758 \uc0c1\uc774\ud55c \ubc84\uc804\uc744 \uc7ac\uac80\uc218 \ud050\ub85c \uc774\ub3d9",
                        "db-merge:captcha_ms",
                        now,
                    ),
                )
                cur.execute(
                    """UPDATE label_tasks SET source_payload=%s,current_payload=%s,
                    status='needs_reconciliation',assigned_to=NULL,claimed_at=NULL,
                    lease_expires_at=NULL,claimed_from_status=NULL,version=%s,current_batch=TRUE,
                    expected_target_count=%s,reconciliation_reason=%s,updated_at=%s
                    WHERE id=%s""",
                    (
                        json.dumps(source, ensure_ascii=False, default=json_default),
                        json.dumps(canonical_payload, ensure_ascii=False),
                        next_version,
                        sum(row["role"] == "target" for row in canonical_payload["objects"]),
                        reason,
                        now,
                        task["id"],
                    ),
                )
                updated += 1
            else:
                source_question_id = (
                    db_question.get("source_question_id") or question_id.removeprefix("tq_")
                )
                source = {
                    "image_path": db_question["image_path"],
                    "question_en": db_question.get("instruction_en"),
                    "objects": [],
                    "merge_conflict": comparison,
                }
                source_sha = hashlib.sha256(
                    signature({"question_id": question_id, "comparison": comparison}).encode()
                ).hexdigest()
                cur.execute(
                    """INSERT INTO label_tasks
                    (queue_id,question_id,existing_question_id,source_payload,current_payload,
                    status,version,current_batch,expected_target_count,reconciliation_reason,
                    source_file,source_line,source_sha256,created_at,updated_at)
                    VALUES(%s,%s,%s,%s,%s,'needs_reconciliation',0,TRUE,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        f"merge_conflict_{question_id}",
                        str(source_question_id),
                        question_id,
                        json.dumps(source, ensure_ascii=False, default=json_default),
                        json.dumps(canonical_payload, ensure_ascii=False),
                        sum(row["role"] == "target" for row in canonical_payload["objects"]),
                        reason,
                        "db-merge:captcha_ms",
                        len(conflict_ids),
                        source_sha,
                        now,
                        now,
                    ),
                )
                inserted += 1
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return updated, inserted, conflict_ids


def import_legacy_reviews(database: Database, path: Path) -> tuple[int, int]:
    imported = 0
    skipped = 0
    with database.connection() as conn, conn.cursor() as cur, path.open(
        "r", encoding="utf-8"
    ) as source:
        cur.execute("SELECT id FROM label_admin_users WHERE username='ms'")
        ms_user = cur.fetchone()
        ms_user_id = ms_user["id"] if ms_user else None

        for line_number, raw_line in enumerate(source, 1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            payload = json.loads(raw_line)
            source_sha = hashlib.sha256(raw_line.encode()).hexdigest()
            queue_id = payload.get("queue_id") or payload.get("id")
            question_id = payload.get("question_id")
            review_status = (
                payload.get("review_status") or payload.get("status") or "unknown"
            )
            reviewer = payload.get("reviewer")
            reviewed_at = payload.get("reviewed_at") or payload.get("timestamp")
            if reviewed_at:
                reviewed_at = datetime.fromisoformat(
                    str(reviewed_at).replace("Z", "+00:00")
                ).replace(tzinfo=None)

            cur.execute(
                """SELECT id FROM label_tasks
                WHERE queue_id=%s OR question_id=%s OR existing_question_id=%s
                ORDER BY queue_id=%s DESC,id DESC LIMIT 1""",
                (
                    queue_id,
                    str(question_id) if question_id is not None else "",
                    str(question_id) if question_id is not None else "",
                    queue_id,
                ),
            )
            task = cur.fetchone()
            cur.execute(
                """INSERT IGNORE INTO label_legacy_reviews
                (source_server,source_file,source_line,source_sha256,queue_id,question_id,
                review_status,reviewer,attributed_to,mapped_task_id,payload,reviewed_at,imported_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    "210.109.52.114",
                    str(path),
                    line_number,
                    source_sha,
                    queue_id,
                    str(question_id) if question_id is not None else None,
                    str(review_status),
                    reviewer,
                    ms_user_id,
                    task["id"] if task else None,
                    json.dumps(payload, ensure_ascii=False),
                    reviewed_at,
                    utcnow(),
                ),
            )
            if cur.rowcount:
                imported += 1
            else:
                skipped += 1
        conn.commit()
    return imported, skipped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-reviewed", type=Path)
    parser.add_argument("--skip-conflicts", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    database = Database(settings)
    database.initialize()

    if not args.skip_conflicts:
        updated, inserted, conflict_ids = create_conflict_tasks(database, args.dry_run)
        print(
            json.dumps(
                {
                    "conflicts": len(conflict_ids),
                    "updated_tasks": updated,
                    "inserted_tasks": inserted,
                    "first_ids": conflict_ids[:5],
                },
                ensure_ascii=False,
            )
        )
    if args.legacy_reviewed:
        imported, skipped = import_legacy_reviews(database, args.legacy_reviewed)
        print(json.dumps({"legacy_imported": imported, "legacy_skipped": skipped}))


if __name__ == "__main__":
    main()
