from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from .admin_auth import AdminAuth, hash_password, public_user
from .config import Settings
from .db import Database, utcnow
from .publisher import deactivate_question, json_value, publish_job, safe_source


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


class LabelObject(BaseModel):
    object_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=128)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    role: Literal["target", "decoy", "ambiguous", "invalid"]

    @model_validator(mode="after")
    def inside_image(self):
        available_width = 1.0 - self.x
        available_height = 1.0 - self.y
        if available_width <= 0 or available_height <= 0:
            raise ValueError("Bounding box must have visible area inside the image")

        # Imported detector boxes may extend past an image edge. The original
        # coordinates remain preserved in label_tasks.source_payload.
        self.width = min(self.width, available_width)
        self.height = min(self.height, available_height)
        return self


class TaskPayload(BaseModel):
    instruction_ko: str = Field(min_length=1, max_length=500)
    difficulty: int = Field(default=2, ge=1, le=5)
    objects: list[LabelObject] = Field(min_length=1, max_length=100)


class SaveRequest(BaseModel):
    version: int = Field(ge=0)
    payload: TaskPayload
    note: str | None = Field(default=None, max_length=1000)
    autosave: bool = False


class VersionRequest(BaseModel):
    version: int = Field(ge=0)
    payload: TaskPayload | None = None
    note: str | None = Field(default=None, max_length=1000)


class ResetPasswordRequest(BaseModel):
    temporary_password: str | None = Field(default=None, min_length=12, max_length=256)


def task_select() -> str:
    return """SELECT t.*,u.username assigned_username FROM label_tasks t
      LEFT JOIN label_admin_users u ON u.id=t.assigned_to"""


def serialize_task(row: dict[str, Any]) -> dict[str, Any]:
    payload = json_value(row["current_payload"])
    source = json_value(row["source_payload"])
    merge_conflict = source.get("merge_conflict")
    return {
        "id": int(row["id"]), "queue_id": row["queue_id"], "question_id": row["question_id"],
        "existing_question_id": row.get("existing_question_id"), "status": row["status"],
        "version": int(row["version"]), "current_batch": bool(row["current_batch"]),
        "expected_target_count": int(row["expected_target_count"]),
        "assigned_to": row.get("assigned_username"),
        "approved_by": payload.get("reviewer") if row["status"] == "published" else None,
        "lease_expires_at": row["lease_expires_at"].isoformat() + "Z" if row.get("lease_expires_at") else None,
        "reconciliation_reason": json_value(row.get("reconciliation_reason")) if row.get("reconciliation_reason") else None,
        "source_file": row["source_file"], "source_line": int(row["source_line"]),
        "payload": payload,
        "context": {
            "image_path": (
                merge_conflict.get("image_path") if merge_conflict else source.get("image_path")
            ),
            "asset_scope": "final" if merge_conflict else "labeling",
            "conflict_comparison": merge_conflict,
            "question_en": source.get("question_en"),
            "relationship_hints": source.get("relationship_hints", []),
        },
        "updated_at": row["updated_at"].isoformat() + "Z",
        "published_at": row["published_at"].isoformat() + "Z" if row.get("published_at") else None,
    }


def task_view_clause(view: str, user_id: int) -> tuple[str, tuple[Any, ...]]:
    clauses: dict[str, tuple[str, tuple[Any, ...]]] = {
        "pending": ("t.status IN ('pending','draft')", ()),
        "mine": ("t.assigned_to=%s AND t.status='in_progress'", (user_id,)),
        "reconciliation": ("t.status='needs_reconciliation'", ()),
        "published": ("t.status='published'", ()),
        "rejected": ("t.status='rejected'", ()),
        "publish_failed": ("t.status='publish_failed'", ()),
        "all": ("1=1", ()),
    }
    if view not in clauses:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "지원하지 않는 작업 보기입니다.")
    return clauses[view]


def task_order_clause(view: str) -> str:
    if view == "published":
        return "COALESCE(t.published_at,t.updated_at) DESC,t.id DESC"
    return "t.current_batch DESC,t.updated_at,t.id"


