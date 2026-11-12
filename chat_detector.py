"""Chat-screenshot recognizer.

Two independent signals are combined:
  1. TEXT   : OCR text + filename scored against datasets/chat_signals.json
              (app names, chat UI phrases, timestamps, "Today" separators, slang).
  2. LAYOUT : message bubbles. Chat screenshots show several coloured blocks, stacked
              vertically, hugging the left (received) and right (sent) edge.
              Works without OCR, so it still helps if Tesseract is not installed.

Test one image:   python chat_detector.py path/to/image.png
Tune it by editing datasets/chat_signals.json (no code changes needed).
"""
import json, os, re, sys
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
SIGNALS_FILE = os.environ.get("CHAT_SIGNALS", os.path.join(BASE, "datasets", "chat_signals.json"))


def _word(p):
    return re.compile(rf"(?<![a-z0-9]){re.escape(p.lower())}(?![a-z0-9])")


def _load():
    with open(SIGNALS_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["_phrases"] = [(_word(p), w, p) for grp in ("apps", "ui_phrases") for p, w in cfg[grp].items()]
    cfg["_regex"] = [(re.compile(r["pattern"], re.I | re.M), r["weight"], r["max"], r["name"])
                     for r in cfg["regex"]]
    cfg["_files"] = [(_word(p), w, p) for p, w in cfg["filename"].items()]
    return cfg


CFG = _load()


def text_signals(text, filename):
    """Return (points, reasons) from OCR text and the filename."""
    hay, total, reasons = (text or "").lower(), 0.0, []
    hits = []
    for rx, w, label in CFG["_phrases"]:
        if rx.search(hay):
            total += w
            hits.append((w, label))
    for rx, w, cap, name in CFG["_regex"]:
        n = len(rx.findall(hay))
        if n:
            total += min(w * n, cap)
            hits.append((min(w * n, cap), f"{n}x {name}"))
    fname = re.sub(r"[\W_]+", " ", filename.lower())
    for rx, w, label in CFG["_files"]:
        if rx.search(fname):
            total += w
            hits.append((w, f"filename '{label}'"))
    if hits:
        best = [label for _, label in sorted(hits, reverse=True)[:3]]
        reasons.append("text: " + ", ".join(best))
    return total, reasons


def layout_score(path):
    """Detect alternating left/right message bubbles. Returns (score 0..1, info)."""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
    except Exception:
        return 0.0, {}
    W0, H0 = img.size
    if W0 < 200 or H0 < 200:
        return 0.0, {}
    w = 160
    h = min(600, max(1, round(H0 * w / W0)))
    box = Image.Resampling.BOX if hasattr(Image, "Resampling") else Image.BOX
    img = img.resize((w, h), box)                       # BOX blur melts text into the bubble colour
    px = [(r >> 4, g >> 4, b >> 4) for r, g, b in img.getdata()]

    counts = Counter(px)
    ranked = counts.most_common(7)
    tops = {c for c, n in ranked[1:] if n >= 0.015 * w * h}      # [0] is the background
    if not tops:
        return 0.0, {}

    stats = {c: [[0, w, -1] for _ in range(h)] for c in tops}
    for y in range(h):
        base = y * w
        for x in range(w):
            c = px[base + x]
            if c in tops:
                s = stats[c][y]
                s[0] += 1
                if x < s[1]: s[1] = x
                if x > s[2]: s[2] = x

    left = right = 0
    min_row = 0.05 * w
    for c in tops:
        rows, y = stats[c], 0
        while y < h:
            if rows[y][0] < min_row:
                y += 1
                continue
            first = last = y                              # grow a group, tolerate 2 empty rows
            xmin, xmax, gap = rows[y][1], rows[y][2], 0
            y += 1
            while y < h and gap <= 2:
                if rows[y][0] >= min_row:
                    last, gap = y, 0
                    xmin, xmax = min(xmin, rows[y][1]), max(xmax, rows[y][2])
                else:
                    gap += 1
                y += 1
            height, frac = last - first + 1, (xmax - xmin + 1) / w
            if height < 4 or height > 0.4 * h or frac < 0.12 or frac > 0.88:
                continue                                  # too small, a big card, or a full-width bar
            if xmin <= 0.15 * w and xmax < 0.88 * w:
                left += 1
            elif xmax >= 0.85 * w and xmin > 0.12 * w:
                right += 1

    n = left + right
    score = min(1.0, n / 6)
    if not (left and right):
        score *= 0.6                                      # one-sided: weaker evidence
    if H0 / W0 < 1.2:
        score *= 0.7                                      # landscape: desktop apps look different
    return score, {"left": left, "right": right}


def detect(path, filename, text=""):
    pts, reasons = text_signals(text, filename)
    t_score = min(1.0, pts / CFG["text_full_score"])
    l_score, info = layout_score(path)
    if l_score >= 0.3:
        reasons.append(f"layout: {info['left']} left / {info['right']} right bubbles")
    conf = 1 - (1 - t_score) * (1 - l_score)             # either signal can carry it, both reinforce
    return {"is_chat": conf >= CFG["threshold"], "confidence": round(conf, 2),
            "text_score": round(t_score, 2), "layout_score": round(l_score, 2), "reasons": reasons}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python chat_detector.py image.png")
    import organizer
    p = sys.argv[1]
    print(detect(p, os.path.basename(p), organizer.extract_text(p)))
