from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, Request, Response, status

from .config import Settings
from .db import Database, utcnow


COOKIE_NAME = "label_admin_session"
USERNAME_RE = re.compile(r"^[a-z0-9_-]{2,64}$")


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + digest.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def client_ip_hash(request: Request, secret: str) -> str:
    address = request.client.host if request.client else "unknown"
    return hmac.new(secret.encode("utf-8"), address.encode("utf-8"), hashlib.sha256).hexdigest()


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "must_change_password": bool(row["must_change_password"]),
    }


class AdminAuth:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def login(self, request: Request, response: Response, username: str, password: str) -> dict[str, Any]:
        username = username.strip().lower()
        if not USERNAME_RE.fullmatch(username):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "아이디 또는 비밀번호가 올바르지 않습니다.")
        now = utcnow()
        ip_hash = client_ip_hash(request, self.settings.app_secret)
        failed = False
        with self.database.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*) count FROM label_events
              WHERE event_type='login_failed' AND created_at>DATE_SUB(%s, INTERVAL 15 MINUTE)
                AND JSON_UNQUOTE(JSON_EXTRACT(details,'$.ip_hash'))=%s""", (now, ip_hash))
            if int(cur.fetchone()["count"]) >= 20:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
            cur.execute("SELECT * FROM label_admin_users WHERE username=%s AND is_active=1", (username,))
            user = cur.fetchone()
            if not user or not verify_password(password, user["password_hash"]):
                cur.execute("""INSERT INTO label_events(event_type,details,created_at)
                  VALUES('login_failed',JSON_OBJECT('username',%s,'ip_hash',%s),%s)""", (username, ip_hash, now))
                conn.commit()
                failed = True
            else:
                token = secrets.token_urlsafe(32)
                expires = now + timedelta(hours=self.settings.admin_session_hours)
                cur.execute("DELETE FROM label_admin_sessions WHERE expires_at<=%s", (now,))
                cur.execute("""INSERT INTO label_admin_sessions
                  (token_hash,user_id,expires_at,created_at,last_seen_at,ip_hash)
                  VALUES(%s,%s,%s,%s,%s,%s)""",
                  (session_hash(token), user["id"], expires, now, now, ip_hash))
                cur.execute("UPDATE label_admin_users SET last_login_at=%s WHERE id=%s", (now, user["id"]))
                cur.execute("""INSERT INTO label_events(actor_id,event_type,details,created_at)
                  VALUES(%s,'login',JSON_OBJECT('username',%s),%s)""", (user["id"], username, now))
                conn.commit()
        if failed:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "아이디 또는 비밀번호가 올바르지 않습니다.")
        response.set_cookie(
            COOKIE_NAME, token, max_age=self.settings.admin_session_hours * 3600,
            httponly=True, secure=self.settings.admin_cookie_secure, samesite="strict", path="/",
        )
        return public_user(user)
    def logout(self, request: Request, response: Response) -> None:
        token = request.cookies.get(COOKIE_NAME)
        if token:
            with self.database.connection() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM label_admin_sessions WHERE token_hash=%s", (session_hash(token),))
                conn.commit()
        response.delete_cookie(COOKIE_NAME, path="/", samesite="strict")

    def current_user(self, request: Request, allow_password_change: bool = False) -> dict[str, Any]:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인이 필요합니다.")
        now = utcnow()
        with self.database.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT u.* FROM label_admin_sessions s
              JOIN label_admin_users u ON u.id=s.user_id
              WHERE s.token_hash=%s AND s.expires_at>%s AND u.is_active=1""", (session_hash(token), now))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "로그인 세션이 만료되었습니다.")
            cur.execute("UPDATE label_admin_sessions SET last_seen_at=%s WHERE token_hash=%s", (now, session_hash(token)))
            conn.commit()
        if user["must_change_password"] and not allow_password_change:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "첫 로그인 비밀번호를 변경해 주세요.")
        return user

    def change_password(self, request: Request, current_password: str, new_password: str) -> dict[str, Any]:
        user = self.current_user(request, allow_password_change=True)
        if not verify_password(current_password, user["password_hash"]):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "현재 비밀번호가 올바르지 않습니다.")
        if current_password == new_password:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "새 비밀번호는 현재 비밀번호와 달라야 합니다.")
        try:
            encoded = hash_password(new_password)
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "새 비밀번호는 12자 이상이어야 합니다.") from error
        now = utcnow()
        token = request.cookies.get(COOKIE_NAME)
        with self.database.connection() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE label_admin_users SET password_hash=%s,must_change_password=0,updated_at=%s
              WHERE id=%s""", (encoded, now, user["id"]))
            cur.execute("DELETE FROM label_admin_sessions WHERE user_id=%s AND token_hash<>%s",
                        (user["id"], session_hash(token or "")))
            cur.execute("""INSERT INTO label_events(actor_id,event_type,details,created_at)
              VALUES(%s,'password_changed',JSON_OBJECT(),%s)""", (user["id"], now))
            conn.commit()
        user["must_change_password"] = False
        return public_user(user)