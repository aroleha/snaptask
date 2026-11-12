"""Quick check of the keyword dataset: classify every sample snippet in datasets/categories.json.

Run:  python test_classifier.py
Add your own snippets to the "samples" lists to test new keywords.
"""
import organizer

ok = total = 0
for expected, cfg in organizer.CFG["categories"].items():
    for sample in cfg.get("samples", []):
        r = organizer.categorize("Screenshot_2026-01-01_120000.png", sample)
        total += 1
        good = r["category"] == expected
        ok += good
        print(f"{'PASS' if good else 'FAIL'}  expected {expected:<9} got {r['category']:<9} "
              f"{r['confidence']:>4.0%}  {sample[:60]}")
        if not good:
            print("      ", r["reason"])
print(f"\n{ok}/{total} samples classified correctly")
for name, words in organizer.RULES.items():
    print(f"  {name:<9} {len(words)} keywords")
raise SystemExit(0 if ok == total else 1)