def progress_snapshot(cur) -> dict[str, Any]:
    cur.execute("SELECT status,COUNT(*) count FROM label_tasks GROUP BY status")
    status_counts = {row["status"]: int(row["count"]) for row in cur.fetchall()}
    cur.execute("""SELECT u.username,
      (SELECT COUNT(*) FROM label_tasks t WHERE t.assigned_to=u.id AND t.status='in_progress') in_progress,
      (SELECT COUNT(*) FROM label_revisions r WHERE r.actor_id=u.id AND r.imported=0 AND r.action='approved') approvals,
      (SELECT COUNT(*) FROM label_revisions r WHERE r.actor_id=u.id AND r.imported=0 AND r.action IN ('draft','autosave')) saves
      FROM label_admin_users u WHERE u.is_active=1 ORDER BY u.username""")
    return {
        "status_counts": status_counts,
        "users": cur.fetchall(),
        "total": sum(status_counts.values()),
    }


def release_expired(cur) -> int:
    now = utcnow()
    cur.execute("""UPDATE label_tasks SET status=COALESCE(claimed_from_status,'pending'),assigned_to=NULL,
      claimed_at=NULL,lease_expires_at=NULL,claimed_from_status=NULL,updated_at=%s
      WHERE status='in_progress' AND lease_expires_at<%s""", (now, now))
    return cur.rowcount


def lock_user_claims(cur, user_id: int) -> None:
    cur.execute("SELECT id FROM label_admin_users WHERE id=%s FOR UPDATE", (user_id,))
    if not cur.fetchone():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "사용자 계정을 찾을 수 없습니다.")


def claimed_task(cur, task_id: int, user_id: int, version: int) -> dict[str, Any]:
    cur.execute(task_select() + " WHERE t.id=%s FOR UPDATE", (task_id,))
    task = cur.fetchone()
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다.")
    if task["status"] != "in_progress" or task.get("assigned_to") != user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "먼저 이 작업을 가져와야 합니다.")
    if not task.get("lease_expires_at") or task["lease_expires_at"] <= utcnow():
        raise HTTPException(status.HTTP_409_CONFLICT, "작업 잠금 시간이 만료되었습니다. 다시 가져와 주세요.")
    if int(task["version"]) != version:
        raise HTTPException(status.HTTP_409_CONFLICT, "다른 저장본이 있습니다. 작업을 다시 불러와 주세요.")
    return task


