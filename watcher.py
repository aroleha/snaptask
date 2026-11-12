"""Automatic screenshot capture with watchdog.

Watches every folder in core.WATCH_DIRS (auto-detected OS screenshot folders + your own).
- New screenshots are picked up the moment they are saved (priority queue, handled first).
- At startup, screenshots that already exist on the computer are imported too (background,
  low priority), so the whole library gets organized, including ones taken while the app was off.
"""
import itertools, os, queue, threading, time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from werkzeug.utils import secure_filename

import core, locations

LIVE, BULK = 0, 1          # queue priorities: live screenshots jump ahead of the backfill


def wait_until_written(path, timeout=15):
    """The OS fires 'created' while the file is still being written; wait for a stable size."""
    last, end = -1, time.time() + timeout
    while time.time() < end:
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        if size > 0 and size == last:
            return True
        last = size
        time.sleep(0.5)
    return False


def is_candidate(path, dedicated):
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if name.startswith(".") or ext not in core.ALLOWED:
        return False
    if os.path.abspath(path).startswith((core.ORGANIZED, core.UPLOADS)):
        return False
    return dedicated or bool(locations.SHOT_NAME.search(name))


class ScreenshotHandler(FileSystemEventHandler):
    def __init__(self, dedicated):
        self.dedicated = dedicated

    def on_created(self, event):
        if not event.is_directory:
            worker.submit(event.src_path, self.dedicated, LIVE)

    def on_moved(self, event):            # many OSes save to a temp name, then rename
        if not event.is_directory:
            worker.submit(event.dest_path, self.dedicated, LIVE)

    def on_modified(self, event):         # some apps keep writing after 'created'
        if not event.is_directory:
            worker.submit(event.src_path, self.dedicated, LIVE)


class Worker:
    """One background thread processes a priority queue, so thousands of old files never
    start thousands of threads and never delay a freshly taken screenshot for long."""
    def __init__(self):
        self.q = queue.PriorityQueue()
        self.count = itertools.count()
        self.queued = set()
        self.guard = threading.Lock()
        self.since_save = 0
        threading.Thread(target=self.run, daemon=True).start()

    def submit(self, path, dedicated, prio):
        if not is_candidate(path, dedicated):
            return
        with self.guard:
            if path in self.queued:
                return
            self.queued.add(path)
            core.state["scanning"] += 1
        self.q.put((prio, next(self.count), path, dedicated))

    def run(self):
        while True:
            prio, _, path, dedicated = self.q.get()
            try:
                self.process(path, prio)
            except Exception as e:
                print(f"[watcher] could not process {os.path.basename(path)}: {e}")
            finally:
                with self.guard:
                    self.queued.discard(path)
                    core.state["scanning"] -= 1
                if prio == BULK:
                    self.since_save += 1
                    if self.since_save >= 20 or self.q.empty():
                        core.persist()
                        self.since_save = 0

    def process(self, path, prio):
        if not os.path.isfile(path):
            return
        old = time.time() - os.path.getmtime(path) > 10        # old files are already complete
        if (not old and not wait_until_written(path)) or core.already_ingested(path):
            return
        name = os.path.basename(path)
        clean = secure_filename(name) or f"screenshot{os.path.splitext(name)[1].lower()}"
        rec = core.ingest(path, clean, source="watch", save=(prio == LIVE))
        if rec is None:                                        # duplicate: already reported by core
            return
        print(f"[watcher] {name} -> {rec['category']} (priority {rec['priority']})")


worker = None


def scan_existing(folder, dedicated):
    """Walk a folder (recursively only if it is a dedicated screenshots folder)."""
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")] if dedicated else []
        for name in sorted(files):
            worker.submit(os.path.join(root, name), dedicated, BULK)


def start_watcher():
    global worker
    worker = Worker()
    observers = []
    for folder, dedicated in core.WATCH_DIRS.items():
        try:
            os.makedirs(folder, exist_ok=True)
            obs = Observer()
            obs.schedule(ScreenshotHandler(dedicated), folder, recursive=dedicated)
            obs.daemon = True
            obs.start()
            observers.append(obs)
            print(f"[watcher] watching {folder} ({'all images' if dedicated else 'screenshot names only'})")
        except Exception as e:
            print(f"[watcher] cannot watch {folder}: {e}")
    core.state["watching"] = bool(observers)
    if core.WATCH_SCAN_EXISTING:
        def backfill():
            for folder, dedicated in core.WATCH_DIRS.items():
                scan_existing(folder, dedicated)
        threading.Thread(target=backfill, daemon=True).start()
    return observers