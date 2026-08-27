#!/usr/bin/env python3
"""
Database duplicate check for a single file — sequential hash -> date -> content.

Run AFTER extraction and BEFORE save_extraction.py (Step 4.5). The script derives
the SHA-1, document_type and document_date itself from the file and the extraction,
so callers cannot pass mismatched flags.

It checks, in order, and prints exactly one JSON object describing the outcome:

  1. Hash  — is this exact file already in the `files` table?
             -> status "exact_duplicate", and stop.
  2. Date  — otherwise, is there a document of the same type on the same date?
             -> none: status "no_match".
  3. Content — for each same-date candidate, load its stored JSON extraction and
             compare the `structured` block + identifying fields against the
             incoming extraction.
             -> any candidate identical: status "content_duplicate".
             -> same date but content differs: status "possible_duplicate".

Usage (pass the extraction as a file — a heredoc puts raw JSON into the shell
command, which trips the harness's brace-expansion check and forces an approval
prompt on every call):

    python scripts/check_db_duplicates.py \\
      --base-path ~/medical-archive \\
      --file-path /path/to/file.pdf \\
      --extraction-file ~/medical-archive/.phr_tmp/<sha1>.json
"""

import argparse
import json
import os
import sqlite3
import sys

from common import get_db_path, sha1_of_file


# document_type -> (table, primary date column, optional extra date column,
#                   list of identifying structured fields used for content comparison)
TYPE_MAP = {
    "lab_result":        ("lab_events",          "collection_date",   None,             ["laboratory_name"]),
    "doctor_visit":      ("doctor_visits",       "visit_date",        None,             ["doctor_full_name", "doctor_specialty", "clinic_name"]),
    "imaging_study":     ("imaging_studies",     "study_date",        None,             ["study_type", "body_region", "clinic_name"]),
    "discharge_summary": ("discharge_summaries", "admission_date",    "discharge_date", ["hospital_name", "attending_doctor"]),
    "prescription":      ("prescriptions",       "prescription_date", None,             ["medication_name", "doctor_full_name", "clinic_name"]),
    "vaccination":       ("vaccinations",        "vaccination_date",  None,             ["vaccine_name", "clinic_name"]),
}


def find_by_sha1(base_path: str, sha1: str) -> "dict | None":
    db_path = get_db_path(base_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT sha1, original_filename, import_timestamp, document_type, brief_description "
            "FROM files WHERE sha1 = ?",
            (sha1,),
        ).fetchone()
    return dict(row) if row else None


def find_candidates(base_path: str, document_type: str, document_date: str) -> list:
    db_path = get_db_path(base_path)
    table, date_col, extra_date_col, extra_cols = TYPE_MAP[document_type]

    select_cols = ["f.sha1", "f.original_filename", "f.import_timestamp",
                   "f.brief_description", f"t.{date_col} AS document_date"]
    for col in extra_cols:
        select_cols.append(f"t.{col}")

    where = f"t.{date_col} = ?"
    params = [document_date]
    if extra_date_col:
        where = f"({where} OR t.{extra_date_col} = ?)"
        params.append(document_date)

    sql = (
        f"SELECT {', '.join(select_cols)} "
        f"FROM {table} t JOIN files f ON f.sha1 = t.file_sha1 "
        f"WHERE {where} "
        f"ORDER BY f.import_timestamp"
    )

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _norm(v) -> str:
    """Normalize a scalar for comparison; None/'' both become ''."""
    if v is None:
        return ""
    return str(v).strip().lower()


def _indicator_names(structured: dict) -> set:
    names = set()
    for ind in (structured.get("indicators") or []):
        name = _norm(ind.get("indicator_name") or ind.get("indicator_name_en"))
        if name:
            names.add(name)
    return names


