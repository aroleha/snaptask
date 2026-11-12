import heapq, itertools, os, uuid
from datetime import datetime, timedelta
from flask import (Flask, render_template, request, redirect,
                   url_for, jsonify, send_from_directory)
from werkzeug.utils import secure_filename

import core, organizer, storage
from icons import icon
from core import data, scheduler, notifier, trie, lock

try:
    import watcher
except ImportError:         
    watcher = None

CATEGORY_META = {
    "Study":    {"icon": "📚", "color": "#6366f1"},
    "Events":   {"icon": "📅", "color": "#0ea5e9"},
    "Payments": {"icon": "💰", "color": "#eab308"},
    "Chat":     {"icon": "💬", "color": "#22c55e"},
    "Travel":   {"icon": "✈️", "color": "#14b8a6"},
    "Shopping": {"icon": "🛍️", "color": "#f97316"},
    "Notes":    {"icon": "📝", "color": "#a855f7"},
    "Memes":    {"icon": "🖼️", "color": "#ec4899"},
    "Others":   {"icon": "📁", "color": "#64748b"},
}
for _c, _m in CATEGORY_META.items():         # full names from datasets/categories.json
    _m["label"] = organizer.LABELS.get(_c, _c)
CATEGORY_ORDER = list(CATEGORY_META)
PRIORITY_NAME = {3: "High", 2: "Medium", 1: "Low"}

app = Flask(__name__)


def due_label(iso):
    if not iso:
        return ""
    mins = (datetime.fromisoformat(iso) - datetime.now()).total_seconds() / 60
    if mins < 0:
        return "Overdue"
    if mins < 60:
        return f"in {int(mins)} min"
    if mins < 1440:
        return f"in {int(mins // 60)} h"
    return f"in {int(mins // 1440)} d"


RECENT = "Recent"                             # a view, not a category: /?cat=Recent
RECENT_COUNT = 30
VIEW_META = {RECENT: {"icon": "🕒", "color": "#0f172a", "label": "Recent screenshots"}}


def taken_at(s):
    """When the screenshot was taken (file time for watched folders, upload time otherwise)."""
    return s.get("source_mtime") or datetime.fromisoformat(s["uploaded"]).timestamp()


app.jinja_env.globals.update(
    meta=lambda c: CATEGORY_META.get(c) or VIEW_META.get(c) or CATEGORY_META["Others"],
    RECENT=RECENT,
    PNAME=PRIORITY_NAME, due_label=due_label, icon=icon, CATEGORIES=CATEGORY_ORDER)


def snapshot(trash=False):
    """Copies of the screenshots in the library (default) or of those in the trash bin."""
    with lock:
        return {k: dict(v) for k, v in data["screenshots"].items()
                if bool(v.get("deleted_at")) == trash}


def linked_deadlines():
    """hash map: screenshot id -> earliest deadline of its active tasks"""
    out = {}
    with lock:
        for t in data["tasks"].values():
            sid = t.get("screenshot_id")
            if sid and not t["done"]:
                out[sid] = min(out.get(sid, t["deadline"]), t["deadline"])
    return out


def decorate(s, linked):
    dl = linked.get(s["id"]) or s.get("detected_deadline")
    overdue = bool(dl) and datetime.fromisoformat(dl) < datetime.now()
    return dict(s, deadline=dl, due=due_label(dl), overdue=overdue, has_task=s["id"] in linked)


def back(default):
    return redirect(request.referrer or url_for(default))   # stay on the page the button was on


@app.context_processor
def inject():
    high = sum(1 for s in snapshot().values()
               if s.get("priority") == 3 and not s.get("handled"))
    return dict(next_task=scheduler.next_task(), high_count=high,
                trash_count=len(snapshot(trash=True)),
                watching=core.state["watching"], watch_dir=core.WATCH_DIR,
                version=core.state["version"],
                now=datetime.now().isoformat(timespec="minutes"))


def library_stats(everything):
    week = datetime.now() + timedelta(days=7)
    return {
        "total": len(everything),
        "high": sum(1 for s in everything if s["priority"] == 3 and not s.get("handled")),
        "due": sum(1 for s in everything if s["deadline"] and not s.get("handled")
                   and datetime.fromisoformat(s["deadline"]) <= week),
        "auto": sum(1 for s in everything if s.get("source") == "watch"),
    }


