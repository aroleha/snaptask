"""Screenshot categorizer.

Pipeline:  OCR  ->  normalize text  ->  weighted keyword score per category
           (+ optional extra signals: chat layout, photo-like image, long text)
           ->  confidence  ->  category, or "Others" if confidence is below the threshold.

All keywords, weights and thresholds live in datasets/categories.json.
Test one image:   python organizer.py path/to/image.png
"""
import json, os, re, shutil, sys

BASE = os.path.dirname(os.path.abspath(__file__))
DATASET_FILE = os.environ.get("CATEGORY_DATASET", os.path.join(BASE, "datasets", "categories.json"))
FALLBACK = "Others"
MAX_TEXT = 5000                      # characters of OCR text kept per screenshot


def normalize(text):
    """Lowercase and replace everything that is not a letter or digit with one space."""
    text = (text or "").lower().replace("₹", " rs ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _load():
    with open(DATASET_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    s = cfg["settings"]
    rules = {}                       # category -> [(normalized keyword, weight), ...]
    for name, c in cfg["categories"].items():
        if name == FALLBACK:
            continue
        words = [(normalize(k), s["strong_weight"]) for k in c.get("strong", [])]
        words += [(normalize(k), s["normal_weight"]) for k in c.get("normal", [])]
        rules[name] = [(k, w) for k, w in words if k]
    return cfg, s, rules


CFG, SETTINGS, RULES = _load()
CATEGORIES = list(RULES) + [FALLBACK]
LABELS = {name: c.get("label", name) for name, c in CFG["categories"].items()}


# ---------- OCR ----------
def _find_tesseract():
    """True if OCR can run. On Windows, Tesseract is often installed but not on PATH."""
    try:
        import pytesseract
    except ImportError:
        return False
    candidates = [os.environ.get("TESSERACT_CMD")]
    if os.name == "nt":
        candidates += [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                       r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                       os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")]
    for c in candidates:
        if c and os.path.isfile(c):
            pytesseract.pytesseract.tesseract_cmd = c
            break
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


OCR_READY = _find_tesseract()


def extract_text(path):
    """OCR one image. Returns "" if Tesseract is not installed or the image cannot be read."""
    if not OCR_READY:
        return ""
    try:
        import pytesseract
        from PIL import Image, ImageOps, ImageStat
        img = Image.open(path).convert("L")
        if ImageStat.Stat(img).mean[0] < 110:          # dark mode: Tesseract wants dark text on light
            img = ImageOps.invert(img)
        if img.width < 1000:                           # small text reads better when enlarged
            img = img.resize((img.width * 2, img.height * 2))
        return pytesseract.image_to_string(img, timeout=60).strip()[:MAX_TEXT]   # never hang on one image
    except Exception:
        return ""


# ---------- second signal: does the image look like a photo / meme? ----------
def photo_score(path):
    """0..1. App screenshots are mostly a few flat colours; photos and memes are not."""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB").resize((96, 96))
    except Exception:
        return 0.0
    counts = {}
    raw = img.tobytes()                                # r, g, b, r, g, b, ...
    for i in range(0, len(raw), 3):
        key = (raw[i] >> 4, raw[i + 1] >> 4, raw[i + 2] >> 4)
        counts[key] = counts.get(key, 0) + 1
    top3 = sum(sorted(counts.values(), reverse=True)[:3]) / (96 * 96)   # share of flat colour
    variety = min(1.0, len(counts) / 300)
    return round(max(0.0, 1 - top3 / 0.5) * variety, 2)


# ---------- scoring ----------
def keyword_scores(norm_text):
    """Return {category: (points, [matched keywords, best first])}."""
    hay = f" {norm_text} "
    cap = SETTINGS["max_hits_per_keyword"]
    out = {}
    for category, words in RULES.items():
        points, hits = 0, []
        for kw, weight in words:
            n = hay.count(f" {kw} ")
            if n:
                points += weight * min(n, cap)
                hits.append((weight, kw))
        out[category] = (points, [kw for _, kw in sorted(hits, reverse=True)])
    return out


def _similar_correction(words, corrections):
    """Reuse a manual correction if a corrected screenshot has nearly the same text."""
    if len(words) < 5:
        return None, 0.0
    best, best_sim = None, 0.0
    for other_words, category in corrections:
        union = len(words | other_words)
        sim = len(words & other_words) / union if union else 0.0
        if sim > best_sim:
            best, best_sim = category, sim
    if best_sim >= SETTINGS["correction_similarity"]:
        return best, best_sim
    return None, 0.0


def categorize(filename, text="", image_path=None, chat_confidence=0.0, corrections=()):
    """Return {"category", "confidence" (0..1), "reason", "scores"}.

    chat_confidence: result of chat_detector (0 if it did not detect a chat).
    corrections:     [(set of words, category), ...] from screenshots the user re-categorized.
    """
    norm_text = normalize(text)
    words = norm_text.split()
    name = normalize(os.path.splitext(filename)[0])

    category, sim = _similar_correction(set(words), corrections)
    if category in CATEGORIES:
        return {"category": category, "confidence": round(sim, 2), "scores": {},
                "reason": f"{category} {sim:.0%}: similar to a screenshot you corrected"}

    scored = keyword_scores(f"{name} {norm_text}")
    points = {c: p for c, (p, _) in scored.items()}
    notes = {c: ", ".join(hits[:4]) for c, (_, hits) in scored.items() if hits}

    # photo / meme signal: only trusted when there is almost no text to go on
    photo = photo_score(image_path) if image_path and len(words) <= SETTINGS["few_words"] else 0.0
    if photo >= 0.3 and "Memes" in points:
        points["Memes"] += SETTINGS["photo_points"] * photo
        notes["Memes"] = (notes.get("Memes", "") + ", photo-like image with little text").strip(", ")
    # chat bubbles signal (ignored for photos: the bubble detector can misfire on them)
    if chat_confidence and photo < 0.3 and "Chat" in points:
        points["Chat"] += SETTINGS["chat_points"] * chat_confidence
        notes["Chat"] = (notes.get("Chat", "") + ", chat layout").strip(", ")
    if len(words) >= SETTINGS["long_text_words"] and "Notes" in points:
        points["Notes"] += SETTINGS["long_text_points"]
        notes["Notes"] = (notes.get("Notes", "") + ", long text").strip(", ")

    ranked = sorted(points.items(), key=lambda kv: kv[1], reverse=True)
    (best, top), second = ranked[0], ranked[1][1]
    if top <= 0:
        return {"category": FALLBACK, "confidence": 0.0, "scores": points,
                "reason": "Others: no category keywords found"
                          + ("" if OCR_READY else " (OCR is off, only the filename was used)")}

    # strength: enough evidence?   share: clearly ahead of the runner-up?
    confidence = round(min(1.0, top / SETTINGS["full_score"]) * top / (top + second), 2)
    if confidence < SETTINGS["threshold"]:
        return {"category": FALLBACK, "confidence": confidence, "scores": points,
                "reason": f"Others: best guess {best} only {confidence:.0%} ({notes.get(best, '')})"}
    return {"category": best, "confidence": confidence, "scores": points,
            "reason": f"{best} {confidence:.0%}: {notes.get(best, '')}"}


# ---------- automatic name ----------
NAME_JUNK = {"lte", "volte", "wifi", "5g", "4g", "http", "https", "www", "com"}


def suggest_name(text, category, filename):
    """Build a readable name from the words in the screenshot, e.g.
    "Payments - Payment Successful Paid to Murugan Tea Stall.png".
    Returns None when there is no usable text (the original filename is kept)."""
    if not SETTINGS.get("auto_name", True):
        return None
    if len((text or "").split()) < SETTINGS.get("name_min_words", 6):
        return None                                   # too little text to trust (often OCR noise)
    keywords = [kw for kw, _ in RULES.get(category, [])]
    candidates = []                                   # (score, position, words)
    for raw in (text or "").splitlines():
        words = [w for w in re.sub(r"[^A-Za-z0-9 ]+", " ", raw).split()
                 if w.lower() not in NAME_JUNK]
        real = [w for w in words if len(w) >= 3 and w.isalpha()]
        letters = sum(c.isalpha() for c in raw)
        if len(real) < len(words) / 2 or len(words) > 10 or letters < 0.6 * len(raw.replace(" ", "")):
            continue                                  # numbers, clock, symbols, OCR noise, paragraphs
        pos = len(candidates)
        hay = f" {normalize(raw)} "
        score = (1 if any(f" {kw} " in hay for kw in keywords) else 0) \
            + (1 if 2 <= len(words) <= 6 else 0) - 0.3 * pos    # headings come first on a screen
        candidates.append((score, pos, words))
        if len(candidates) == 12:
            break
    if not candidates:
        return None
    best = sorted(sorted(candidates, reverse=True)[:2], key=lambda c: c[1])   # top 2, reading order
    words = [w for _, _, ws in best for w in ws][:SETTINGS.get("name_max_words", 7)]
    title = " ".join(words)[:60].strip()
    ext = os.path.splitext(filename)[1].lower()
    return f"{category} - {title}{ext}" if category != FALLBACK else f"{title}{ext}"


def classify(filename, text=""):
    """Kept for older code: category name only."""
    return categorize(filename, text)["category"]


def organize(tmp_path, saved_name, original_name, base_dir):
    text = extract_text(tmp_path)
    category = categorize(original_name, text, tmp_path)["category"]
    folder = os.path.join(base_dir, category)
    os.makedirs(folder, exist_ok=True)
    shutil.move(tmp_path, os.path.join(folder, saved_name))
    return category


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python organizer.py image.png")
    p = sys.argv[1]
    t = extract_text(p)
    print("OCR ready:", OCR_READY, "| words:", len(normalize(t).split()), "| photo score:", photo_score(p))
    r = categorize(os.path.basename(p), t, p)
    print(r)
    print("suggested name:", suggest_name(t, r["category"], os.path.basename(p)))