def compare_content(document_type: str, incoming: dict, candidate_extraction: dict) -> dict:
    """Compare the structured + identifying fields of two extractions."""
    fields = TYPE_MAP[document_type][3]
    new_s = incoming.get("structured") or {}
    old_s = candidate_extraction.get("structured") or {}

    matched_fields, differing_fields = [], []
    for field in fields:
        new_v, old_v = _norm(new_s.get(field)), _norm(old_s.get(field))
        if not new_v and not old_v:
            continue  # empty on both sides — no signal
        if new_v == old_v:
            matched_fields.append(field)
        else:
            differing_fields.append({
                "field": field,
                "new": new_s.get(field),
                "existing": old_s.get(field),
            })

    comparison = {"matched_fields": matched_fields, "differing_fields": differing_fields}

    if document_type == "lab_result":
        new_inds, old_inds = _indicator_names(new_s), _indicator_names(old_s)
        comparison["indicators"] = {
            "matched": len(new_inds & old_inds),
            "only_new": len(new_inds - old_inds),
            "only_existing": len(old_inds - new_inds),
        }
        indicators_match = new_inds == old_inds and bool(new_inds)
    else:
        indicators_match = True  # not applicable

    comparison["verdict"] = (
        "identical" if not differing_fields and indicators_match else "different"
    )
    return comparison


def load_candidate_extraction(base_path: str, sha1: str) -> "dict | None":
    path = os.path.join(base_path, "json_extractions", f"{sha1}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check(base_path: str, file_path: str, extraction: dict) -> dict:
    original_filename = os.path.basename(file_path)
    sha1 = sha1_of_file(file_path)

    # A brand-new archive has no database yet — save_extraction.py creates it on
    # the first ingest. Nothing can be a duplicate of an archive with no records.
    if not os.path.exists(get_db_path(base_path)):
        return {"status": "no_match", "sha1": sha1,
                "original_filename": original_filename,
                "document_type": (extraction.get("document_type") or "").strip() or None,
                "candidates": [],
                "reason": "db_not_initialized"}

    # 1. Hash check
    record = find_by_sha1(base_path, sha1)
    if record:
        return {"status": "exact_duplicate", "match_type": "hash", **record}

    doc_type = (extraction.get("document_type") or "").strip()
    meta = extraction.get("metadata") or {}
    doc_date = (meta.get("document_date") or "").strip()

    if doc_type not in TYPE_MAP:
        return {"status": "no_match", "sha1": sha1,
                "original_filename": original_filename,
                "document_type": doc_type or None,
                "reason": "unsupported_document_type"}

    if not doc_date:
        return {"status": "skipped", "sha1": sha1,
                "original_filename": original_filename,
                "document_type": doc_type, "reason": "no_document_date"}

    # 2. Date check
    candidates = find_candidates(base_path, doc_type, doc_date)
    if not candidates:
        return {"status": "no_match", "sha1": sha1,
                "original_filename": original_filename,
                "document_type": doc_type, "document_date": doc_date,
                "candidates": []}

    # 3. Content comparison
    any_identical = False
    for cand in candidates:
        cand_extraction = load_candidate_extraction(base_path, cand["sha1"])
        if cand_extraction is None:
            cand["comparison"] = {"verdict": "unknown", "reason": "json_not_found"}
            continue
        comparison = compare_content(doc_type, extraction, cand_extraction)
        cand["comparison"] = comparison
        if comparison["verdict"] == "identical":
            any_identical = True

    return {
        "status": "content_duplicate" if any_identical else "possible_duplicate",
        "sha1": sha1,
        "original_filename": original_filename,
        "document_type": doc_type,
        "document_date": doc_date,
        "candidates": candidates,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Sequential hash/date/content duplicate check for one file."
    )
    parser.add_argument("--base-path", required=True, help="Root archive directory")
    parser.add_argument("--file-path", dest="file_path", required=True,
                        help="Path to the original file (PDF, JPG, PNG, etc.)")
    parser.add_argument("--extraction", default=None,
                        help="JSON extraction string, or '-' to read from stdin")
    parser.add_argument("--extraction-file", dest="extraction_file", default=None,
                        help="Path to a file containing the JSON extraction. Preferred over "
                             "--extraction: keeps the JSON out of the shell command (no quoting "
                             "or brace-expansion issues).")
    args = parser.parse_args()

    if args.extraction_file is not None:
        with open(args.extraction_file, encoding="utf-8") as fh:
            raw = fh.read()
    elif args.extraction == "-":
        raw = sys.stdin.read()
    elif args.extraction is not None:
        raw = args.extraction
    else:
        parser.error("provide --extraction-file, --extraction, or --extraction -")
    extraction = json.loads(raw)

    print(json.dumps(check(args.base_path, args.file_path, extraction),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