# ---------- Home (opens first) ----------
@app.route("/")
def home():
    linked = linked_deadlines()
    everything = [decorate(s, linked) for s in snapshot().values()]
    counts = {c: 0 for c in CATEGORY_ORDER}
    for s in everything:
        counts[s["category"]] = counts.get(s["category"], 0) + 1
    recent = sorted(everything, key=taken_at, reverse=True)[:8]
    upcoming = scheduler.upcoming()
    return render_template(
        "home.html", stats=library_stats(everything), counts=counts,
        biggest=max(counts.values(), default=0) or 1, recent=recent,
        tasks=upcoming[:5], task_count=len(upcoming),
        described=sum(1 for s in everything if s.get("description")),
        ocr_ready=organizer.OCR_READY, watch_dirs=list(core.WATCH_DIRS),
        scanning=core.state["scanning"])


# ---------- Library (categories) ----------
@app.route("/library")
def library():
    q = request.args.get("q", "").strip()
    home = "cat" not in request.args and not q       # home = one container per category
    cat = request.args.get("cat", "All")
    linked = linked_deadlines()
    everything = [decorate(s, linked) for s in snapshot().values()]

    counts = {c: 0 for c in CATEGORY_ORDER}          # hash map: category -> count
    for s in everything:
        counts[s["category"]] = counts.get(s["category"], 0) + 1

    stats = library_stats(everything)

    if home:                                         # newest 4 screenshots of each category as a preview
        newest = sorted(everything, key=taken_at, reverse=True)
        previews = {"All": newest[:4], RECENT: newest[:4]}
        for s in newest:
            p = previews.setdefault(s["category"], [])
            if len(p) < 4:
                p.append(s)
        return render_template("library.html", home=True, previews=previews, groups=[], counts=counts,
                               total=len(everything), stats=stats, cat=cat, q=q,
                               recent_count=min(len(everything), RECENT_COUNT))

    visible = everything
    if q:
        # every word must match a filename word or a word of the screenshot's text (prefix match)
        ids = set.intersection(*(set(trie.search(w)) for w in organizer.normalize(q).split())) \
            if organizer.normalize(q) else set()
        visible = [s for s in visible if s["id"] in ids]
    if cat == RECENT:                                # newest screenshots, all categories together
        newest = sorted(visible, key=taken_at, reverse=True)[:RECENT_COUNT]
        return render_template("library.html", groups=[(RECENT, newest)] if newest else [],
                               counts=counts, total=len(everything), stats=stats, cat=cat, q=q)
    if cat != "All":
        visible = [s for s in visible if s["category"] == cat]

    groups = {}
    for s in sorted(visible, key=lambda s: s["uploaded"], reverse=True):
        groups.setdefault(s["category"], []).append(s)
    ordered = [(c, groups[c]) for c in CATEGORY_ORDER if c in groups]
    ordered += [(c, v) for c, v in groups.items() if c not in CATEGORY_ORDER]
    return render_template("library.html", groups=ordered, counts=counts,
                           total=len(everything), stats=stats, cat=cat, q=q)


@app.route("/recent")
def recent():
    return redirect(url_for("library", cat=RECENT))


@app.route("/shot/delete/<sid>", methods=["POST"])
def delete_shot(sid):
    core.delete(sid)                                 # moves it to the trash bin
    return back("library")


@app.route("/shot/describe/<sid>", methods=["POST"])
def describe_shot(sid):
    in_page = request.headers.get("X-Requested-With") == "fetch"   # saved without reloading the page
    try:
        s = core.set_description(sid, request.form.get("description", ""))
    except Exception as e:                           # e.g. "database is locked"
        if in_page:
            return jsonify(ok=False, error=str(e)), 500
        raise
    if in_page:
        if not s:
            return jsonify(ok=False, error="This screenshot no longer exists. Refresh the page."), 404
        return jsonify(ok=True, description=s["description"])
    return back("library")


# ---------- Trash bin ----------
@app.route("/trash")
def trash():
    shots = sorted(snapshot(trash=True).values(), key=lambda s: s["deleted_at"], reverse=True)
    return render_template("trash.html", shots=shots)


@app.route("/trash/restore/<sid>", methods=["POST"])
def trash_restore(sid):
    core.restore(sid)
    return back("trash")


@app.route("/trash/delete/<sid>", methods=["POST"])
def trash_delete(sid):
    core.purge(sid)                                  # permanent
    return back("trash")


@app.route("/trash/empty", methods=["POST"])
def trash_empty():
    core.empty_trash()
    return back("trash")


@app.route("/upload", methods=["POST"])
def upload():
    for f in request.files.getlist("files"):
        name = secure_filename(f.filename or "")
        if not name or os.path.splitext(name)[1].lower() not in core.ALLOWED:
            continue
        tmp = os.path.join(core.UPLOADS, f"{uuid.uuid4().hex[:8]}_{name}")
        f.save(tmp)
        core.ingest(tmp, name, source="upload")
    return back("library")


