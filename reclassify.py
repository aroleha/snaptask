"""Re-run the categorizer on screenshots already in the library.

Use it after installing Tesseract or after editing datasets/categories.json:
    python reclassify.py            # stop the app first
    python reclassify.py --all      # redo every screenshot, even ones already processed
It can be stopped with Ctrl+C and started again: it continues where it stopped.
Screenshots whose category you corrected by hand are left alone.
"""
import os, re, sys
from collections import Counter

import analyzer, core, organizer

CATEGORY_LINE = re.compile(r"^(%s)( \d+%%)?:" % "|".join(organizer.CATEGORIES))

if not organizer.OCR_READY:
    print("WARNING: Tesseract OCR not found. Only filenames will be used.")

REDO_ALL = "--all" in sys.argv
moved, counts, missing, skipped = 0, Counter(), 0, 0
shots = list(core.data["screenshots"].values())
try:
    for i, s in enumerate(shots, 1):
        path = os.path.join(core.ORGANIZED, s["category"], s["saved"])
        if s.get("category_manual"):
            counts[s["category"]] += 1
            continue
        if s.get("confidence") is not None and not REDO_ALL:     # done in an earlier run
            skipped += 1
            counts[s["category"]] += 1
            continue
        if not os.path.exists(path):
            missing += 1
            counts[s["category"]] += 1
            continue
        s["original_name"] = s.get("original_name") or s["name"]
        r = analyzer.analyze(path, s["original_name"], core.corrections())
        s["text"] = r["text"]
        s["name"] = r["name"] or s["original_name"]       # automatic name (the file on disk keeps its name)
        s["reasons"] = [x for x in s["reasons"] if not CATEGORY_LINE.match(x)]   # drop the old category line
        s["reasons"].insert(0, r["reasons"][0])
        moved += r["category"] != s["category"]
        print(f"[{i}/{len(shots)}] {s['original_name']} -> {r['category']} ({r['confidence']:.0%})  {s['name']}",
              flush=True)
        core.set_category(s["id"], r["category"], manual=False, confidence=r["confidence"], save=False)
        counts[r["category"]] += 1
        if i % 10 == 0:
            core.persist()                                # progress is saved every 10 screenshots
except KeyboardInterrupt:
    core.persist()                                    # keep what was done so far
    sys.exit("\nStopped. Progress is saved; run python reclassify.py again to continue.")
core.persist()
print(f"\nDone. {moved} moved, {skipped} already done earlier, {missing} files not found on disk.")
for c in organizer.CATEGORIES:
    print(f"  {c:<9} {counts[c]}")