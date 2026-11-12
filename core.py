"""Shared state used by both the Flask app and the watchdog folder watcher."""
import hashlib, os, shutil, threading, uuid
from datetime import datetime

from werkzeug.utils import secure_filename

import analyzer, locations, storage
from scheduler import TaskScheduler
from trie import Trie
from notifier import Notifier

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.path.join(BASE, "uploads")
ORGANIZED = os.path.join(BASE, "organized")
DEFAULT_WATCH = os.path.join(BASE, "watch")

ALLOWED = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
WATCH_MODE = os.environ.get("WATCH_MODE", "copy").lower()                 # copy | move
AUTO_DETECT = os.environ.get("AUTO_DETECT", "1") == "1"                   # find OS screenshot folders
WATCH_SCAN_EXISTING = os.environ.get("WATCH_SCAN_EXISTING", "1") == "1"   # organize old screenshots too

# WATCH_DIRS: folder -> dedicated (True = every image is a screenshot,
#                                   False = only screenshot-looking filenames)
WATCH_DIRS = {DEFAULT_WATCH: True}
if AUTO_DETECT:
    WATCH_DIRS.update(locations.detect())
# Extra folders: SCREENSHOT_WATCH_DIR="D:\\Shots;E:\\More"  (separator: ; on Windows, : elsewhere)
for _p in filter(None, os.environ.get("SCREENSHOT_WATCH_DIR", "").split(os.pathsep)):
    WATCH_DIRS[os.path.abspath(os.path.expanduser(_p))] = True
WATCH_DIRS = {p: d for p, d in WATCH_DIRS.items()
              if not p.startswith((ORGANIZED, UPLOADS))}   # never watch our own output
WATCH_DIR = next(iter(WATCH_DIRS))                          # kept for older code

for d in (UPLOADS, ORGANIZED, DEFAULT_WATCH):
    os.makedirs(d, exist_ok=True)

lock = threading.RLock()                      # watcher thread and web threads share the data
state = {"version": 0, "watching": False, "scanning": 0}   # scanning = files still queued

data = storage.load()
data.setdefault("screenshots", {})
data.setdefault("tasks", {})
for _s in data["screenshots"].values():       # upgrade records saved by older versions
    _s.setdefault("priority", 2)
    _s.setdefault("detected_deadline", None)
    _s.setdefault("reasons", [])
    _s.setdefault("source", "upload")
    _s.setdefault("manual", False)
    _s.setdefault("handled", False)
    _s.setdefault("text", "")
    _s.setdefault("confidence", None)
    _s.setdefault("category_manual", False)
    _s["original_name"] = _s.get("original_name") or _s["name"]
    _s.setdefault("description", "")
    _s.setdefault("deleted_at", None)
    _s.setdefault("file_hash", None)

