#!/usr/bin/env python3
"""
Save a completed JSON extraction to the archive.

Used by Claude in-session (Claude extracts the PDF itself, then calls this script
to persist results) and by ingest.py (which calls the Anthropic API first).

Usage:
  python scripts/save_extraction.py \\
    --base-path ~/medical-archive \\
    --pdf-path ~/Downloads/blood_test.pdf \\
    --extraction '{"document_type": "lab_result", ...}'

  # or pass JSON via stdin:
  echo '{...}' | python scripts/save_extraction.py \\
    --base-path ~/medical-archive \\
    --pdf-path ~/Downloads/blood_test.pdf \\
    --extraction -
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from init_db import init_db


def sha1_of_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def db_check_duplicate(conn: sqlite3.Connection, sha1: str) -> dict | None:
    row = conn.execute(
        "SELECT original_filename, import_timestamp FROM files WHERE sha1 = ?", (sha1,)
    ).fetchone()
    return dict(row) if row else None


def db_insert_file(conn: sqlite3.Connection, sha1: str, original_filename: str,
                   document_type: str, brief_description: str, language: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO files "
        "(sha1, original_filename, import_timestamp, document_type, brief_description, language) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (sha1, original_filename, datetime.now(timezone.utc).isoformat(),
         document_type, brief_description, language),
    )


def db_insert_lab(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO lab_events "
        "(file_sha1, collection_date, patient_full_name, patient_dob, "
        "laboratory_name, laboratory_address, laboratory_city, ordering_doctor, "
        "performing_doctor, report_date, total_indicators_count, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, s.get("collection_date"), meta.get("patient_full_name"), meta.get("patient_dob"),
         s.get("laboratory_name"), s.get("laboratory_address"), s.get("laboratory_city"),
         s.get("ordering_doctor"), s.get("performing_doctor"), s.get("report_date"),
         len(s.get("indicators") or []), s.get("notes")),
    )
    event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for ind in s.get("indicators") or []:
        conn.execute(
            "INSERT INTO lab_indicators "
            "(lab_event_id, file_sha1, collection_date, indicator_name, indicator_name_en, "
            "value_raw, value_numeric, unit, ref_range_low, ref_range_high, ref_range_text, "
            "ref_range_notes, range_status, flag, method, other_notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, sha1, s.get("collection_date"),
             ind.get("indicator_name"), ind.get("indicator_name_en"),
             ind.get("value_raw"), ind.get("value_numeric"), ind.get("unit"),
             ind.get("ref_range_low"), ind.get("ref_range_high"),
             ind.get("ref_range_text"), ind.get("ref_range_notes"),
             ind.get("range_status"), ind.get("flag"),
             ind.get("method"), ind.get("other_notes")),
        )


def db_insert_doctor_visit(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO doctor_visits "
        "(file_sha1, visit_date, patient_full_name, patient_dob, doctor_full_name, "
        "doctor_specialty, clinic_name, clinic_address, chief_complaint, anamnesis, "
        "objective_findings, diagnosis_main, diagnosis_icd, diagnosis_secondary, "
        "treatment_plan, medications_prescribed, recommendations, next_visit_date, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, s.get("visit_date"), meta.get("patient_full_name"), meta.get("patient_dob"),
         s.get("doctor_full_name"), s.get("doctor_specialty"), s.get("clinic_name"),
         s.get("clinic_address"), s.get("chief_complaint"), s.get("anamnesis"),
         s.get("objective_findings"), s.get("diagnosis_main"), s.get("diagnosis_icd"),
         s.get("diagnosis_secondary"), s.get("treatment_plan"), s.get("medications_prescribed"),
         s.get("recommendations"), s.get("next_visit_date"), s.get("notes")),
    )


def db_insert_imaging(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO imaging_studies "
        "(file_sha1, study_date, study_type, body_region, patient_full_name, patient_dob, "
        "referring_doctor, performing_doctor, radiologist, clinic_name, equipment_model, "
        "protocol, contrast_used, description, conclusion, recommendations, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, s.get("study_date"), s.get("study_type"), s.get("body_region"),
         meta.get("patient_full_name"), meta.get("patient_dob"),
         s.get("referring_doctor"), s.get("performing_doctor"), s.get("radiologist"),
         s.get("clinic_name"), s.get("equipment_model"), s.get("protocol"),
         s.get("contrast_used"), s.get("description"), s.get("conclusion"),
         s.get("recommendations"), s.get("notes")),
    )


def db_insert_discharge(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO discharge_summaries "
        "(file_sha1, patient_full_name, patient_dob, admission_date, discharge_date, ward, "
        "hospital_name, attending_doctor, admission_reason, diagnosis_on_admission, "
        "diagnosis_on_discharge, diagnosis_icd, procedures_performed, treatment_summary, "
        "discharge_condition, discharge_medications, follow_up_instructions, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, meta.get("patient_full_name"), meta.get("patient_dob"),
         s.get("admission_date"), s.get("discharge_date"), s.get("ward"),
         s.get("hospital_name"), s.get("attending_doctor"), s.get("admission_reason"),
         s.get("diagnosis_on_admission"), s.get("diagnosis_on_discharge"), s.get("diagnosis_icd"),
         s.get("procedures_performed"), s.get("treatment_summary"), s.get("discharge_condition"),
         s.get("discharge_medications"), s.get("follow_up_instructions"), s.get("notes")),
    )


def db_insert_prescription(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO prescriptions "
        "(file_sha1, prescription_date, patient_full_name, patient_dob, doctor_full_name, "
        "doctor_specialty, clinic_name, medication_name, dosage, form, frequency, duration, "
        "instructions, prescription_number, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, s.get("prescription_date"), meta.get("patient_full_name"), meta.get("patient_dob"),
         s.get("doctor_full_name"), s.get("doctor_specialty"), s.get("clinic_name"),
         s.get("medication_name"), s.get("dosage"), s.get("form"), s.get("frequency"),
         s.get("duration"), s.get("instructions"), s.get("prescription_number"), s.get("notes")),
    )


def db_insert_vaccination(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO vaccinations "
        "(file_sha1, vaccination_date, patient_full_name, patient_dob, vaccine_name, "
        "disease_targeted, manufacturer, batch_number, dose_number, clinic_name, "
        "administering_doctor, next_dose_date, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, s.get("vaccination_date"), meta.get("patient_full_name"), meta.get("patient_dob"),
         s.get("vaccine_name"), s.get("disease_targeted"), s.get("manufacturer"),
         s.get("batch_number"), s.get("dose_number"), s.get("clinic_name"),
         s.get("administering_doctor"), s.get("next_dose_date"), s.get("notes")),
    )


DB_INSERTERS = {
    "lab_result": db_insert_lab,
    "doctor_visit": db_insert_doctor_visit,
    "imaging_study": db_insert_imaging,
    "discharge_summary": db_insert_discharge,
    "prescription": db_insert_prescription,
    "vaccination": db_insert_vaccination,
}


def save_to_archive(pdf_path: str, base_path: str, extraction: dict,
                    force: bool = False) -> dict:
    """
    Persist a completed extraction to the archive.
    Called by Claude in-session and by ingest.py after API extraction.

    Returns a result dict with status: ingested | duplicate | error.
    """
    original_filename = os.path.basename(pdf_path)
    sha1 = sha1_of_file(pdf_path)
    db_path = os.path.join(base_path, "structured_database", "medical.db")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        existing = db_check_duplicate(conn, sha1)

    if existing and not force:
        return {
            "sha1": sha1,
            "status": "duplicate",
            "original_filename": original_filename,
            "imported_on": existing["import_timestamp"],
        }

    doc_type = extraction.get("document_type", "unknown")
    brief = extraction.get("brief_description", "")
    language = extraction.get("language", "unknown")
    meta = extraction.get("metadata") or {}
    structured = extraction.get("structured") or {}

    # Copy original PDF
    orig_dir = os.path.join(base_path, "original_files")
    os.makedirs(orig_dir, exist_ok=True)
    shutil.copy2(pdf_path, os.path.join(orig_dir, f"{sha1}.pdf"))

    # Save JSON extraction
    json_dir = os.path.join(base_path, "json_extractions")
    os.makedirs(json_dir, exist_ok=True)
    with open(os.path.join(json_dir, f"{sha1}.json"), "w", encoding="utf-8") as f:
        json.dump(extraction, f, ensure_ascii=False, indent=2)

    # Write to SQLite
    with sqlite3.connect(db_path) as conn:
        db_insert_file(conn, sha1, original_filename, doc_type, brief, language)
        inserter = DB_INSERTERS.get(doc_type)
        if inserter:
            inserter(conn, sha1, structured, meta)
        conn.commit()

    result: dict = {
        "sha1": sha1,
        "status": "ingested",
        "original_filename": original_filename,
        "document_type": doc_type,
        "brief_description": brief,
        "language": language,
    }
    if doc_type == "lab_result":
        result["indicators_count"] = len(structured.get("indicators") or [])
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Save a JSON extraction to the medical archive."
    )
    parser.add_argument("--base-path", required=True, help="Root archive directory")
    parser.add_argument("--pdf-path", required=True,
                        help="Path to the original PDF file")
    parser.add_argument("--extraction", required=True,
                        help="JSON extraction string, or '-' to read from stdin")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite even if SHA-1 already in database")
    args = parser.parse_args()

    if args.extraction == "-":
        raw = sys.stdin.read()
    else:
        raw = args.extraction

    try:
        extraction = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.pdf_path):
        print(f"PDF not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    init_db(args.base_path)

    try:
        result = save_to_archive(args.pdf_path, args.base_path, extraction, force=args.force)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
