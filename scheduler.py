import heapq, itertools, uuid
from datetime import datetime


class TaskScheduler:
    def __init__(self, tasks):
        self.tasks = tasks                 # hash map: id -> task
        self.heap = []                     # min-heap of (deadline_ts, -priority, seq, id)
        self.seq = itertools.count()       # tie-breaker so tuples never compare ids
        for t in tasks.values():
            if not t["done"]:
                self._push(t)

    def _push(self, t):
        ts = datetime.fromisoformat(t["deadline"]).timestamp()
        heapq.heappush(self.heap, (ts, -t["priority"], next(self.seq), t["id"]))

    def add(self, title, deadline, priority, screenshot_id=None):
        tid = uuid.uuid4().hex[:8]
        task = {"id": tid, "title": title, "deadline": deadline,
                "priority": int(priority), "screenshot_id": screenshot_id,
                "done": False}
        self.tasks[tid] = task
        self._push(task)
        return task

    def complete(self, tid):
        if tid in self.tasks:
            # lazy deletion: removed from the heap when it reaches the top
            self.tasks[tid]["done"] = True

    def next_task(self):
        while self.heap and self.tasks[self.heap[0][3]]["done"]:
            heapq.heappop(self.heap)
        return self.tasks[self.heap[0][3]] if self.heap else None

    def upcoming(self):
        """Active tasks, most urgent first."""
        return [self.tasks[i[3]] for i in sorted(self.heap)
                if not self.tasks[i[3]]["done"]]
