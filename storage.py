"""SQLite-backed audit log for Provenance Guard.

The audit log is append-only event storage. A submission writes one
'submission' event (status='classified'). An appeal writes an 'appeal' event
that copies the original decision (so it sits alongside it in the log) AND
flips the original submission event's status to 'under_review'.
"""
import sqlite3
import datetime

DB = "provenance.db"


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def init_db():
    con = sqlite3.connect(DB)
    con.execute(
        """CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type       TEXT,
            content_id       TEXT,
            creator_id       TEXT,
            timestamp        TEXT,
            text             TEXT,
            llm_score        REAL,
            stylo_score      REAL,
            confidence       REAL,
            attribution      TEXT,
            status           TEXT,
            appeal_reasoning TEXT
        )"""
    )
    con.commit()
    con.close()


def log_submission(content_id, creator_id, text, llm_score,
                   stylo_score, confidence, attribution, status="classified"):
    con = sqlite3.connect(DB)
    con.execute(
        """INSERT INTO audit_log
           (event_type, content_id, creator_id, timestamp, text,
            llm_score, stylo_score, confidence, attribution, status)
           VALUES ('submission', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (content_id, creator_id, _now(), text,
         llm_score, stylo_score, confidence, attribution, status),
    )
    con.commit()
    con.close()


def log_appeal(content_id, creator_reasoning):
    """Returns the appeal confirmation dict, or None if content_id not found."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute(
        """SELECT creator_id, llm_score, stylo_score, confidence, attribution
           FROM audit_log
           WHERE content_id = ? AND event_type = 'submission'
           ORDER BY id DESC LIMIT 1""",
        (content_id,),
    ).fetchone()
    if row is None:
        con.close()
        return None

    # flip the original decision's status
    con.execute(
        "UPDATE audit_log SET status='under_review' "
        "WHERE content_id = ? AND event_type = 'submission'",
        (content_id,),
    )
    # append the appeal event, carrying the original decision alongside it
    con.execute(
        """INSERT INTO audit_log
           (event_type, content_id, creator_id, timestamp,
            llm_score, stylo_score, confidence, attribution,
            status, appeal_reasoning)
           VALUES ('appeal', ?, ?, ?, ?, ?, ?, ?, 'under_review', ?)""",
        (content_id, row["creator_id"], _now(),
         row["llm_score"], row["stylo_score"], row["confidence"],
         row["attribution"], creator_reasoning),
    )
    con.commit()
    con.close()
    return {"content_id": content_id, "status": "under_review"}


def get_log(limit=20):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]
