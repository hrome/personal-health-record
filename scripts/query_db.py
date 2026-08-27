#!/usr/bin/env python3
"""Run a SELECT query against medical.db and return results as JSON or an ASCII table."""

import argparse
import json
import os
import sqlite3
import sys
import urllib.parse

from common import get_db_path


def run_query(db_path: str, sql: str) -> list[dict]:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}. Run init_db.py first.")
    # Read-only connection: this script only ever answers questions, and the SQL
    # it runs is composed per request, so writes must be rejected by SQLite.
    uri = f"file:{urllib.parse.quote(db_path)}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(sql)
        return [dict(row) for row in cursor.fetchall()]


def render_table(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    cols = list(rows[0].keys())
    widths = {c: max(len(c), max((len(str(r[c] or "")) for r in rows), default=0)) for c in cols}
    sep = "+" + "+".join("-" * (widths[c] + 2) for c in cols) + "+"
    header = "|" + "|".join(f" {c:<{widths[c]}} " for c in cols) + "|"
    lines = [sep, header, sep]
    for row in rows:
        lines.append("|" + "|".join(f" {str(row[c] or ''):<{widths[c]}} " for c in cols) + "|")
    lines.append(sep)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Query the medical records SQLite database.")
    parser.add_argument("--base-path", required=True, help="Root archive directory")
    parser.add_argument("--sql", required=True, help="SQL SELECT statement to execute")
    parser.add_argument("--format", choices=["json", "table"], default="json",
                        help="Output format (default: json)")
    args = parser.parse_args()

    db_path = get_db_path(args.base_path)
    try:
        rows = run_query(db_path, args.sql)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as e:
        print(f"SQL error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.format == "table":
        print(render_table(rows))
    else:
        print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
