"""Parsers for the HTML fragments PSX returns.

Several of the most useful routes (market watch, indices, sector summary,
historical, announcements) are server-rendered tables rather than JSON, so
reading them is part of the job rather than a workaround.

Two quirks drive the design:

  * Numeric cells carry the unformatted value in `data-order` behind a
    prettified cell body -- data-order="-0.23" under a rendered "v -0.23".
    Prefer the attribute, so callers get numbers instead of glyphs.
  * On date columns that same attribute is a unix epoch while the text is
    the readable date, so the choice has to be made per column, not per cell.
"""

import re
from html.parser import HTMLParser

DATE_COLUMNS = {"time", "date"}


class TableParser(HTMLParser):
    """Extract the FIRST <table> from a fragment.

    Scoped to the first table deliberately: /sector-summary/sectorwise ships
    a summary table followed by one table per sector, and slurping them all
    silently interleaves rows of different shapes into one result.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headers = []
        self.rows = []
        self.row_links = []
        self._depth = 0
        self._done = False
        self._row = None
        self._cell = None
        self._order = None
        self._name = None
        self._is_header = False
        self._link = ""

    def _active(self):
        return not self._done and self._depth == 1

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            return
        if not self._active():
            return
        attr = dict(attrs)
        if tag == "tr":
            self._row = []
            self._link = ""
        elif tag == "a" and self._row is not None and not self._link:
            href = attr.get("href") or ""
            if "/download/" in href:
                self._link = href
        elif tag in ("th", "td"):
            self._cell = []
            self._order = attr.get("data-order")
            self._name = attr.get("data-name")
            self._is_header = tag == "th"

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth -= 1
            if self._depth <= 0:
                self._done = True
            return
        if not self._active():
            return
        if tag == "tr":
            if self._row:
                self.rows.append(self._row)
                self.row_links.append(self._link)
            self._row = None
            self._link = ""
        elif tag in ("th", "td") and self._cell is not None:
            text = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            if self._is_header:
                self.headers.append(self._name or text or f"col{len(self.headers)}")
            elif self._row is not None:
                self._row.append((text, self._order))
            self._cell = self._order = self._name = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _value(header, text, order):
    if header.strip().lower().rstrip("*").strip() in DATE_COLUMNS:
        return text or order or ""
    if order not in (None, ""):
        return order
    return text


def parse_table(html):
    """Return (headers, rows, links). Links are '' where a row has no file."""
    parser = TableParser()
    parser.feed(html)
    if not parser.rows:
        return [], [], []
    width = max(len(row) for row in parser.rows)
    headers = list(parser.headers)
    if len(headers) < width:
        headers += [f"col{i}" for i in range(len(headers), width)]
    headers = headers[:width]
    rows = []
    for raw in parser.rows:
        raw = raw + [("", None)] * (width - len(raw))
        rows.append([_value(h, t, o) for h, (t, o) in zip(headers, raw)])
    return headers, rows, parser.row_links


def table_records(html, link_field=None, host=None):
    """Parse a table into dicts, optionally lifting each row's file link."""
    headers, rows, links = parse_table(html)
    out = []
    for row, link in zip(rows, links):
        record = dict(zip(headers, row))
        if link_field:
            record[link_field] = f"https://{host}{link}" if link else ""
        out.append(record)
    return out


def drop_widget_columns(records):
    """Remove positional columns (colN) and blank headers.

    These are the View/PDF button cells: presentation, not data. Any link
    they carried has already been lifted into its own field.
    """
    if not records:
        return []
    keep = [c for c in records[0] if c.strip() and not re.fullmatch(r"col\d+", c)]
    return [{c: r.get(c, "") for c in keep} for r in records]


def to_number(value):
    """Best-effort numeric coercion; returns None when it is not a number."""
    if value is None:
        return None
    text = str(value).replace(",", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
