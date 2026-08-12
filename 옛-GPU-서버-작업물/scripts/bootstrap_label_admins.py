from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.admin_auth import hash_password
from app.config import settings
from app.db import Database, utcnow


USERS = ("ms", "my", "sw", "jy")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the four internal labeling administrators")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reset-existing", action="store_true")
    args = parser.parse_args()
    database = Database(settings); database.initialize(); now = utcnow(); credentials = []
    with database.connection() as conn, conn.cursor() as cur:
        for username in USERS:
            cur.execute("SELECT id FROM label_admin_users WHERE username=%s", (username,))
            existing = cur.fetchone()
            if existing and not args.reset_existing:
                credentials.append({"username": username, "status": "existing_password_unchanged"})
                continue
            temporary = secrets.token_urlsafe(18)
            encoded = hash_password(temporary)
            if existing:
                cur.execute("""UPDATE label_admin_users SET display_name=%s,password_hash=%s,role='admin',is_active=1,
                  must_change_password=1,updated_at=%s WHERE id=%s""", (username.upper(), encoded, now, existing["id"]))
                cur.execute("DELETE FROM label_admin_sessions WHERE user_id=%s", (existing["id"],))
            else:
                cur.execute("""INSERT INTO label_admin_users
                  (username,display_name,password_hash,role,is_active,must_change_password,created_at,updated_at)
                  VALUES(%s,%s,%s,'admin',1,1,%s,%s)""", (username, username.upper(), encoded, now, now))
            credentials.append({"username": username, "temporary_password": temporary, "must_change_password": True})
        conn.commit()
    document = json.dumps({"accounts": credentials}, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(document, encoding="utf-8")
        os.chmod(args.output, 0o600)
        print(json.dumps({"created": len([row for row in credentials if "temporary_password" in row]), "output": str(args.output)}))
    else:
        print(document, end="")


if __name__ == "__main__":
    main()