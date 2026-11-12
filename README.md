# Screenshot Organizer — Build Guide (v2: watchdog + categories + priority page)

## What's new
- **Automatic folder watching (watchdog):** save a screenshot into the watched folder and it is
  analysed and filed with no clicks.
- **Automatic analysis (`analyzer.py`):** category (OCR text + filename), deadline detection
  ("25 Oct", "2026-10-25", "25/10/2026"…) and a suggested priority with the reasons shown.
- **Library page:** category cards with counts, and collapsible sections grouped by category.
- **Priority page:** every screenshot ranked High -> Medium -> Low, then by nearest deadline
  (built with a min-heap). Change priority with one click, create a task from a detected
  deadline, or mark a screenshot as handled.
- **Tasks page:** the original heap-ordered deadline list and notifications.

## Folder structure
```
screenshot_organizer/
├── app.py          # Flask routes (Library, Priority, Tasks, API)
├── core.py         # shared state + ingest() used by the web app AND the watcher   (new)
├── analyzer.py     # category, date detection, priority suggestion                 (new)
├── watcher.py      # watchdog observer                                             (new)
├── icons.py        # inline SVG icons for the templates                            (new)
├── organizer.py    # OCR + dataset-driven categorizer (categories.json)
├── datasets/       # categories.json (keywords), chat_signals.json
├── reclassify.py, test_classifier.py   # re-sort the library / test the dataset
├── scheduler.py    # min-heap tasks (unchanged)
├── storage.py      # SQLite persistence (screenshots.db), replaces data.json   (updated)
├── trie.py, notifier.py   (unchanged)
├── requirements.txt
├── watch/          # default watched folder (auto-created)
├── organized/      # Study, Events, Payments, Chat, Travel, Shopping, Notes, Memes, Others
├── templates/ base.html, library.html, priority.html, tasks.html
└── static/style.css
```
`templates/index.html` from v1 is no longer used. Delete it.

## Pages
- **Home** (`/`): opens first. Totals, quick actions, screenshots per category, upcoming tasks,
  app status (auto-organizing, OCR, trash) and the newest screenshots.
- **Library** (`/library`): one container per category; click one to see its screenshots.
- **Priority**, **Tasks**, **Trash**: as described below.

## Categorization (v4: dataset + OCR + confidence)
Categories: **Study** (Assignments/Study), **Events** (Events/Meetings), **Payments**
(Payments/Receipts), **Chat** (Chats/Social), **Travel** (Travel/Tickets), **Shopping**
(Shopping/Orders), **Notes** (Notes/Documents), **Memes** (Memes/Images), **Others** (fallback).

How one screenshot is categorized (`organizer.categorize`):
1. **OCR** with Tesseract (dark-mode screenshots are inverted, small ones enlarged).
2. **Normalize**: lowercase, punctuation removed (`Order ID:` matches `order id`).
3. **Score** every category from `datasets/categories.json`: a *strong* keyword is worth 3 points,
   a *normal* keyword 1 point (the filename counts too).
4. **Extra signals** add points: chat bubbles (`chat_detector.py`) -> Chat, a photo-like image
   with almost no text -> Memes, a very long text -> Notes.