def file_hash(path):
    """SHA-256 of the file's bytes: two identical images always give the same value."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_changed = False
for _s in data["screenshots"].values():       # older screenshots: hash the copy in organized/
    if not _s.get("file_hash"):
        _p = os.path.join(ORGANIZED, _s["category"], _s["saved"])
        if os.path.isfile(_p):
            _s["file_hash"] = file_hash(_p)
            _changed = True
if _changed:
    storage.save(data)

# hash map: file hash -> screenshot id. O(1) duplicate check. Trashed ones count too, so a
# deleted screenshot is not silently re-imported. Live ones win if a hash appears twice.
_hashes = {}
for _s in sorted(data["screenshots"].values(), key=lambda s: not s.get("deleted_at")):
    if _s.get("file_hash"):
        _hashes[_s["file_hash"]] = _s["id"]

scheduler = TaskScheduler(data["tasks"])
notifier = Notifier(scheduler)
trie = Trie()
undo_stack = []                               # stack of screenshot IDs (web uploads only)


def index_for_search(s):
    """Search finds a screenshot by its automatic name, original filename or the text inside it."""
    trie.insert(" ".join([s["name"], s.get("original_name") or "", s.get("description") or "",
                          s.get("text") or ""]), s["id"])


for _s in data["screenshots"].values():
    index_for_search(_s)

# hash set of (source_path, mtime): O(1) duplicate check even with thousands of screenshots
_seen = {(s.get("source_path"), s.get("source_mtime"))
         for s in data["screenshots"].values() if s.get("source_path")}
_seen |= storage.load_deleted_sources()       # deleted screenshots are not imported again


def already_ingested(path):
    return (os.path.abspath(path), os.path.getmtime(path)) in _seen


def persist():
    """Save to SQLite and tell open pages something changed (used for bulk imports)."""
    with lock:
        storage.save(data)
        state["version"] += 1


def corrections():
    """[(set of words, category)] for screenshots whose category the user corrected.
    New screenshots with nearly the same text get the same category."""
    with lock:
        return [(set(analyzer.organizer.normalize(s["text"]).split()), s["category"])
                for s in data["screenshots"].values()
                if s.get("category_manual") and s.get("text")]


def set_category(sid, category, manual=True, confidence=1.0, save=True):
    """Move a screenshot to another category (file + record). Returns the record or None."""
    with lock:
        s = data["screenshots"].get(sid)
        if not s or category not in analyzer.organizer.CATEGORIES:
            return None
        if category != s["category"]:
            old = os.path.join(ORGANIZED, s["category"], s["saved"])
            folder = os.path.join(ORGANIZED, category)
            os.makedirs(folder, exist_ok=True)
            if os.path.exists(old):
                shutil.move(old, os.path.join(folder, s["saved"]))
            s["category"] = category
        s["confidence"] = confidence
        if manual:
            s["category_manual"] = True
        if save:
            storage.save(data)
            state["version"] += 1
        return s


def ingest(path, original_name, source="upload", save=True):
    """Analyse one image, file it under organized/<Category>/ and record it.
    save=False is used while importing many files; call persist() afterwards."""
    h = file_hash(path)
    sid = uuid.uuid4().hex[:8]
    with lock:                                        # check + reserve in one step (thread safe)
        if h in _hashes:
            return _skip_duplicate(path, original_name, source, h)
        _hashes[h] = sid
    try:
        return _ingest(path, original_name, source, save, sid, h)
    except Exception:
        with lock:
            _hashes.pop(h, None)                      # failed: allow a later retry
        raise


def _skip_duplicate(path, name, source, h):
    orig = data["screenshots"].get(_hashes[h])
    where = "in the trash" if orig and orig.get("deleted_at") else "in the library"
    label = orig["name"] if orig else "an earlier screenshot"
    if source == "watch":
        _seen.add((os.path.abspath(path), os.path.getmtime(path)))   # don't look at it again
    else:
        try:
            os.remove(path)                           # the temporary upload copy
        except OSError:
            pass
    notifier.push("Duplicate skipped", f"{name} is already {where} as \"{label}\"")
    print(f"[duplicate] {name} skipped ({where}: {label})")
    return None


def _ingest(path, original_name, source, save, sid, h):
    result = analyzer.analyze(path, original_name, corrections())
    name = result["name"] or original_name            # automatic name from the screenshot's text
    saved = f"{sid}_{secure_filename(name) or original_name}"
    folder = os.path.join(ORGANIZED, result["category"])
    os.makedirs(folder, exist_ok=True)
    final = os.path.join(folder, saved)

    record = {"id": sid, "name": name, "original_name": original_name, "saved": saved,
              "category": result["category"], "confidence": result["confidence"],
              "text": result["text"], "category_manual": False,
              "priority": result["priority"],
              "detected_deadline": result["detected_deadline"], "reasons": result["reasons"],
              "uploaded": datetime.now().isoformat(timespec="seconds"),
              "source": source, "manual": False, "handled": False,
              "description": "", "deleted_at": None, "file_hash": h}
    if source == "watch":
        record["source_path"] = os.path.abspath(path)
        record["source_mtime"] = os.path.getmtime(path)
        if WATCH_MODE == "move":
            shutil.move(path, final)
        else:
            shutil.copy2(path, final)         # default: leave the original untouched
    else:
        shutil.move(path, final)

    with lock:
        data["screenshots"][sid] = record
        index_for_search(record)
        if source == "watch":
            _seen.add((record["source_path"], record["source_mtime"]))
        else:
            undo_stack.append(sid)
        if save:
            storage.save(data)
            state["version"] += 1
    return record


def delete(sid):
    """Move a screenshot to the trash bin. Nothing is removed yet; restore() brings it back."""
    with lock:
        s = data["screenshots"].get(sid)
        if not s or s.get("deleted_at"):
            return None
        s["deleted_at"] = datetime.now().isoformat(timespec="seconds")
        if sid in undo_stack:
            undo_stack.remove(sid)
        storage.save(data)
        state["version"] += 1
        return s


def restore(sid):
    """Take a screenshot out of the trash bin."""
    with lock:
        s = data["screenshots"].get(sid)
        if not s or not s.get("deleted_at"):
            return None
        s["deleted_at"] = None
        storage.save(data)
        state["version"] += 1
        return s


def purge(sid, save=True):
    """Permanently delete a screenshot that is in the trash: the organized copy and its record.
    The original file in the watched folder is not touched, and it is not imported again."""
    with lock:
        s = data["screenshots"].get(sid)
        if not s or not s.get("deleted_at"):
            return None
        del data["screenshots"][sid]
        if _hashes.get(s.get("file_hash")) == sid:
            del _hashes[s["file_hash"]]
        path = os.path.join(ORGANIZED, s["category"], s["saved"])
        if os.path.exists(path):
            os.remove(path)
        if s.get("source_path"):
            storage.add_deleted_source(s["source_path"], s["source_mtime"])
        for t in data["tasks"].values():              # keep its tasks, just unlink them
            if t.get("screenshot_id") == sid:
                t["screenshot_id"] = None
        if save:
            storage.save(data)                        # the Trie keeps a stale ID, which the pages filter out
            state["version"] += 1
        return s


def empty_trash():
    with lock:
        ids = [sid for sid, s in data["screenshots"].items() if s.get("deleted_at")]
        for sid in ids:
            purge(sid, save=False)
        storage.save(data)
        state["version"] += 1
        return len(ids)


def set_description(sid, text):
    with lock:
        s = data["screenshots"].get(sid)
        if not s:
            return None
        s["description"] = " ".join((text or "").split())[:500]
        trie.insert(s["description"], sid)            # descriptions are searchable too
        storage.save(data)
        return s


def undo_last():
    with lock:
        if not undo_stack:
            return None
        s = data["screenshots"].pop(undo_stack.pop(), None)
        if s:
            if _hashes.get(s.get("file_hash")) == s["id"]:
                del _hashes[s["file_hash"]]
            path = os.path.join(ORGANIZED, s["category"], s["saved"])
            if os.path.exists(path):
                os.remove(path)
            storage.save(data)    # the Trie keeps a stale ID, which the pages filter out
            state["version"] += 1
        return s