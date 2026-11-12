import threading, time
from collections import deque
from datetime import datetime

try:
    from plyer import notification as desktop
except ImportError:
    desktop = None

# minutes before the deadline at which an alert is sent
THRESHOLDS = {3: [1440, 180, 30], 2: [180, 30], 1: [30]}
LABEL = {3: "HIGH", 2: "MEDIUM", 1: "LOW"}


class Notifier:
    def __init__(self, scheduler, interval=30):
        self.scheduler = scheduler
        self.interval = interval
        self.queue = deque()      # pending messages for the browser
        self.sent = set()         # (task_id, threshold) already alerted
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.check()
            except Exception as e:
                print("Notifier error:", e)
            time.sleep(self.interval)

    def check(self):
        now = datetime.now()
        for t in self.scheduler.upcoming():          # sorted by deadline
            mins = (datetime.fromisoformat(t["deadline"]) - now).total_seconds() / 60
            if mins > 1440:
                break                                # everything after this is later
            if mins < 0:
                self._alert(t, (t["id"], "overdue"), f"OVERDUE: {t['title']}")
                continue
            crossed = [m for m in THRESHOLDS[t["priority"]] if mins <= m]
            if crossed:
                key = (t["id"], min(crossed))
                if key not in self.sent:
                    self.sent.update((t["id"], m) for m in crossed)
                    msg = f"[{LABEL[t['priority']]}] {t['title']} is due in {int(mins)} min"
                    self._alert(t, key, msg, mark=False)

    def _alert(self, task, key, msg, mark=True):
        if mark:
            if key in self.sent:
                return
            self.sent.add(key)
        with self.lock:
            self.queue.append({"title": "Task reminder", "message": msg,
                               "priority": task["priority"]})
        if desktop:
            try:
                desktop.notify(title="Task reminder", message=msg, timeout=8)
            except Exception:
                pass

    def push(self, title, message, priority=1):
        """Show a one-off message in the browser (e.g. "duplicate skipped")."""
        with self.lock:
            self.queue.append({"title": title, "message": message, "priority": priority})

    def pop_all(self):
        with self.lock:
            items = list(self.queue)
            self.queue.clear()
        return items