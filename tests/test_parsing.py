"""Parser tests. All offline -- these must never touch PSX."""

from conftest import fixture

from psx_dps.parsing import (
    drop_widget_columns,
    parse_table,
    table_records,
    to_number,
)


def test_market_watch_uses_data_name_headers():
    rows = table_records(fixture("market_watch.html"))
    assert rows, "expected at least one row"
    for column in ("symbol", "sector", "ldcp", "open", "high", "low",
                   "close", "change", "percentChange", "volume"):
        assert column in rows[0], f"missing {column}"


def test_data_order_beats_rendered_text():
    """change cells render as "v -0.23"; we want the raw -0.23."""
    rows = table_records(fixture("market_watch.html"))
    for row in rows:
        assert to_number(row["change"]) is not None
        assert "▼" not in row["change"] and "▲" not in row["change"]


def test_headers_fall_back_to_th_text():
    """/indices has no data-name attributes, so headers come from the text."""
    headers, rows, _ = parse_table(fixture("indices.html"))
    assert rows
    assert not any(h.startswith("col") for h in headers), headers
    assert "INDEX" in [h.upper() for h in headers]


def test_date_column_prefers_text_over_epoch():
    """On date columns data-order is an epoch; the readable date is the text."""
    rows = table_records(fixture("history_month.html"))
    assert rows
    value = rows[0]["time"]
    assert not value.isdigit(), f"got raw epoch {value!r} instead of a date"
    assert "20" in value


def test_only_first_table_is_read():
    """Sector summary ships one table per sector after the summary table."""
    rows = table_records(fixture("sector_two_tables.html"))
    assert rows
    flat = [v for row in rows for v in row.values()]
    assert "SHOULD_NOT_APPEAR" not in flat


def test_announcement_links_are_absolute():
    rows = table_records(fixture("announcements.html"), link_field="url",
                         host="dps.psx.com.pk")
    assert rows
    assert any(r["url"].startswith("https://dps.psx.com.pk/download/")
               for r in rows)


def test_drop_widget_columns_removes_positional_cells():
    rows = drop_widget_columns(
        table_records(fixture("announcements.html"), link_field="url",
                      host="dps.psx.com.pk")
    )
    assert rows
    assert not any(c.startswith("col") or not c.strip() for c in rows[0])
    assert "url" in rows[0]


def test_to_number_handles_psx_formatting():
    assert to_number("1,234.5") == 1234.5
    assert to_number("-0.74%") == -0.74
    assert to_number("") is None
    assert to_number("N/A") is None


def test_empty_html_is_not_an_error():
    assert parse_table("<div>nothing here</div>") == ([], [], [])
