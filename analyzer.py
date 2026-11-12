"""Automatic screenshot analysis: category, deadline detection and priority suggestion."""
import re
from datetime import datetime, timedelta

import chat_detector
import organizer

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
MONTH_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
            r"nov(?:ember)?|dec(?:ember)?)")

ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
NUMERIC = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")            # dd/mm/yyyy
DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{MONTH_RE}\b\.?(?:,?\s+(\d{{4}}))?", re.I)
MONTH_DAY = re.compile(rf"\b{MONTH_RE}\b\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)

URGENT_WORDS = ["urgent", "asap", "due", "deadline", "overdue", "expires",
                "expiry", "exam", "submit", "submission", "tomorrow", "important"]
BASE_LEVEL = {"Study": 2, "Events": 2, "Payments": 2}   # everything else starts at Low (1)


def find_deadline(text, now=None):
    """Return the nearest future date (end of that day) found in the text, or None."""
    now = now or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    found = []

    def add(year, month, day):
        try:
            dt = datetime(year or now.year, month, day, 23, 59)
        except ValueError:
            return
        if today <= dt <= today + timedelta(days=365):
            found.append(dt)

    for m in ISO.finditer(text):
        add(int(m[1]), int(m[2]), int(m[3]))
    for m in NUMERIC.finditer(text):
        a, b = int(m[1]), int(m[2])
        day, month = (a, b) if b <= 12 else (b, a)   # prefer dd/mm, swap if impossible
        add(int(m[3]), month, day)
    for m in DAY_MONTH.finditer(text):
        add(int(m[3]) if m[3] else None, MONTHS[m[2][:3].lower()], int(m[1]))
    for m in MONTH_DAY.finditer(text):
        add(int(m[3]) if m[3] else None, MONTHS[m[1][:3].lower()], int(m[2]))
    return min(found) if found else None


def suggest_priority(category, haystack, deadline, now):
    reasons = []
    level = BASE_LEVEL.get(category, 1)
    hits = [w for w in URGENT_WORDS if re.search(rf"\b{re.escape(w)}\b", haystack)]
    if hits:
        level = max(level, 2)
        reasons.append("Keywords: " + ", ".join(hits[:3]))
    if len(hits) >= 2:
        level = 3
    if deadline:
        days = (deadline - now).days
        when = "today" if days == 0 else f"in {days} d"
        if days <= 3:
            level = 3
            reasons.append(f"Deadline {deadline:%d %b} ({when})")
        elif days <= 7:
            level = max(level, 2)
            reasons.append(f"Deadline {deadline:%d %b} ({when})")
        else:
            reasons.append(f"Date found: {deadline:%d %b %Y}")
    if not reasons:
        reasons.append(f"{category} default")
    return level, reasons


def analyze(path, filename, corrections=()):
    """OCR (if available) + filename + layout -> category, confidence, extracted text,
    priority, detected deadline, reasons."""
    now = datetime.now()
    text = organizer.extract_text(path)
    chat = chat_detector.detect(path, filename, text)
    # the chat detector is one more signal for the scorer (it no longer overrides everything)
    result = organizer.categorize(filename, text, path,
                                  chat["confidence"] if chat["is_chat"] else 0.0, corrections)
    category = result["category"]
    haystack = re.sub(r"[\W_]+", " ", (filename + " " + text).lower())
    deadline = find_deadline(text + " " + filename.replace("_", " "), now)
    priority, reasons = suggest_priority(category, haystack, deadline, now)
    if chat["is_chat"] and category == "Chat":
        reasons.insert(0, f"Chat detected ({chat['confidence']:.0%}): " + "; ".join(chat["reasons"]))
    reasons.insert(0, result["reason"])        # why this category was chosen
    return {"category": category, "confidence": result["confidence"], "text": text,
            "name": organizer.suggest_name(text, category, filename),   # None = keep the filename
            "priority": priority, "reasons": reasons,
            "detected_deadline": deadline.isoformat(timespec="minutes") if deadline else None}
