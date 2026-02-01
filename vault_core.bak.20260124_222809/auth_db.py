from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from werkzeug.security import generate_password_hash, check_password_hash


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    # Prefer explicit env var, fallback to your ops directory.
    p = (os.getenv("STRAIGHTLINE_AUTH_DB_PATH") or "/opt/straightline-vault/db/vault.sqlite3").strip()
    return Path(p)


def connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def create_user(username: str, password: str) -> int:
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required")
    if not password:
        raise ValueError("Password is required")

    pw_hash = generate_password_hash(password)

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, is_active, created_at) VALUES (?, ?, 1, ?)",
            (username, pw_hash, utc_now_iso()),
        )
        return int(cur.lastrowid)


def verify_user(username: str, password: str) -> bool:
    username = (username or "").strip()
    if not username or not password:
        return False

    with connect() as conn:
        row = conn.execute(
            "SELECT password_hash, is_active FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if not row:
        return False
    if int(row["is_active"] or 0) != 1:
        return False

    try:
        return bool(check_password_hash(str(row["password_hash"]), password))
    except Exception:
        return False


def consume_invite(code: str, username_to_create: str, password: str) -> None:
    """
    Atomically:
      - ensure invite exists + unused
      - create user
      - mark invite as used by that user
    """
    code = (code or "").strip()
    if not code:
        raise ValueError("Invite code is required")

    username_to_create = (username_to_create or "").strip()
    if not username_to_create:
        raise ValueError("Username is required")
    if not password:
        raise ValueError("Password is required")

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")

        inv = conn.execute(
            "SELECT code, is_used FROM invites WHERE code = ?",
            (code,),
        ).fetchone()

        if not inv:
            raise ValueError("Invalid invite code")
        if int(inv["is_used"] or 0) == 1:
            raise ValueError("Invite code already used")

        # Create the user (will raise on duplicate username)
        pw_hash = generate_password_hash(password)
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, is_active, created_at) VALUES (?, ?, 1, ?)",
            (username_to_create, pw_hash, utc_now_iso()),
        )
        user_id = int(cur.lastrowid)

        conn.execute(
            "UPDATE invites SET is_used = 1, used_by = ?, used_at = ? WHERE code = ?",
            (user_id, utc_now_iso(), code),
        )

        conn.commit()


def create_invite(code: str) -> None:
    """Convenience helper; optional."""
    code = (code or "").strip()
    if not code:
        raise ValueError("Invite code is required")

    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO invites (code, is_used, created_at) VALUES (?, 0, ?)",
            (code, utc_now_iso()),
        )
