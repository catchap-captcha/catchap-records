from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

from app.config import settings
from app.db import Database


BASE = "http://127.0.0.1:8000"


def request(path: str, method: str = "GET", payload: dict | None = None, headers: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def main() -> None:
    session_id = str(uuid.uuid4())
    status, challenge = request("/api/captcha/challenges", "POST",
        {"purpose": "signup", "risk_level": "high", "session_id": session_id},
        {"X-Captcha-Site-Key": settings.site_key})
    assert status == 201
    db = Database(settings)
    stored = db.challenge_for_verify(challenge["challenge_id"])
    targets = [row["temporary_object_id"] for row in stored["objects"] if row["role"] == "target"]
    now = int(time.time() * 1000)
    status, verified = request(f"/api/captcha/challenges/{challenge['challenge_id']}/verify", "POST",
        {"selected_object_ids": targets, "session_id": session_id, "duration_ms": 1200,
         "events": [{"type": "challenge_loaded", "timestamp_ms": now},
                    {"type": "pointer_down", "object_id": targets[0], "x": .2, "y": .2, "timestamp_ms": now + 300},
                    {"type": "drag_start", "object_id": targets[0], "x": .2, "y": .2, "timestamp_ms": now + 310},
                    {"type": "drop", "object_id": targets[0], "x": .8, "y": .8, "timestamp_ms": now + 900}]},
        {"X-Captcha-Site-Key": settings.site_key})
    assert status == 200 and verified["success"]
    signup = {"email": f"prototype-{uuid.uuid4()}@example.com", "password": "prototype-password-123",
              "captcha_token": verified["captcha_token"], "session_id": session_id}
    first_status, _ = request("/api/signup", "POST", signup)
    second_status, second = request("/api/signup", "POST", signup)
    print(json.dumps({"signup_first_status": first_status, "signup_second_status": second_status,
                      "signup_second_detail": second.get("detail")}))
    assert first_status == 201 and second_status == 403 and second.get("detail") == "CAPTCHA_REQUIRED"
    print(json.dumps({"question_id": stored["question_id"], "challenge": "passed",
                      "signup": "created", "token_reuse": "rejected", "active_pool": "reviewed-prototype"}))


if __name__ == "__main__":
    main()
