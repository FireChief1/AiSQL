"""Seed the demo app_users table with bcrypt-hashed passwords.

Run once after migrations/apply.py:

    python migrations/seed_users.py

Re-running rotates the passwords back to the demo defaults. Each user_id
ends up mapped to (password_hash, role) so /api/auth/login can verify
credentials and issue a JWT.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth import seed_demo_users, DEMO_USERS  # noqa: E402


def main() -> int:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1

    with psycopg.connect(database_url) as conn:
        seed_demo_users(conn)
        conn.commit()
        # Grant audit-writer SELECT/INSERT access? No - app_users is
        # read by the JWT login flow which runs as superuser in dev.
        # In production, you'd create a dedicated read-only app_auth_reader role.

    print(f"Seeded {len(DEMO_USERS)} demo users: "
          f"{', '.join(DEMO_USERS.keys())}")
    print("Demo passwords are documented in auth.py (DEMO_USERS dict).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
