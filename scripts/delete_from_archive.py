#!/usr/bin/env python3
"""Inspect and delete archived files by SHA-1 hash."""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys


FILE_SHA1_TABLES = [
    "lab_indicators",
    "lab_events",
    "doctor_visits",
    "imaging_studies",
    "discharge_summaries",
    "prescriptions",
    "vaccinations",
]

SUMMARY_SQL = """
SELECT
    sha1,
    original_filename,
    import_timestamp,
    document_type,
    brief_description,
    language
FROM files
WHERE sha1 = ?
"""


def validate_sha1(sha1: str) -> str:
    normalized = sha1.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", normalized):
        raise ValueError("SHA-1 must be exactly 40 hexadecimal characters.")
    return normalized


def get_db_path(base_path: str) -> str:
    return os.path.join(base_path, "structured_database", "medical.db")


def get_json_path(base_path: str, sha1: str) -> str:
    return os.path.join(base_path, "json_extractions", f"{sha1}.json")


def get_original_file_path(base_path: str, sha1: str) -> str:
    matches = glob.glob(os.path.join(base_path, "original_files", f"{sha1}.*"))
    if not matches:
        raise FileNotFoundError(f"Original file not found for sha1={sha1}")
    return matches[0]


def load_json_metadata(base_path: str, sha1: str) -> dict:
    json_path = get_json_path(base_path, sha1)
    if not os.path.exists(json_path):
        return {}

    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"json_extraction_readable": False}

    metadata = payload.get("metadata") or {}
    structured = payload.get("structured") or {}
    summary = {
        "document_date": metadata.get("document_date"),
        "patient_full_name": metadata.get("patient_full_name"),
        "clinic_or_lab_name": metadata.get("clinic_or_lab_name"),
        "json_extraction_readable": True,
    }
    if payload.get("document_type") == "lab_result":
        summary["indicators_count"] = len(structured.get("indicators") or [])
    return {key: value for key, value in summary.items() if value is not None}


def get_file_summary(base_path: str, sha1: str) -> "dict | None":
    db_path = get_db_path(base_path)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}.")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(SUMMARY_SQL, (sha1,)).fetchone()

    if row is None:
        return None

    result = dict(row)
    result["original_file_exists"] = bool(
        glob.glob(os.path.join(base_path, "original_files", f"{sha1}.*"))
    )
    result["json_extraction_exists"] = os.path.exists(get_json_path(base_path, sha1))
    result.update(load_json_metadata(base_path, sha1))
    return result


def delete_file_records(conn: sqlite3.Connection, sha1: str) -> dict:
    counts: dict[str, int] = {}
    for table in FILE_SHA1_TABLES:
        deleted = conn.execute(f"DELETE FROM {table} WHERE file_sha1 = ?", (sha1,))
        counts[table] = deleted.rowcount

    deleted_files = conn.execute("DELETE FROM files WHERE sha1 = ?", (sha1,))
    counts["files"] = deleted_files.rowcount
    return counts


def delete_from_archive(base_path: str, sha1: str) -> dict:
    summary = get_file_summary(base_path, sha1)
    if summary is None:
        return {"status": "not_found", "sha1": sha1}

    db_path = get_db_path(base_path)
    original_path = get_original_file_path(base_path, sha1)
    json_path = get_json_path(base_path, sha1)

    with sqlite3.connect(db_path) as conn:
        counts = delete_file_records(conn, sha1)
        conn.commit()

    original_deleted = False
    if os.path.exists(original_path):
        os.remove(original_path)
        original_deleted = True

    json_deleted = False
    if os.path.exists(json_path):
        os.remove(json_path)
        json_deleted = True

    return {
        "status": "deleted",
        "sha1": sha1,
        "deleted_records": counts,
        "deleted_original_file": original_deleted,
        "deleted_json_extraction": json_deleted,
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Inspect or delete an archived medical file by SHA-1."
    )
    parser.add_argument("--base-path", required=True, help="Root archive directory")
    parser.add_argument("--sha1", required=True, help="SHA-1 hash of the archived file")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the file from original_files, json_extractions, and the database",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required together with --delete to avoid accidental removal",
    )
    args = parser.parse_args()

    try:
        sha1 = validate_sha1(args.sha1)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        sys.exit(1)

    try:
        if args.delete:
            if not args.confirm:
                print(
                    json.dumps(
                        {
                            "status": "error",
                            "error": "Deletion requires --confirm.",
                        }
                    )
                )
                sys.exit(1)
            result = delete_from_archive(args.base_path, sha1)
        else:
            summary = get_file_summary(args.base_path, sha1)
            if summary is None:
                result = {"status": "not_found", "sha1": sha1}
            else:
                result = {"status": "found", "summary": summary}
    except FileNotFoundError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        sys.exit(1)
    except sqlite3.Error as exc:
        print(json.dumps({"status": "error", "error": f"SQL error: {exc}"}))
        sys.exit(1)
    except OSError as exc:
        print(json.dumps({"status": "error", "error": f"Filesystem error: {exc}"}))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