@app.route("/undo", methods=["POST"])
def undo():
    core.undo_last()
    return back("library")


# ---------- Priority board (separate page) ----------
@app.route("/priority")
def priority():
    linked = linked_deadlines()
    heap, seq = [], itertools.count()
    shots = snapshot()
    for s in shots.values():
        if s.get("handled"):
            continue
        d = decorate(s, linked)
        ts = datetime.fromisoformat(d["deadline"]).timestamp() if d["deadline"] else float("inf")
        # min-heap: highest priority first, then nearest deadline
        heapq.heappush(heap, (-s["priority"], ts, next(seq), d))
    ranked = []
    while heap:
        ranked.append(heapq.heappop(heap)[3])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    levels = [(lvl, PRIORITY_NAME[lvl], [r for r in ranked if r["priority"] == lvl])
              for lvl in (3, 2, 1)]
    handled = sorted((decorate(s, linked) for s in shots.values() if s.get("handled")),
                     key=lambda s: s["uploaded"], reverse=True)[:12]
    return render_template("priority.html", levels=levels, handled=handled, total=len(ranked))


@app.route("/shot/priority/<sid>", methods=["POST"])
def set_priority(sid):
    level = request.form.get("level", type=int)
    with lock:
        s = data["screenshots"].get(sid)
        if s and level in (1, 2, 3):
            s["priority"], s["manual"] = level, True
            storage.save(data)
    return back("priority")


@app.route("/shot/category/<sid>", methods=["POST"])
def set_category(sid):
    """Manual correction: moves the file and is remembered for similar screenshots."""
    core.set_category(sid, request.form.get("category", ""))
    return back("library")


@app.route("/shot/done/<sid>", methods=["POST"])
def shot_done(sid):
    with lock:
        s = data["screenshots"].get(sid)
        if s:
            s["handled"] = not s.get("handled", False)
            storage.save(data)
    return back("priority")


@app.route("/shot/task/<sid>", methods=["POST"])
def shot_task(sid):
    """One-click: turn the deadline found in a screenshot into a tracked task."""
    with lock:
        s = data["screenshots"].get(sid)
        if s and s.get("detected_deadline") and sid not in linked_deadlines():
            scheduler.add(f"Review: {s['name']}", s["detected_deadline"], s["priority"], sid)
            storage.save(data)
    return back("priority")


# ---------- Tasks ----------
@app.route("/tasks")
def tasks():
    now = datetime.now()
    end_today = now.replace(hour=23, minute=59, second=59)
    buckets = {"Overdue": [], "Today": [], "This week": [], "Later": []}
    for t in scheduler.upcoming():                   # already heap-ordered
        d = datetime.fromisoformat(t["deadline"])
        if d < now:
            key = "Overdue"
        elif d <= end_today:
            key = "Today"
        elif d <= now + timedelta(days=7):
            key = "This week"
        else:
            key = "Later"
        buckets[key].append(t)
    groups = [(k, v) for k, v in buckets.items() if v]
    return render_template("tasks.html", groups=groups,
                           count=sum(len(v) for v in buckets.values()), all_shots=snapshot())


@app.route("/task/add", methods=["POST"])
def add_task():
    title = request.form["title"].strip()
    deadline = request.form["deadline"]   # from datetime-local input
    if title and deadline:
        with lock:
            scheduler.add(title, deadline, request.form.get("priority", 2),
                          request.form.get("screenshot_id") or None)
            storage.save(data)
    return back("tasks")


@app.route("/task/done/<tid>", methods=["POST"])
def done_task(tid):
    with lock:
        scheduler.complete(tid)
        storage.save(data)
    return back("tasks")


# ---------- API / files ----------
@app.route("/api/notifications")
def api_notifications():
    return jsonify(notifier.pop_all())


@app.route("/api/version")
def api_version():
    return jsonify({"version": core.state["version"]})


@app.route("/files/<category>/<path:filename>")
def files(category, filename):
    return send_from_directory(os.path.join(core.ORGANIZED, category), filename)


if __name__ == "__main__":
    if organizer.OCR_READY:
        print("[ocr] Tesseract found: screenshots are categorized by their text")
    else:
        print("[ocr] WARNING: Tesseract OCR not found. Only filenames are used, so most screenshots\n"
              "      will land in Others. Install it (see README) or set TESSERACT_CMD to tesseract.exe")
    notifier.start()
    if watcher:
        watcher.start_watcher()
        print(f"[watcher] watching {core.WATCH_DIR} (mode: {core.WATCH_MODE})")
    else:
        print("[watcher] watchdog not installed: automatic watching disabled (pip install watchdog)")
    app.run(debug=True, use_reloader=False)   # reloader off so only one watcher/notifier runs