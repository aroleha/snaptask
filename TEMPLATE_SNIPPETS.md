# Optional template snippets

The backend is complete without these. They only expose the new data in your existing pages.
Paste them inside the loop that draws one screenshot (the variable is assumed to be `s`;
rename it if your loop uses another name). `CATEGORIES` and `meta()` are already available
in every template.

## 1. Change category (manual correction)
```html
<form method="post" action="{{ url_for('set_category', sid=s.id) }}">
  <select name="category" onchange="this.form.submit()">
    {% for c in CATEGORIES %}
      <option value="{{ c }}" {{ 'selected' if c == s.category }}>{{ meta(c).icon }} {{ meta(c).label }}</option>
    {% endfor %}
  </select>
</form>
```

## 2. Confidence and extracted text
```html
{% if s.category_manual %}<span>Set manually</span>
{% elif s.confidence is not none %}<span>{{ (s.confidence * 100)|round|int }}% sure</span>{% endif %}
{% if s.text %}<details><summary>Text in this screenshot</summary><pre>{{ s.text }}</pre></details>{% endif %}
```

## 3. Full category names (optional)
Wherever a template prints `{{ s.category }}` or the category name of a group/card,
`{{ meta(c).label }}` prints the long form, e.g. "Payments / Receipts" instead of "Payments".

## 4. Delete button (on each screenshot)
```html
<form method="post" action="{{ url_for('delete_shot', sid=s.id) }}"
      onsubmit="return confirm('Delete this screenshot from the library?')">
  <button type="submit" title="Delete">{{ icon('x', 16) }} Delete</button>
</form>
```

## 5. Recent screenshots link (next to the category chips, or in the sidebar)
```html
<a href="{{ url_for('recent') }}" class="{{ 'active' if cat == RECENT }}">{{ icon('clock') }} Recent</a>
```
