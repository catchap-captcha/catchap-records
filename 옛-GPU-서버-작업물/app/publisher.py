from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from datetime import timedelta
from typing import Any

from PIL import Image

from .config import Settings
from .db import Database, utcnow


def json_value(value: Any) -> Any:
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


def safe_source(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ValueError("Labeling source asset was not found")
    return candidate


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_save_png(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".png", dir=destination.parent)
    os.close(fd)
    try:
        image.save(temporary, "PNG", optimize=True)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def normalized_box(row: dict[str, Any]) -> tuple[float, float, float, float]:
    return tuple(float(row[name]) for name in ("x", "y", "width", "height"))


def pixel_box(row: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    x, y, box_width, box_height = normalized_box(row)
    left = max(0, min(width - 1, round(x * width)))
    top = max(0, min(height - 1, round(y * height)))
    right = max(left + 1, min(width, round((x + box_width) * width)))
    bottom = max(top + 1, min(height, round((y + box_height) * height)))
    return left, top, right, bottom


def same_box(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return all(abs(a - b) < 1e-6 for a, b in zip(normalized_box(first), normalized_box(second)))


def prepare_question_assets(
    database: Database, settings: Settings, task: dict[str, Any], payload: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    now = utcnow()
    source_payload = json_value(task["source_payload"])
    existing_id = task.get("existing_question_id")
    existing = database.get_question(existing_id) if existing_id else None
    question_id = existing_id or "tq_" + str(task["question_id"])

    if existing:
        image_rel = existing["image_path"]
        final_image = safe_source(settings.final_dir, image_rel)
        width, height = int(existing["image_width"]), int(existing["image_height"])
        created_at = existing["created_at"]
        old_objects = {
            str(row["object_key"]): {
                "x": row["bbox_x"], "y": row["bbox_y"], "width": row["bbox_width"],
                "height": row["bbox_height"], "piece_path": row.get("piece_path"),
            }
            for row in existing["objects"]
        }
    else:
        image_source = safe_source(settings.labeling_dir, source_payload["image_path"])
        suffix = image_source.suffix.lower() or ".jpg"
        image_rel = "images/" + question_id + suffix
        final_image = settings.final_dir / image_rel
        atomic_copy(image_source, final_image)
        with Image.open(final_image) as image:
            width, height = image.size
        created_at = now
        old_objects = {}

    source_objects = {str(row.get("object_key")): row for row in source_payload.get("objects", [])}
    object_rows: list[dict[str, Any]] = []
    for raw in payload.get("objects", []):
        row = {
            "object_key": str(raw["object_key"]), "label": str(raw.get("label") or "object")[:128],
            "x": float(raw["x"]), "y": float(raw["y"]), "width": float(raw["width"]),
            "height": float(raw["height"]), "role": str(raw["role"]), "piece_path": None,
        }
        if row["role"] in {"target", "decoy"}:
            old = old_objects.get(row["object_key"])
            if old and old.get("piece_path") and same_box(row, old):
                row["piece_path"] = old["piece_path"]
            else:
                piece_rel = "pieces/" + question_id + "-" + row["object_key"] + ".png"
                piece_path = settings.final_dir / piece_rel
                prepared = source_objects.get(row["object_key"])
                prepared_rel = prepared.get("prepared_piece_path") if prepared else None
                prepared_path = safe_source(settings.labeling_dir, prepared_rel) if prepared_rel else None
                if prepared_path and prepared and same_box(row, prepared):
                    atomic_copy(prepared_path, piece_path)
                elif prepared_path and prepared:
                    new_box = pixel_box(row, width, height)
                    old_box = pixel_box(prepared, width, height)
                    new_size = (new_box[2] - new_box[0], new_box[3] - new_box[1])
                    old_size = (old_box[2] - old_box[0], old_box[3] - old_box[1])
                    with Image.open(prepared_path) as source_piece:
                        masked = source_piece.convert("RGBA")
                        if masked.size != old_size:
                            masked = masked.resize(old_size, Image.Resampling.LANCZOS)
                        adjusted = Image.new("RGBA", new_size, (0, 0, 0, 0))
                        adjusted.alpha_composite(masked, (old_box[0] - new_box[0], old_box[1] - new_box[1]))
                        atomic_save_png(adjusted, piece_path)
                else:
                    with Image.open(final_image) as image:
                        atomic_save_png(image.crop(pixel_box(row, width, height)).convert("RGBA"), piece_path)
                row["piece_path"] = piece_rel
        object_rows.append(row)

    question = {
        "id": question_id, "type": existing["type"] if existing else "object_drag",
        "instruction_ko": str(payload["instruction_ko"])[:500],
        "instruction_en": existing.get("instruction_en") if existing else source_payload.get("question_en"),
        "source": existing["source"] if existing else "tallyqa_visual_genome",
        "source_question_id": existing.get("source_question_id") if existing else str(task["question_id"]),
        "image_path": image_rel, "image_width": width, "image_height": height,
        "difficulty": int(payload.get("difficulty", 2)), "status": "active",
        "review_status": "approved", "reviewer": payload["reviewer"],
        "reviewed_at": now, "created_at": created_at,
    }
    return question, object_rows


def export_manifest(database: Database, settings: Settings) -> int:
    with database.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK('label_manifest_export',30) acquired")
        if cur.fetchone()["acquired"] != 1:
            raise RuntimeError("Manifest export lock unavailable")
        try:
            cur.execute("""SELECT * FROM captcha_questions
              WHERE status='active' AND review_status='approved' ORDER BY id""")
            questions = cur.fetchall()
            rows = []
            for question in questions:
                cur.execute("""SELECT object_key,label,bbox_x,bbox_y,bbox_width,bbox_height,role,piece_path
                  FROM captcha_objects WHERE question_id=%s AND role<>'invalid' ORDER BY id""", (question["id"],))
                objects = cur.fetchall()
                rows.append({
                    "challenge_id": question["id"], "source": question["source"],
                    "source_question_id": question["source_question_id"], "image_path": question["image_path"],
                    "instruction": question["instruction_ko"], "difficulty": question["difficulty"],
                    "review_status": "approved",
                    "objects": [{
                        "object_key": row["object_key"], "label": row["label"],
                        "bbox": [row["bbox_x"], row["bbox_y"], row["bbox_width"], row["bbox_height"]],
                        "role": row["role"], "piece_path": row["piece_path"],
                    } for row in objects],
                })
            destination = settings.final_dir / "challenges.jsonl"
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix="challenges.", suffix=".jsonl", dir=destination.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    for row in rows:
                        stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return len(rows)
        finally:
            cur.execute("SELECT RELEASE_LOCK('label_manifest_export')")


def publish_job(database: Database, settings: Settings, job_id: int) -> dict[str, Any]:
    with database.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM label_publish_jobs WHERE id=%s FOR UPDATE", (job_id,))
        job = cur.fetchone()
        if not job:
            raise ValueError("Publish job not found")
        if job["status"] == "completed":
            return {"job_id": job_id, "status": "completed"}
        now = utcnow()
        if job["status"] == "running" and job.get("started_at") and job["started_at"] > now - timedelta(minutes=15):
            raise RuntimeError("Publish job is already running")
        cur.execute("""UPDATE label_publish_jobs SET status='running',attempt_count=attempt_count+1,
          started_at=%s,error_text=NULL WHERE id=%s""", (now, job_id))
        cur.execute("""SELECT t.*,r.payload,u.username reviewer FROM label_tasks t
          JOIN label_revisions r ON r.id=%s
          LEFT JOIN label_admin_users u ON u.id=%s
          WHERE t.id=%s""", (job["revision_id"], job.get("requested_by"), job["task_id"]))
        task = cur.fetchone()
        conn.commit()
    if not task:
        raise ValueError("Publish task or revision not found")
    try:
        payload = json_value(task["payload"])
        payload["reviewer"] = task.get("reviewer") or payload.get("reviewer") or "admin"
        question, objects = prepare_question_assets(database, settings, task, payload)
        database.upsert_question(question, objects)
        manifest_count = export_manifest(database, settings)
        now = utcnow()
        with database.connection() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE label_publish_jobs SET status='completed',completed_at=%s,error_text=NULL
              WHERE id=%s""", (now, job_id))
            cur.execute("""UPDATE label_tasks SET status='published',assigned_to=NULL,claimed_at=NULL,
              lease_expires_at=NULL,claimed_from_status=NULL,published_at=%s,updated_at=%s,
              existing_question_id=%s WHERE id=%s""", (now, now, question["id"], task["id"]))
            cur.execute("""INSERT INTO label_events(task_id,actor_id,event_type,details,created_at)
              VALUES(%s,%s,'publish_completed',JSON_OBJECT('job_id',%s,'manifest_count',%s),%s)""",
              (task["id"], job.get("requested_by"), job_id, manifest_count, now))
            conn.commit()
        return {"job_id": job_id, "status": "completed", "question_id": question["id"], "manifest_count": manifest_count}
    except Exception as error:
        now = utcnow()
        with database.connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE label_publish_jobs SET status='failed',error_text=%s WHERE id=%s", (str(error)[:8000], job_id))
            cur.execute("UPDATE label_tasks SET status='publish_failed',updated_at=%s WHERE id=%s", (now, task["id"]))
            cur.execute("""INSERT INTO label_events(task_id,actor_id,event_type,details,created_at)
              VALUES(%s,%s,'publish_failed',JSON_OBJECT('job_id',%s,'error',%s),%s)""",
              (task["id"], job.get("requested_by"), job_id, str(error)[:1000], now))
            conn.commit()
        raise


def deactivate_question(database: Database, settings: Settings, question_id: str | None) -> int:
    if question_id:
        with database.connection() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE captcha_questions SET status='inactive',review_status='rejected'
              WHERE id=%s""", (question_id,))
            conn.commit()
    return export_manifest(database, settings)