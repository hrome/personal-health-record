#!/usr/bin/env python3
"""Export medical.db tables to CSV files."""

import argparse
import csv
import os
import sqlite3
import sys


ALL_TABLES = [
    "files", "lab_events", "lab_indicators", "doctor_visits",
    "imaging_studies", "discharge_summaries", "prescriptions", "vaccinations",
]

DATE_COLUMNS = {
    "lab_events": "collection_date",
    "lab_indicators": "collection_date",
    "doctor_visits": "visit_date",
    "imaging_studies": "study_date",
    "discharge_summaries": "admission_date",
    "prescriptions": "prescription_date",
    "vaccinations": "vaccination_date",
}


def get_db_path(base_path: str) -> str:
    return os.path.join(base_path, "structured_database", "medical.db")


def export_table(conn: sqlite3.Connection, table: str, output_dir: str, since: str | None) -> str:
    cursor = conn.cursor()
    date_col = DATE_COLUMNS.get(table)
    if since and date_col:
        cursor.execute(f"SELECT * FROM {table} WHERE {date_col} >= ?", (since,))
    else:
        cursor.execute(f"SELECT * FROM {table}")
    rows = cursor.fetchall()
    col_names = [desc[0] for desc in cursor.description]
    out_path = os.path.join(output_dir, f"{table}.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(col_names)
        writer.writerows(rows)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Export medical records database tables to CSV.")
    parser.add_argument("--base-path", required=True, help="Root archive directory")
    parser.add_argument("--output-dir", required=True, help="Directory to write CSV files")
    parser.add_argument("--tables", nargs="+", choices=ALL_TABLES,
                        help="Tables to export (default: all)")
    parser.add_argument("--since", help="ISO date filter — export rows with date >= this value")
    args = parser.parse_args()

    db_path = get_db_path(args.base_path)
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}. Run init_db.py first.", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    tables = args.tables or ALL_TABLES

    with sqlite3.connect(db_path) as conn:
        for table in tables:
            try:
                path = export_table(conn, table, args.output_dir, args.since)
                print(path)
            except sqlite3.Error as e:
                print(f"Error exporting {table}: {e}", file=sys.stderr)
                sys.exit(1)


if __name__ == "__main__":
    main()
