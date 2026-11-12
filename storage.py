"""SQLite storage. Replaces data.json; the rest of the app is unchanged."""
import json, os, sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.environ.get("SCREENSHOT_DB", os.path.join(BASE, "screenshots.db"))
OLD_JSON = os.path.join(BASE, "data.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS screenshots (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    saved             TEXT NOT NULL,
    category          TEXT NOT NULL,
    priority          INTEGER NOT NULL DEFAULT 2,
    detected_deadline TEXT,
    reasons           TEXT NOT NULL DEFAULT '[]',
    uploaded          TEXT NOT NULL,
    source            TEXT NOT NULL DEFAULT 'upload',
    source_path       TEXT,
    source_mtime      REAL,
    manual            INTEGER NOT NULL DEFAULT 0,
    handled           INTEGER NOT NULL DEFAULT 0,
    text              TEXT NOT NULL DEFAULT '',      -- OCR text (used for search)
    confidence        REAL,                          -- 0..1, how sure the categorizer was
    category_manual   INTEGER NOT NULL DEFAULT 0,    -- 1 = category corrected by the user
    original_name     TEXT,                          -- filename before automatic naming
    description       TEXT NOT NULL DEFAULT '',      -- note written by the user
    deleted_at        TEXT,                          -- set = in the trash bin (NULL = in the library)
    file_hash         TEXT                           -- SHA-256 of the image (duplicate detection)
);
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    deadline      TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 2,
    screenshot_id TEXT,
    done          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS deleted_sources (      -- deleted screenshots: never import them again
    source_path  TEXT NOT NULL,
    source_mtime REAL NOT NULL,
    PRIMARY KEY (source_path, source_mtime)
);
CREATE INDEX IF NOT EXISTS idx_shots_category ON screenshots(category);
CREATE INDEX IF NOT EXISTS idx_tasks_active   ON tasks(done, deadline);
"""
# columns added after the first release: added to existing databases on startup
NEW_COLUMNS = {"text": "TEXT NOT NULL DEFAULT ''", "confidence": "REAL",
               "category_manual": "INTEGER NOT NULL DEFAULT 0", "original_name": "TEXT",
               "description": "TEXT NOT NULL DEFAULT ''", "deleted_at": "TEXT",
               "file_hash": "TEXT"}


def _upgrade(conn):
    have = {r["name"] for r in conn.execute("PRAGMA table_info(screenshots)")}
    for col, ddl in NEW_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE screenshots ADD COLUMN {col} {ddl}")


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _shot_from_row(r):
    d = dict(r)
    d["reasons"] = json.loads(d["reasons"] or "[]")
    d["manual"], d["handled"] = bool(d["manual"]), bool(d["handled"])
    d["category_manual"] = bool(d["category_manual"])
    return d


def _task_from_row(r):
    d = dict(r)
    d["done"] = bool(d["done"])
    return d


def load():
    with connect() as conn:
        conn.executescript(SCHEMA)
        _upgrade(conn)
        empty = conn.execute("SELECT COUNT(*) FROM screenshots").fetchone()[0] == 0 \
            and conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    if empty and os.path.exists(OLD_JSON):       # one-time import of an old data.json
        with open(OLD_JSON, encoding="utf-8") as f:
            old = json.load(f)
        save({"screenshots": old.get("screenshots", {}), "tasks": old.get("tasks", {})})
        os.replace(OLD_JSON, OLD_JSON + ".bak")
        print("[storage] imported data.json into SQLite (kept as data.json.bak)")

    with connect() as conn:
        shots = {r["id"]: _shot_from_row(r) for r in conn.execute("SELECT * FROM screenshots")}
        tasks = {r["id"]: _task_from_row(r) for r in conn.execute("SELECT * FROM tasks")}
    return {"screenshots": shots, "tasks": tasks}


def load_deleted_sources():
    with connect() as conn:
        return {(r[0], r[1]) for r in conn.execute("SELECT source_path, source_mtime FROM deleted_sources")}


def add_deleted_source(path, mtime):
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO deleted_sources VALUES (?, ?)", (path, mtime))


def save(data):
    shots, tasks = data["screenshots"], data["tasks"]
    with connect() as conn:                      # one transaction: all or nothing
        conn.executemany(
            """INSERT OR REPLACE INTO screenshots
               (id, name, saved, category, priority, detected_deadline, reasons, uploaded,
                source, source_path, source_mtime, manual, handled,
                text, confidence, category_manual, original_name, description, deleted_at, file_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(s["id"], s["name"], s["saved"], s["category"], int(s.get("priority", 2)),
              s.get("detected_deadline"), json.dumps(s.get("reasons", [])), s["uploaded"],
              s.get("source", "upload"), s.get("source_path"), s.get("source_mtime"),
              int(bool(s.get("manual"))), int(bool(s.get("handled"))),
              s.get("text") or "", s.get("confidence"), int(bool(s.get("category_manual"))),
              s.get("original_name"), s.get("description") or "", s.get("deleted_at"),
              s.get("file_hash"))
             for s in shots.values()])
        conn.executemany(
            """INSERT OR REPLACE INTO tasks (id, title, deadline, priority, screenshot_id, done)
               VALUES (?,?,?,?,?,?)""",
            [(t["id"], t["title"], t["deadline"], int(t["priority"]),
              t.get("screenshot_id"), int(bool(t["done"]))) for t in tasks.values()])
        _delete_missing(conn, "screenshots", shots.keys())
        _delete_missing(conn, "tasks", tasks.keys())


def _delete_missing(conn, table, keep_ids):
    keep = set(keep_ids)
    for (rid,) in conn.execute(f"SELECT id FROM {table}").fetchall():
        if rid not in keep:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (rid,))