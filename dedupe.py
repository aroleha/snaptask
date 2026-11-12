"""Move duplicate screenshots that are already in the library to the Trash.

    python dedupe.py            # stop the app first
Duplicates = identical image files (same SHA-256). Of each group one is kept: the one you
corrected / described by hand, otherwise the oldest. The rest go to the Trash, so you can
still Restore them there.
"""
from collections import defaultdict

import core

groups = defaultdict(list)
for s in core.data["screenshots"].values():
    if not s.get("deleted_at") and s.get("file_hash"):
        groups[s["file_hash"]].append(s)

moved = 0
for items in groups.values():
    if len(items) < 2:
        continue
    items.sort(key=lambda s: (not (s.get("category_manual") or s.get("description") or s.get("manual")),
                              s["uploaded"]))
    keep, extra = items[0], items[1:]
    print(f"keep {keep['name']}  ({len(extra)} duplicate(s))")
    for s in extra:
        core.delete(s["id"])
        moved += 1
print(f"\nDone. {moved} duplicate(s) moved to the Trash.")