5. **Confidence** = (points of the winner / 6, max 1) x (winner's share against the runner-up).
   Below the threshold (0.3) the screenshot goes to **Others** instead of a wrong category.

Each screenshot stores `category`, `confidence` and the extracted `text` (SQLite columns are added
to an existing database automatically). The search box now matches the text inside screenshots,
not just filenames; several words must all match.

**Automatic names:** each screenshot is named from its own text, for example
`Payments - Payment Successful Paid to Murugan Tea Stall.png`. The name is built from the two
most title-like lines near the top (short lines, and lines containing the category's keywords,
score higher; clock, numbers and OCR noise are skipped). The original filename is kept in
`original_name` and still works in search. Screenshots with fewer than 6 readable words keep their
filename. Turn it off with `"auto_name": false` in `datasets/categories.json`.

**Delete and Trash:** deleting a screenshot moves it to the **Trash** page (sidebar). There you
can **Restore** it, **Delete forever** (removes its copy in `organized/` and its record), or
**Empty trash**. The original in your watched folder is never touched and is not imported again.
Linked tasks are kept. **Description:** every Library card has an "Add description" line; the
text is saved with the screenshot and is searchable. **Recent:** `/recent` (or `/?cat=Recent`) shows the 30
newest screenshots across all categories, newest first. Snippets for the Delete button and the
Recent link are in `TEMPLATE_SNIPPETS.md`.

**OCR is required for good results.** Without Tesseract only filenames are used and nearly
everything lands in Others (the app prints an `[ocr] WARNING` at startup).
- Windows: install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki, then
  `pip install pytesseract`. The default install folder is found automatically; otherwise
  `set TESSERACT_CMD=C:\path\to\tesseract.exe`.
- Mac: `brew install tesseract`  |  Linux: `sudo apt install tesseract-ocr`

Tuning and tools:
- Add or change keywords, weights and the threshold in `datasets/categories.json` only.
- `python test_classifier.py` checks the sample snippets in that file.
- `python organizer.py image.png` shows the OCR text size, scores and result for one image.
- `python reclassify.py` (app stopped) re-sorts the screenshots already in the library, for
  example after installing Tesseract. Manually corrected screenshots are not touched.
- Manual correction: `POST /shot/category/<id>` with form field `category`. The file is moved,
  the screenshot is marked as corrected, and new screenshots with nearly the same text get
  the same category. See `TEMPLATE_SNIPPETS.md` for the dropdown to paste into a template.

## Interface (v3 redesign)
- **Layout:** dark sidebar navigation (turns into a bottom tab bar on phones), page headers, and a
  next-deadline banner on every page.
- **Colour has two jobs:** category colour (violet Study, blue Work, green Chat, orange Shopping,
  yellow Finance, grey Others) and priority colour (red High, amber Medium, green Low).
- **Library:** summary strip, drag-and-drop anywhere on the page, category chips with counts,
  a quick High/Medium/Low filter, collapsible category sections, and click-to-preview (lightbox).
- **Priority:** ranked High -> Medium -> Low with jump links, reasons for each suggestion,
  one-click priority change, Create task and Mark handled.
- **Tasks:** labelled form plus tasks grouped into Overdue / Today / This week / Later.
- New file `icons.py` provides the inline SVG icons used by the templates.
- The page font (Plus Jakarta Sans) loads from Google Fonts. Offline, the system font is used.

## Database (SQLite)
Data is stored in `screenshots.db` (created automatically, built into Python, nothing to install).
- Tables: `screenshots` and `tasks`, with indexes on category and (done, deadline).
- On startup the data is loaded into memory, where the heap, hash map and Trie are rebuilt.
  Every change is saved back to SQLite in one transaction.
- If an old `data.json` exists, it is imported once and renamed to `data.json.bak`.
- Use another location with `SCREENSHOT_DB=/path/to/file.db`.
- Inspect it with the `sqlite3 screenshots.db` shell or "DB Browser for SQLite".
- Back up by copying `screenshots.db`.

## Setup and run
```
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # Mac/Linux
pip install flask pillow watchdog
pip install pytesseract plyer  # optional (OCR needs the Tesseract program installed too)
python app.py
```
Open http://127.0.0.1:5000. The top bar shows **👀 Watching folder** when watchdog is running.

## Point the watcher at your real screenshot folder
By default it watches `./watch`. To watch your OS screenshot folder, set an environment
variable **before** `python app.py`:

| OS | Command |
|---|---|
| Windows (cmd) | `set SCREENSHOT_WATCH_DIR=C:\Users\YOU\Pictures\Screenshots` |
| Windows (PowerShell) | `$env:SCREENSHOT_WATCH_DIR="C:\Users\YOU\Pictures\Screenshots"` |
| Mac | `export SCREENSHOT_WATCH_DIR=~/Desktop` |
| Linux | `export SCREENSHOT_WATCH_DIR=~/Pictures` |

Optional settings:
- `WATCH_MODE=copy` (default) leaves your original file in place. `WATCH_MODE=move` moves it.
- `WATCH_SCAN_EXISTING=1` also imports images already in the folder at startup.
  (Off by default so a big folder isn't imported by surprise.)

## How a screenshot flows through the app
1. watchdog fires `created` / `moved` for a new image (temp files, hidden files and non-images are ignored).
2. `watcher.py` waits until the file size stops changing (the file is fully written).
3. `core.ingest()` calls `analyzer.analyze()`: OCR + filename give the category, a date regex
   finds a deadline, and keywords + deadline distance give a priority.
4. The file is copied into `organized/<Category>/`, the record goes into the hash map,
   the Trie and the undo stack, and `data.json` is saved.
5. Open pages show a **Refresh** toast within ~5 seconds.

## Priority rules (automatic suggestion)
| Signal | Effect |
|---|---|
| Category Study / Events / Payments | starts at Medium |
| Other categories | start at Low |
| 1 keyword (urgent, asap, due, deadline, overdue, exam, submit, tomorrow, important…) | at least Medium |
| 2 or more keywords | High |
| Date found within 3 days | High |
| Date found within 7 days | at least Medium |

You can always override it on the Priority page. A manual choice is marked "Set manually".

## Build order (if typing it yourself)
1. `storage.py`, `trie.py`, `organizer.py`, `scheduler.py`, `notifier.py` (as in v1)
2. `analyzer.py`: test `find_deadline("due 25 Oct")` and `analyze(path, "urgent_exam.png")`
3. `core.py`: shared state and `ingest()`
4. `watcher.py`: run it alone and drop an image into `watch/` to see the `[watcher]` log line
5. `app.py`, then `base.html`, `library.html`, `priority.html`, `tasks.html`, `style.css`

## Test checklist
| Test | Expected |
|---|---|
| Save `whatsapp_chat.png` into `watch/` | Appears in Chat within seconds, with a "👀 auto" badge |
| Save `urgent_assignment_due_<date in 2 days>.png` | Study, High, deadline detected, listed first on Priority |
| Save `IMG_001.png` | Others, Low |
| Save a `.txt` file | Ignored |
| Priority page | Ordered High -> Medium -> Low, then nearest deadline |
| Click Med / Low / High | Priority changes, "Set manually" shown |
| ➕ Create task | Task appears on Tasks page, button becomes "✔ Task linked" |
| ✔ Handled | Leaves the board, can be restored |
| Upload through the web form | Same analysis as the watcher |
| Undo last upload | File and record removed |

## Troubleshooting
- **Watcher off / "watchdog not installed":** `pip install watchdog`.
- **Nothing happens:** the file must be .png/.jpg/.jpeg/.webp/.gif, and the watched path must be correct (hover the top-bar badge to see it).
- **Same file imported twice:** it won't be; path + modified time are remembered.
- **No OCR:** fine. Filename rules and filename dates still work.