def insert_revision(cur, task: dict[str, Any], user_id: int, action: str, payload: dict[str, Any], note: str | None) -> tuple[int, int]:
    version = int(task["version"]) + 1
    now = utcnow()
    cur.execute("""INSERT INTO label_revisions(task_id,version,actor_id,action,payload,note,created_at)
      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
      (task["id"], version, user_id, action, json.dumps(payload, ensure_ascii=False), note, now))
    return int(cur.lastrowid), version


def validate_publish(payload: dict[str, Any]) -> None:
    roles = [row["role"] for row in payload["objects"]]
    if "target" not in roles:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "승인하려면 target 객체가 하나 이상 필요합니다.")
    if "ambiguous" in roles:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "ambiguous 객체는 target, decoy 또는 invalid로 정리해 주세요.")
    keys = [row["object_key"] for row in payload["objects"]]
    if len(keys) != len(set(keys)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "object_key가 중복되었습니다.")


def create_admin_router(database: Database, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["label-admin"])
    auth = AdminAuth(database, settings)

    @router.post("/session")
    def login(payload: LoginRequest, request: Request, response: Response):
        return {"user": auth.login(request, response, payload.username, payload.password)}

    @router.get("/session")
    def session(request: Request):
        return {"user": public_user(auth.current_user(request, allow_password_change=True))}

    @router.delete("/session")
    def logout(request: Request, response: Response):
        auth.logout(request, response)
        return {"logged_out": True}

    @router.put("/password")
    def change_password(payload: PasswordRequest, request: Request):
        return {"user": auth.change_password(request, payload.current_password, payload.new_password)}

    @router.get("/progress")
    def progress(request: Request):
        auth.current_user(request)
        with database.connection() as conn, conn.cursor() as cur:
            release_expired(cur)
            data = progress_snapshot(cur)
            conn.commit()
        return data

    @router.get("/users")
    def users(request: Request):
        auth.current_user(request)
        with database.connection(True) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM label_admin_users WHERE is_active=1 ORDER BY username")
            return {"users": [public_user(row) for row in cur.fetchall()]}

    @router.post("/users/{username}/reset-password")
    def reset_password(username: str, payload: ResetPasswordRequest, request: Request):
        actor = auth.current_user(request)
        temporary = payload.temporary_password or secrets.token_urlsafe(15)
        try:
            encoded = hash_password(temporary)
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "임시 비밀번호는 12자 이상이어야 합니다.") from error
        now = utcnow()
        with database.connection() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE label_admin_users SET password_hash=%s,must_change_password=1,updated_at=%s
              WHERE username=%s AND is_active=1""", (encoded, now, username.lower()))
            if cur.rowcount != 1:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "계정을 찾을 수 없습니다.")
            cur.execute("DELETE s FROM label_admin_sessions s JOIN label_admin_users u ON u.id=s.user_id WHERE u.username=%s", (username.lower(),))
            cur.execute("""INSERT INTO label_events(actor_id,event_type,details,created_at)
              VALUES(%s,'password_reset',JSON_OBJECT('username',%s),%s)""", (actor["id"], username.lower(), now))
            conn.commit()
        return {"username": username.lower(), "temporary_password": temporary, "must_change_password": True}

    @router.get("/tasks")
    def tasks(request: Request, view: str = "pending", limit: int = 50, offset: int = 0):
        user = auth.current_user(request)
        limit = max(1, min(limit, 200)); offset = max(0, offset)
        where, params = task_view_clause(view, user["id"])
        order_by = task_order_clause(view)
        with database.connection() as conn, conn.cursor() as cur:
            release_expired(cur)
            cur.execute(task_select() + " WHERE " + where + " ORDER BY " + order_by + " LIMIT %s OFFSET %s", (*params, limit, offset))
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) count FROM label_tasks t WHERE " + where, params)
            total = int(cur.fetchone()["count"])
            conn.commit()
        return {"items": [serialize_task(row) for row in rows], "view": view, "total": total}

    @router.get("/sync")
    def sync_state(request: Request, view: str = "pending", limit: int = 100, selected_task_id: int | None = None):
        user = auth.current_user(request)
        limit = max(1, min(limit, 200))
        where, params = task_view_clause(view, user["id"])
        order_by = task_order_clause(view)
        with database.connection() as conn, conn.cursor() as cur:
            release_expired(cur)
            progress_data = progress_snapshot(cur)
            cur.execute(task_select() + " WHERE " + where + " ORDER BY " + order_by + " LIMIT %s", (*params, limit))
            rows = cur.fetchall()
            cur.execute("SELECT COUNT(*) count FROM label_tasks t WHERE " + where, params)
            total = int(cur.fetchone()["count"])
            selected = None
            if selected_task_id is not None:
                cur.execute(task_select() + " WHERE t.id=%s", (selected_task_id,))
                selected = cur.fetchone()
            conn.commit()
        return {
            "progress": progress_data,
            "items": [serialize_task(row) for row in rows],
            "view": view,
            "total": total,
            "selected_task": serialize_task(selected) if selected else None,
            "synced_at": utcnow().isoformat() + "Z",
        }

    @router.get("/tasks/{task_id}")
    def task(task_id: int, request: Request):
        auth.current_user(request)
        with database.connection() as conn, conn.cursor() as cur:
            release_expired(cur)
            cur.execute(task_select() + " WHERE t.id=%s", (task_id,))
            row = cur.fetchone()
            conn.commit()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다.")
        return {"task": serialize_task(row)}

    @router.post("/tasks/claim-next")
    def claim_next(request: Request):
        user = auth.current_user(request); now = utcnow(); lease = now + timedelta(minutes=settings.label_task_lease_minutes)
        with database.connection() as conn, conn.cursor() as cur:
            lock_user_claims(cur, user["id"])
            release_expired(cur)
            cur.execute(task_select() + " WHERE t.assigned_to=%s AND t.status='in_progress' ORDER BY t.claimed_at LIMIT 1 FOR UPDATE", (user["id"],))
            row = cur.fetchone()
            if not row:
                cur.execute(task_select() + " WHERE t.status IN ('needs_reconciliation','pending','draft','rejected','publish_failed') "
                            "ORDER BY t.current_batch DESC,FIELD(t.status,'needs_reconciliation','pending','draft','rejected','publish_failed'),t.updated_at,t.id LIMIT 1 FOR UPDATE SKIP LOCKED")
                row = cur.fetchone()
                if not row:
                    conn.commit()
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "가져올 작업이 없습니다.")
                cur.execute("""UPDATE label_tasks SET claimed_from_status=status,status='in_progress',assigned_to=%s,
                  claimed_at=%s,lease_expires_at=%s,updated_at=%s WHERE id=%s""", (user["id"], now, lease, now, row["id"]))
                cur.execute("""INSERT INTO label_events(task_id,actor_id,event_type,details,created_at)
                  VALUES(%s,%s,'claimed',JSON_OBJECT('lease_minutes',%s),%s)""", (row["id"], user["id"], settings.label_task_lease_minutes, now))
            else:
                cur.execute("UPDATE label_tasks SET lease_expires_at=%s WHERE id=%s", (lease, row["id"]))
            conn.commit()
        return task(int(row["id"]), request)

    @router.post("/tasks/{task_id}/claim")
    def claim(task_id: int, request: Request):
        user = auth.current_user(request); now = utcnow(); lease = now + timedelta(minutes=settings.label_task_lease_minutes)
        with database.connection() as conn, conn.cursor() as cur:
            lock_user_claims(cur, user["id"])
            release_expired(cur)
            cur.execute("SELECT id FROM label_tasks WHERE assigned_to=%s AND status='in_progress' AND id<>%s LIMIT 1", (user["id"], task_id))
            if cur.fetchone():
                raise HTTPException(status.HTTP_409_CONFLICT, "현재 작업을 저장하거나 반납한 뒤 다른 작업을 가져와 주세요.")
            cur.execute(task_select() + " WHERE t.id=%s FOR UPDATE", (task_id,)); row = cur.fetchone()
            if not row:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "작업을 찾을 수 없습니다.")
            if row["status"] == "publish_pending":
                raise HTTPException(status.HTTP_409_CONFLICT, "현재 배포 중인 작업입니다.")
            if row["status"] == "in_progress" and row.get("assigned_to") != user["id"]:
                raise HTTPException(status.HTTP_409_CONFLICT, "다른 팀원이 작업 중입니다.")
            from_status = row.get("claimed_from_status") if row["status"] == "in_progress" else row["status"]
            cur.execute("""UPDATE label_tasks SET claimed_from_status=%s,status='in_progress',assigned_to=%s,
              claimed_at=%s,lease_expires_at=%s,updated_at=%s WHERE id=%s""", (from_status, user["id"], now, lease, now, task_id))
            conn.commit()
        return task(task_id, request)

    @router.post("/tasks/{task_id}/heartbeat")
    def heartbeat(task_id: int, request: Request):
        user = auth.current_user(request); now = utcnow(); expires = now + timedelta(minutes=settings.label_task_lease_minutes)
        with database.connection() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE label_tasks SET lease_expires_at=%s
              WHERE id=%s AND assigned_to=%s AND status='in_progress' AND lease_expires_at>=%s""",
                        (expires, task_id, user["id"], now))
            if cur.rowcount != 1:
                release_expired(cur)
                conn.commit()
                raise HTTPException(status.HTTP_409_CONFLICT, "작업 잠금이 만료되었습니다.")
            conn.commit()
        return {"lease_expires_at": expires.isoformat() + "Z"}

    @router.post("/tasks/{task_id}/release")
    def release(task_id: int, request: Request):
        user = auth.current_user(request); now = utcnow()
        with database.connection() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE label_tasks SET status=COALESCE(claimed_from_status,'draft'),assigned_to=NULL,
              claimed_at=NULL,lease_expires_at=NULL,claimed_from_status=NULL,updated_at=%s
              WHERE id=%s AND assigned_to=%s AND status='in_progress'""", (now, task_id, user["id"]))
            if cur.rowcount != 1:
                raise HTTPException(status.HTTP_409_CONFLICT, "반납할 작업이 없습니다.")
            conn.commit()
        return {"released": True}

    @router.put("/tasks/{task_id}/draft")
    def save_draft(task_id: int, payload: SaveRequest, request: Request):
        user = auth.current_user(request); body = payload.payload.model_dump(); action = "autosave" if payload.autosave else "draft"; now = utcnow()
        with database.connection() as conn, conn.cursor() as cur:
            task_row = claimed_task(cur, task_id, user["id"], payload.version)
            revision_id, version = insert_revision(cur, task_row, user["id"], action, body, payload.note)
            cur.execute("""UPDATE label_tasks SET current_payload=%s,version=%s,updated_at=%s,lease_expires_at=%s
              WHERE id=%s""", (json.dumps(body, ensure_ascii=False), version, now,
              now + timedelta(minutes=settings.label_task_lease_minutes), task_id))
            conn.commit()
        return {"saved": True, "version": version, "revision_id": revision_id}

    @router.post("/tasks/{task_id}/approve")
    def approve(task_id: int, payload: VersionRequest, request: Request):
        user = auth.current_user(request); now = utcnow()
        with database.connection() as conn, conn.cursor() as cur:
            task_row = claimed_task(cur, task_id, user["id"], payload.version)
            body = payload.payload.model_dump() if payload.payload else json_value(task_row["current_payload"])
            validate_publish(body); body["reviewer"] = user["username"]
            revision_id, version = insert_revision(cur, task_row, user["id"], "approved", body, payload.note)
            cur.execute("""INSERT INTO label_publish_jobs(task_id,revision_id,requested_by,status,created_at)
              VALUES(%s,%s,%s,'pending',%s)""", (task_id, revision_id, user["id"], now))
            job_id = int(cur.lastrowid)
            cur.execute("""UPDATE label_tasks SET current_payload=%s,version=%s,status='publish_pending',
              assigned_to=NULL,claimed_at=NULL,lease_expires_at=NULL,claimed_from_status=NULL,updated_at=%s WHERE id=%s""",
              (json.dumps(body, ensure_ascii=False), version, now, task_id))
            conn.commit()
        try:
            return publish_job(database, settings, job_id)
        except Exception as error:
            return {"job_id": job_id, "status": "failed", "detail": str(error)}

    @router.post("/tasks/{task_id}/reject")
    def reject(task_id: int, payload: VersionRequest, request: Request):
        user = auth.current_user(request); now = utcnow()
        with database.connection() as conn, conn.cursor() as cur:
            task_row = claimed_task(cur, task_id, user["id"], payload.version)
            body = payload.payload.model_dump() if payload.payload else json_value(task_row["current_payload"])
            revision_id, version = insert_revision(cur, task_row, user["id"], "rejected", body, payload.note)
            cur.execute("""UPDATE label_tasks SET current_payload=%s,version=%s,status='rejected',assigned_to=NULL,
              claimed_at=NULL,lease_expires_at=NULL,claimed_from_status=NULL,updated_at=%s WHERE id=%s""",
              (json.dumps(body, ensure_ascii=False), version, now, task_id))
            conn.commit()
        manifest_count = deactivate_question(database, settings, task_row.get("existing_question_id"))
        return {"status": "rejected", "version": version, "revision_id": revision_id, "manifest_count": manifest_count}

    @router.get("/tasks/{task_id}/revisions")
    def revisions(task_id: int, request: Request):
        auth.current_user(request)
        with database.connection(True) as conn, conn.cursor() as cur:
            cur.execute("""SELECT r.id,r.version,r.action,r.note,r.imported,r.source_file,r.source_line,r.created_at,
              u.username actor FROM label_revisions r LEFT JOIN label_admin_users u ON u.id=r.actor_id
              WHERE r.task_id=%s ORDER BY r.id DESC""", (task_id,))
            rows = cur.fetchall()
        for row in rows:
            row["created_at"] = row["created_at"].isoformat() + "Z"
            row["imported"] = bool(row["imported"])
        return {"revisions": rows}

    @router.post("/publish/{job_id}/retry")
    def retry_publish(job_id: int, request: Request):
        auth.current_user(request)
        return publish_job(database, settings, job_id)

    @router.get("/assets/{path:path}")
    def asset(path: str, request: Request):
        auth.current_user(request)
        try:
            source = safe_source(settings.labeling_dir, path)
        except ValueError:
            source = safe_source(settings.final_dir, path)
        return FileResponse(source)

    @router.get("/final-assets/{path:path}")
    def final_asset(path: str, request: Request):
        auth.current_user(request)
        return FileResponse(safe_source(settings.final_dir, path))

    return router