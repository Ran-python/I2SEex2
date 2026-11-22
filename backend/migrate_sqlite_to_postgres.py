"""One-time idempotent migration script: local SQLite (./backend/attendance.db) -> Neon PostgreSQL.

Usage (from ./backend):
    .venv\\Scripts\\python.exe migrate_sqlite_to_postgres.py
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from core import config
from core.database import Base, engine, utcnow
import modules.roster.models  # noqa: F401
import modules.attendance.models  # noqa: F401


def _parse_dt(val) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip().rstrip("Z")
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return utcnow()


def _sqlite_table_exists(cur: sqlite3.Cursor, name: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    return cur.fetchone() is not None


def migrate_sqlite_to_postgres(
    sqlite_path: Path | None = None,
    verbose: bool = True,
) -> dict[str, int]:
    """Read all rows from SQLite attendance.db and upsert them into PostgreSQL, resetting SERIAL sequences."""
    sqlite_file = sqlite_path or config.DATABASE_PATH
    if not config.IS_POSTGRES:
        raise RuntimeError(
            f"DATABASE_URL is not pointing to PostgreSQL (current: {config.DATABASE_SUMMARY}). "
            "Set DATABASE_URL in .env / backend/.env first."
        )

    # 1. Ensure all tables exist in Neon PostgreSQL.
    Base.metadata.create_all(bind=engine)

    counts = {
        "courses": 0,
        "students": 0,
        "lessons": 0,
        "attendance_records": 0,
        "attendance_alerts": 0,
        "device_locks": 0,
    }

    if not sqlite_file.is_file():
        if verbose:
            print(f"No local SQLite file found at {sqlite_file}; skipping data copy.")
        return counts

    s_conn = sqlite3.connect(str(sqlite_file))
    s_conn.row_factory = sqlite3.Row
    s_cur = s_conn.cursor()

    try:
        with engine.begin() as pg_conn:
            # --- 1. Courses ---
            if _sqlite_table_exists(s_cur, "courses"):
                for row in s_cur.execute("SELECT id, name, created_at FROM courses ORDER BY id"):
                    pg_conn.execute(
                        text(
                            """
                            INSERT INTO courses (id, name, created_at)
                            VALUES (:id, :name, :created_at)
                            ON CONFLICT (id) DO UPDATE SET
                                name = EXCLUDED.name,
                                created_at = EXCLUDED.created_at
                            """
                        ),
                        {
                            "id": int(row["id"]),
                            "name": str(row["name"]),
                            "created_at": _parse_dt(row["created_at"]) or utcnow(),
                        },
                    )
                    counts["courses"] += 1

            # --- 2. Students ---
            if _sqlite_table_exists(s_cur, "students"):
                cols = {r[1] for r in s_cur.execute("PRAGMA table_info(students)").fetchall()}
                has_course = "course_id" in cols
                has_present = "is_present" in cols
                for row in s_cur.execute("SELECT * FROM students ORDER BY id"):
                    pg_conn.execute(
                        text(
                            """
                            INSERT INTO students
                                (id, course_id, full_name, id_number, email, is_present, in_roster, created_at)
                            VALUES
                                (:id, :course_id, :full_name, :id_number, :email, :is_present, :in_roster, :created_at)
                            ON CONFLICT (id) DO UPDATE SET
                                course_id = EXCLUDED.course_id,
                                full_name = EXCLUDED.full_name,
                                id_number = EXCLUDED.id_number,
                                email = EXCLUDED.email,
                                is_present = EXCLUDED.is_present,
                                in_roster = EXCLUDED.in_roster
                            """
                        ),
                        {
                            "id": int(row["id"]),
                            "course_id": int(row["course_id"]) if has_course and row["course_id"] else 1,
                            "full_name": str(row["full_name"]),
                            "id_number": row["id_number"],
                            "email": row["email"],
                            "is_present": bool(row["is_present"]) if has_present else False,
                            "in_roster": bool(row["in_roster"]),
                            "created_at": _parse_dt(row["created_at"]) or utcnow(),
                        },
                    )
                    counts["students"] += 1

            # --- 3. Lessons ---
            if _sqlite_table_exists(s_cur, "lessons"):
                for row in s_cur.execute(
                    "SELECT id, course_id, lesson_number, title, status, opened_at, expires_at, closed_at, qr_token, checkin_url "
                    "FROM lessons ORDER BY id"
                ):
                    pg_conn.execute(
                        text(
                            """
                            INSERT INTO lessons
                                (id, course_id, lesson_number, title, status, opened_at, expires_at, closed_at, qr_token, checkin_url)
                            VALUES
                                (:id, :course_id, :lesson_number, :title, :status, :opened_at, :expires_at, :closed_at, :qr_token, :checkin_url)
                            ON CONFLICT (id) DO UPDATE SET
                                course_id = EXCLUDED.course_id,
                                lesson_number = EXCLUDED.lesson_number,
                                title = EXCLUDED.title,
                                status = EXCLUDED.status,
                                opened_at = EXCLUDED.opened_at,
                                expires_at = EXCLUDED.expires_at,
                                closed_at = EXCLUDED.closed_at,
                                qr_token = EXCLUDED.qr_token,
                                checkin_url = EXCLUDED.checkin_url
                            """
                        ),
                        {
                            "id": int(row["id"]),
                            "course_id": int(row["course_id"]),
                            "lesson_number": int(row["lesson_number"]),
                            "title": str(row["title"]),
                            "status": str(row["status"]),
                            "opened_at": _parse_dt(row["opened_at"]) or utcnow(),
                            "expires_at": _parse_dt(row["expires_at"]) or utcnow(),
                            "closed_at": _parse_dt(row["closed_at"]),
                            "qr_token": str(row["qr_token"]),
                            "checkin_url": str(row["checkin_url"]),
                        },
                    )
                    counts["lessons"] += 1

            # --- 4. Attendance Records ---
            if _sqlite_table_exists(s_cur, "attendance_records"):
                cols = {r[1] for r in s_cur.execute("PRAGMA table_info(attendance_records)").fetchall()}
                fk_col = "lesson_id" if "lesson_id" in cols else "session_id"
                for row in s_cur.execute(
                    f"SELECT id, {fk_col} AS lesson_id, student_id, timestamp, status, device_fingerprint "
                    "FROM attendance_records ORDER BY id"
                ):
                    pg_conn.execute(
                        text(
                            """
                            INSERT INTO attendance_records
                                (id, lesson_id, student_id, timestamp, status, device_fingerprint)
                            VALUES
                                (:id, :lesson_id, :student_id, :timestamp, :status, :device_fingerprint)
                            ON CONFLICT (id) DO UPDATE SET
                                lesson_id = EXCLUDED.lesson_id,
                                student_id = EXCLUDED.student_id,
                                timestamp = EXCLUDED.timestamp,
                                status = EXCLUDED.status,
                                device_fingerprint = EXCLUDED.device_fingerprint
                            """
                        ),
                        {
                            "id": int(row["id"]),
                            "lesson_id": int(row["lesson_id"]),
                            "student_id": int(row["student_id"]),
                            "timestamp": _parse_dt(row["timestamp"]) or utcnow(),
                            "status": str(row["status"]),
                            "device_fingerprint": row["device_fingerprint"],
                        },
                    )
                    counts["attendance_records"] += 1

            # --- 5. Attendance Alerts ---
            if _sqlite_table_exists(s_cur, "attendance_alerts"):
                cols = {r[1] for r in s_cur.execute("PRAGMA table_info(attendance_alerts)").fetchall()}
                fk_col = "lesson_id" if "lesson_id" in cols else "session_id"
                for row in s_cur.execute(
                    f"SELECT id, {fk_col} AS lesson_id, student_id, full_name, id_number, email, created_at, dismissed "
                    "FROM attendance_alerts ORDER BY id"
                ):
                    pg_conn.execute(
                        text(
                            """
                            INSERT INTO attendance_alerts
                                (id, lesson_id, student_id, full_name, id_number, email, created_at, dismissed)
                            VALUES
                                (:id, :lesson_id, :student_id, :full_name, :id_number, :email, :created_at, :dismissed)
                            ON CONFLICT (id) DO UPDATE SET
                                dismissed = EXCLUDED.dismissed
                            """
                        ),
                        {
                            "id": int(row["id"]),
                            "lesson_id": int(row["lesson_id"]),
                            "student_id": int(row["student_id"]) if row["student_id"] is not None else None,
                            "full_name": str(row["full_name"]),
                            "id_number": row["id_number"],
                            "email": row["email"],
                            "created_at": _parse_dt(row["created_at"]) or utcnow(),
                            "dismissed": bool(row["dismissed"]),
                        },
                    )
                    counts["attendance_alerts"] += 1

            # --- 6. Device Locks ---
            if _sqlite_table_exists(s_cur, "device_locks"):
                cols = {r[1] for r in s_cur.execute("PRAGMA table_info(device_locks)").fetchall()}
                fk_col = "lesson_id" if "lesson_id" in cols else "session_id"
                for row in s_cur.execute(
                    f"SELECT id, cookie_token, fingerprint, device_id, client_ip, {fk_col} AS lesson_id, student_id, created_at, expires_at "
                    "FROM device_locks ORDER BY id"
                ):
                    pg_conn.execute(
                        text(
                            """
                            INSERT INTO device_locks
                                (id, cookie_token, fingerprint, device_id, client_ip, lesson_id, student_id, created_at, expires_at)
                            VALUES
                                (:id, :cookie_token, :fingerprint, :device_id, :client_ip, :lesson_id, :student_id, :created_at, :expires_at)
                            ON CONFLICT (id) DO NOTHING
                            """
                        ),
                        {
                            "id": int(row["id"]),
                            "cookie_token": str(row["cookie_token"]),
                            "fingerprint": str(row["fingerprint"]),
                            "device_id": row["device_id"],
                            "client_ip": row["client_ip"],
                            "lesson_id": int(row["lesson_id"]) if row["lesson_id"] is not None else None,
                            "student_id": int(row["student_id"]) if row["student_id"] is not None else None,
                            "created_at": _parse_dt(row["created_at"]) or utcnow(),
                            "expires_at": _parse_dt(row["expires_at"]) or utcnow(),
                        },
                    )
                    counts["device_locks"] += 1

            # --- 7. Reset PostgreSQL SERIAL sequences so new inserts continue from MAX(id) + 1 ---
            for table_name in (
                "courses",
                "students",
                "lessons",
                "attendance_records",
                "attendance_alerts",
                "device_locks",
            ):
                pg_conn.execute(
                    text(
                        f"""
                        SELECT setval(
                            pg_get_serial_sequence('{table_name}', 'id'),
                            COALESCE((SELECT MAX(id) FROM {table_name}), 1),
                            (SELECT COUNT(*) > 0 FROM {table_name})
                        )
                        """
                    )
                )
    finally:
        s_cur.close()
        s_conn.close()

    if verbose:
        print(f"Successfully migrated SQLite ({sqlite_file}) -> {config.DATABASE_SUMMARY}:")
        for tbl, cnt in counts.items():
            print(f"  - {tbl}: {cnt} row(s)")
    return counts


if __name__ == "__main__":
    migrate_sqlite_to_postgres(verbose=True)
