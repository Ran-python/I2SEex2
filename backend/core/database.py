"""SQLAlchemy engine / session setup (Neon PostgreSQL when DATABASE_URL is set, with SQLite fallback)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import DATABASE_PATH, DATABASE_URL, IS_POSTGRES

if IS_POSTGRES:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")   # better concurrent reads while polling
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=True, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utcnow() -> datetime:
    """Naive UTC timestamp."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat(timespec="seconds") + "Z"


def _table_columns(cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _migrate_existing_sqlite_schema() -> None:
    """Idempotent migration for local SQLite databases."""
    from modules.roster.models import DEFAULT_COURSE_NAME

    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")

        now_str = utcnow().isoformat(sep=" ", timespec="seconds")
        cur.execute("SELECT id FROM courses ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO courses (name, created_at) VALUES (?, ?)",
                (DEFAULT_COURSE_NAME, now_str),
            )
            default_course_id = cur.lastrowid
        else:
            default_course_id = row[0]

        student_cols = _table_columns(cur, "students")
        if student_cols and "course_id" not in student_cols:
            cur.execute(
                f"ALTER TABLE students ADD COLUMN course_id INTEGER NOT NULL "
                f"DEFAULT {int(default_course_id)} REFERENCES courses(id) ON DELETE CASCADE"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS ix_students_course_id ON students (course_id)")
        if student_cols and "is_present" not in student_cols:
            cur.execute("ALTER TABLE students ADD COLUMN is_present BOOLEAN NOT NULL DEFAULT 0")

        if _table_exists(cur, "attendance_sessions"):
            cur.execute(
                "SELECT id, token, checkin_url, opened_at, expires_at, closed_at "
                "FROM attendance_sessions ORDER BY id"
            )
            legacy_sessions = cur.fetchall()
            cur.execute(
                "SELECT COALESCE(MAX(lesson_number), 0) FROM lessons WHERE course_id=?",
                (default_course_id,),
            )
            next_num = (cur.fetchone()[0] or 0) + 1
            for sess_id, token, url, opened_at, expires_at, closed_at in legacy_sessions:
                cur.execute("SELECT 1 FROM lessons WHERE id=?", (sess_id,))
                if cur.fetchone() is None:
                    status = "closed" if closed_at else "open"
                    cur.execute(
                        "INSERT INTO lessons "
                        "(id, course_id, lesson_number, title, status, opened_at, expires_at, closed_at, qr_token, checkin_url) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            sess_id,
                            default_course_id,
                            next_num,
                            f"Lesson {next_num}",
                            status,
                            opened_at,
                            expires_at,
                            closed_at,
                            token,
                            url,
                        ),
                    )
                    next_num += 1

        renamed_records = False
        rec_cols = _table_columns(cur, "attendance_records")
        if rec_cols and "lesson_id" not in rec_cols and "session_id" in rec_cols:
            cur.execute("DROP INDEX IF EXISTS ix_attendance_records_session_id")
            cur.execute("DROP INDEX IF EXISTS ix_attendance_records_student_id")
            cur.execute("ALTER TABLE attendance_records RENAME TO _old_attendance_records")
            renamed_records = True

        renamed_locks = False
        lock_cols = _table_columns(cur, "device_locks")
        if lock_cols and "lesson_id" not in lock_cols and "session_id" in lock_cols:
            cur.execute("DROP INDEX IF EXISTS ix_device_locks_cookie_token")
            cur.execute("DROP INDEX IF EXISTS ix_device_locks_fingerprint")
            cur.execute("DROP INDEX IF EXISTS ix_device_locks_device_id")
            cur.execute("DROP INDEX IF EXISTS ix_device_locks_expires_at")
            cur.execute("ALTER TABLE device_locks RENAME TO _old_device_locks")
            renamed_locks = True

        renamed_alerts = False
        alert_cols = _table_columns(cur, "attendance_alerts")
        if alert_cols and "lesson_id" not in alert_cols and "session_id" in alert_cols:
            cur.execute("DROP INDEX IF EXISTS ix_attendance_alerts_session_id")
            cur.execute("ALTER TABLE attendance_alerts RENAME TO _old_attendance_alerts")
            renamed_alerts = True

        raw_conn.commit()
        cur.close()
    finally:
        raw_conn.close()

    if renamed_records or renamed_locks or renamed_alerts:
        Base.metadata.create_all(bind=engine)

    raw_conn = engine.raw_connection()
    try:
        cur = raw_conn.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        if renamed_records and _table_exists(cur, "_old_attendance_records"):
            cur.execute(
                "INSERT OR IGNORE INTO attendance_records "
                "(id, lesson_id, student_id, timestamp, status, device_fingerprint) "
                "SELECT id, session_id, student_id, timestamp, status, device_fingerprint "
                "FROM _old_attendance_records"
            )
            cur.execute("DROP TABLE _old_attendance_records")

        if renamed_locks and _table_exists(cur, "_old_device_locks"):
            cur.execute(
                "INSERT OR IGNORE INTO device_locks "
                "(id, cookie_token, fingerprint, device_id, client_ip, lesson_id, student_id, created_at, expires_at) "
                "SELECT id, cookie_token, fingerprint, device_id, client_ip, session_id, student_id, created_at, expires_at "
                "FROM _old_device_locks"
            )
            cur.execute("DROP TABLE _old_device_locks")

        if renamed_alerts and _table_exists(cur, "_old_attendance_alerts"):
            cur.execute(
                "INSERT OR IGNORE INTO attendance_alerts "
                "(id, lesson_id, student_id, full_name, id_number, email, created_at, dismissed) "
                "SELECT id, session_id, student_id, full_name, id_number, email, created_at, dismissed "
                "FROM _old_attendance_alerts"
            )
            cur.execute("DROP TABLE _old_attendance_alerts")

        if _table_exists(cur, "attendance_sessions"):
            cur.execute("DROP TABLE attendance_sessions")

        cur.execute("UPDATE students SET in_roster = 1 WHERE in_roster = 0")

        cur.execute(
            "UPDATE students SET is_present = 1 WHERE id IN ("
            "  SELECT ar.student_id FROM attendance_records ar "
            "  JOIN lessons l ON l.id = ar.lesson_id "
            "  WHERE l.id = (SELECT l2.id FROM lessons l2 WHERE l2.course_id = l.course_id ORDER BY l2.lesson_number DESC, l2.id DESC LIMIT 1)"
            ")"
        )

        raw_conn.commit()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    finally:
        raw_conn.close()


def _init_postgres_defaults() -> None:
    """Ensure initial data or SQLite migration has been applied on PostgreSQL startup."""
    from modules.roster.models import DEFAULT_COURSE_NAME, Course

    with SessionLocal() as db:
        course_count = db.scalar(select(func.count(Course.id))) or 0

    if course_count == 0 and DATABASE_PATH.is_file():
        try:
            from migrate_sqlite_to_postgres import migrate_sqlite_to_postgres
            migrate_sqlite_to_postgres(verbose=False)
            return
        except Exception:
            pass

    with SessionLocal() as db:
        if (db.scalar(select(func.count(Course.id))) or 0) == 0:
            db.add(Course(name=DEFAULT_COURSE_NAME))
            db.commit()


def init_db() -> None:
    # Import models so they register with Base.metadata before create_all.
    import modules.roster.models  # noqa: F401
    import modules.attendance.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "sqlite":
        _migrate_existing_sqlite_schema()
    else:
        _init_postgres_defaults()
