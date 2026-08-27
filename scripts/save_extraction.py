#!/usr/bin/env python3
"""
Save a completed JSON extraction to the archive.

Called by Claude in-session after extracting structured data from a medical
document (PDF attached in chat, read from a local path, or loaded via MCP).

Usage (preferred — pass the JSON via a file to avoid shell quoting/brace issues):
  python scripts/save_extraction.py \\
    --base-path ~/medical-archive \\
    --file-path ~/Downloads/blood_test.pdf \\
    --extraction-file ~/medical-archive/.phr_tmp/<sha1>.json

  # also accepted: inline string or stdin
  python scripts/save_extraction.py ... --extraction '{"document_type": "lab_result", ...}'
  python scripts/save_extraction.py ... --extraction - <<'EOF'
  {...}
  EOF
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from common import sha1_of_file
from delete_from_archive import delete_file_records
from init_db import init_db
from owner_file import (
    add_alias as owner_add_alias,
    add_policy_number as owner_add_policy_number,
    canonical_name_list,
    canonical_name_set,
    normalize_patient_name,
    normalize_policy_number,
    policy_number_set,
    read_owner_file,
    write_owner_file,
)


def _sv(v):
    """Serialize dict/list to JSON string so SQLite can bind it."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def log_ingest_error(base_path: str, sha1: str, filename: str, error) -> None:
    """Append one failure line to structured_database/errors.log.

    Format (documented in SKILL.md): <timestamp> | <sha1> | <filename> | <error>
    Logging must never mask the original failure, so all errors here are dropped.
    """
    message = " ".join(str(error).split())
    line = f"{datetime.now(timezone.utc).isoformat()} | {sha1} | {filename} | {message}\n"
    try:
        log_dir = os.path.join(base_path, "structured_database")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "errors.log"), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def db_check_duplicate(conn: sqlite3.Connection, sha1: str) -> "dict | None":
    row = conn.execute(
        "SELECT original_filename, import_timestamp FROM files WHERE sha1 = ?", (sha1,)
    ).fetchone()
    return dict(row) if row else None


_PATIENT_TABLES = (
    "lab_events",
    "doctor_visits",
    "imaging_studies",
    "discharge_summaries",
    "prescriptions",
    "vaccinations",
)


def db_known_patient_names(conn: sqlite3.Connection) -> "list[str]":
    """Distinct non-null patient_full_name values across all typed tables."""
    seen: dict = {}
    for table in _PATIENT_TABLES:
        try:
            rows = conn.execute(
                f"SELECT DISTINCT patient_full_name FROM {table} "
                f"WHERE patient_full_name IS NOT NULL AND patient_full_name <> ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            raw = row[0]
            norm = normalize_patient_name(raw)
            if norm and norm not in seen:
                seen[norm] = raw
    return list(seen.values())


def check_patient_identity(conn: sqlite3.Connection,
                           base_path: str,
                           file_patient_name: "str | None",
                           file_policy_number: "str | None" = None) -> "dict | None":
    """
    Return a mismatch result dict if the file's patient does not match the
    archive's known owner, otherwise None.

    Resolution order:
      1. archive_owner.json — authoritative when present.
      2. Distinct patient_full_name values across typed tables (legacy
         archives without an owner file).
      3. No known owner — pass through.

    When the file has no patient name but does carry an insurance policy number,
    and the owner file has recorded policy numbers, identity is verified by the
    policy number instead: a matching policy passes, a non-matching policy is a
    mismatch.

    A null/empty file patient name with no verifiable policy is allowed through
    here; SKILL.md requires Claude to confirm with the user at the conversation
    layer in that case.
    """
    norm_new = normalize_patient_name(file_patient_name)
    norm_policy = normalize_policy_number(file_policy_number)

    owner = read_owner_file(base_path)
    if owner is not None:
        if norm_new is not None:
            if norm_new in canonical_name_set(owner):
                return None
            return {
                "archive_patient_names": canonical_name_list(owner),
                "file_patient_name": file_patient_name,
                "owner_file_present": True,
                "match_basis": "patient_name",
            }
        # No name on the file — fall back to the policy number if both the file
        # and the owner card have one.
        owner_policies = policy_number_set(owner)
        if norm_policy is not None and owner_policies:
            if norm_policy in owner_policies:
                return None
            return {
                "archive_patient_names": canonical_name_list(owner),
                "file_patient_name": None,
                "file_policy_number": file_policy_number,
                "archive_policy_numbers": list(owner["owner"].get("policy_numbers") or []),
                "owner_file_present": True,
                "match_basis": "policy_number",
            }
        return None

    if norm_new is None:
        return None
    known = db_known_patient_names(conn)
    if not known:
        return None
    norm_known = {normalize_patient_name(n) for n in known}
    if norm_new in norm_known:
        return None
    return {
        "archive_patient_names": known,
        "file_patient_name": file_patient_name,
        "owner_file_present": False,
    }


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
        (sha1, _sv(s.get("collection_date")), _sv(meta.get("patient_full_name")), _sv(meta.get("patient_dob")),
         _sv(s.get("laboratory_name")), _sv(s.get("laboratory_address")), _sv(s.get("laboratory_city")),
         _sv(s.get("ordering_doctor")), _sv(s.get("performing_doctor")), _sv(s.get("report_date")),
         len(s.get("indicators") or []), _sv(s.get("notes"))),
    )
    event_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for ind in s.get("indicators") or []:
        conn.execute(
            "INSERT INTO lab_indicators "
            "(lab_event_id, file_sha1, collection_date, indicator_name, indicator_name_en, "
            "value_raw, value_numeric, unit, ref_range_low, ref_range_high, ref_range_text, "
            "ref_range_notes, range_status, flag, method, other_notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, sha1, _sv(s.get("collection_date")),
             _sv(ind.get("indicator_name")), _sv(ind.get("indicator_name_en")),
             _sv(ind.get("value_raw")), ind.get("value_numeric"), _sv(ind.get("unit")),
             ind.get("ref_range_low"), ind.get("ref_range_high"),
             _sv(ind.get("ref_range_text")), _sv(ind.get("ref_range_notes")),
             _sv(ind.get("range_status")), _sv(ind.get("flag")),
             _sv(ind.get("method")), _sv(ind.get("other_notes"))),
        )


def db_insert_doctor_visit(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO doctor_visits "
        "(file_sha1, visit_date, patient_full_name, patient_dob, doctor_full_name, "
        "doctor_specialty, clinic_name, clinic_address, chief_complaint, anamnesis, "
        "objective_findings, diagnosis_main, diagnosis_icd, diagnosis_secondary, "
        "treatment_plan, medications_prescribed, recommendations, next_visit_date, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, _sv(s.get("visit_date")), _sv(meta.get("patient_full_name")), _sv(meta.get("patient_dob")),
         _sv(s.get("doctor_full_name")), _sv(s.get("doctor_specialty")), _sv(s.get("clinic_name")),
         _sv(s.get("clinic_address")), _sv(s.get("chief_complaint")), _sv(s.get("anamnesis")),
         _sv(s.get("objective_findings")), _sv(s.get("diagnosis_main")), _sv(s.get("diagnosis_icd")),
         _sv(s.get("diagnosis_secondary")), _sv(s.get("treatment_plan")), _sv(s.get("medications_prescribed")),
         _sv(s.get("recommendations")), _sv(s.get("next_visit_date")), _sv(s.get("notes"))),
    )


def db_insert_imaging(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO imaging_studies "
        "(file_sha1, study_date, study_type, body_region, patient_full_name, patient_dob, "
        "referring_doctor, performing_doctor, radiologist, clinic_name, equipment_model, "
        "protocol, contrast_used, description, conclusion, recommendations, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, s.get("study_date"), s.get("study_type"), s.get("body_region"),
         _sv(meta.get("patient_full_name")), _sv(meta.get("patient_dob")),
         _sv(s.get("referring_doctor")), _sv(s.get("performing_doctor")), _sv(s.get("radiologist")),
         _sv(s.get("clinic_name")), _sv(s.get("equipment_model")), _sv(s.get("protocol")),
         _sv(s.get("contrast_used")), _sv(s.get("description")), _sv(s.get("conclusion")),
         _sv(s.get("recommendations")), _sv(s.get("notes"))),
    )


def db_insert_discharge(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO discharge_summaries "
        "(file_sha1, patient_full_name, patient_dob, admission_date, discharge_date, ward, "
        "hospital_name, attending_doctor, admission_reason, diagnosis_on_admission, "
        "diagnosis_on_discharge, diagnosis_icd, procedures_performed, treatment_summary, "
        "discharge_condition, discharge_medications, follow_up_instructions, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, _sv(meta.get("patient_full_name")), _sv(meta.get("patient_dob")),
         _sv(s.get("admission_date")), _sv(s.get("discharge_date")), _sv(s.get("ward")),
         _sv(s.get("hospital_name")), _sv(s.get("attending_doctor")), _sv(s.get("admission_reason")),
         _sv(s.get("diagnosis_on_admission")), _sv(s.get("diagnosis_on_discharge")), _sv(s.get("diagnosis_icd")),
         _sv(s.get("procedures_performed")), _sv(s.get("treatment_summary")), _sv(s.get("discharge_condition")),
         _sv(s.get("discharge_medications")), _sv(s.get("follow_up_instructions")), _sv(s.get("notes"))),
    )


def db_insert_prescription(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO prescriptions "
        "(file_sha1, prescription_date, patient_full_name, patient_dob, doctor_full_name, "
        "doctor_specialty, clinic_name, medication_name, dosage, form, frequency, duration, "
        "instructions, prescription_number, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, _sv(s.get("prescription_date")), _sv(meta.get("patient_full_name")), _sv(meta.get("patient_dob")),
         _sv(s.get("doctor_full_name")), _sv(s.get("doctor_specialty")), _sv(s.get("clinic_name")),
         _sv(s.get("medication_name")), _sv(s.get("dosage")), _sv(s.get("form")), _sv(s.get("frequency")),
         _sv(s.get("duration")), _sv(s.get("instructions")), _sv(s.get("prescription_number")), _sv(s.get("notes"))),
    )


def db_insert_vaccination(conn: sqlite3.Connection, sha1: str, s: dict, meta: dict) -> None:
    conn.execute(
        "INSERT INTO vaccinations "
        "(file_sha1, vaccination_date, patient_full_name, patient_dob, vaccine_name, "
        "disease_targeted, manufacturer, batch_number, dose_number, clinic_name, "
        "administering_doctor, next_dose_date, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sha1, _sv(s.get("vaccination_date")), _sv(meta.get("patient_full_name")), _sv(meta.get("patient_dob")),
         _sv(s.get("vaccine_name")), _sv(s.get("disease_targeted")), _sv(s.get("manufacturer")),
         _sv(s.get("batch_number")), _sv(s.get("dose_number")), _sv(s.get("clinic_name")),
         _sv(s.get("administering_doctor")), _sv(s.get("next_dose_date")), _sv(s.get("notes"))),
    )


DB_INSERTERS = {
    "lab_result": db_insert_lab,
    "doctor_visit": db_insert_doctor_visit,
    "imaging_study": db_insert_imaging,
    "discharge_summary": db_insert_discharge,
    "prescription": db_insert_prescription,
    "vaccination": db_insert_vaccination,
}


def save_to_archive(file_path: str, base_path: str, extraction: dict,
                    force: bool = False,
                    allow_patient_mismatch: bool = False,
                    add_alias_first: "str | None" = None) -> dict:
    """
    Persist a completed extraction to the archive.
    Called by Claude in-session after extracting structured data from a document.

    Returns a result dict with status:
      ingested | duplicate | patient_mismatch | no_owner_file | error.
    """
    original_filename = os.path.basename(file_path)
    sha1 = sha1_of_file(file_path)
    db_path = init_db(base_path)

    doc_type = extraction.get("document_type", "unknown")
    brief = extraction.get("brief_description", "")
    language = extraction.get("language", "unknown")
    meta = extraction.get("metadata") or {}
    structured = extraction.get("structured") or {}

    if add_alias_first:
        updated = owner_add_alias(base_path, add_alias_first)
        if updated is None:
            return {
                "sha1": sha1,
                "status": "no_owner_file",
                "original_filename": original_filename,
                "base_path": base_path,
                "error": (
                    "Cannot add an alias: archive_owner.json does not exist yet "
                    "for this archive. Ingest a file with a known patient name "
                    "first so the owner file can be auto-created."
                ),
            }

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
        if not allow_patient_mismatch:
            mismatch = check_patient_identity(
                conn, base_path, meta.get("patient_full_name"),
                meta.get("policy_number"),
            )
            if mismatch is not None:
                return {
                    "sha1": sha1,
                    "status": "patient_mismatch",
                    "original_filename": original_filename,
                    "base_path": base_path,
                    **mismatch,
                }

    orig_dir = os.path.join(base_path, "original_files")
    json_dir = os.path.join(base_path, "json_extractions")
    os.makedirs(orig_dir, exist_ok=True)
    os.makedirs(json_dir, exist_ok=True)
    ext = os.path.splitext(file_path)[1] or ".pdf"
    archived_original = os.path.join(orig_dir, f"{sha1}{ext}")
    json_extraction = os.path.join(json_dir, f"{sha1}.json")

    # An ingest must be all-or-nothing. If the database write fails after the
    # original has been copied, the next run's filesystem dedup check would see
    # {sha1}.* in original_files/ and skip the file forever, leaving it archived
    # but absent from the database. Roll back whatever this run created.
    created = [p for p in (archived_original, json_extraction) if not os.path.exists(p)]
    try:
        shutil.copy2(file_path, archived_original)

        with open(json_extraction, "w", encoding="utf-8") as f:
            json.dump(extraction, f, ensure_ascii=False, indent=2)

        with sqlite3.connect(db_path) as conn:
            if existing:
                # Re-ingest (--force): drop the previous rows first, otherwise
                # the typed tables accumulate a second copy of every lab event,
                # visit, indicator, etc. for this file.
                delete_file_records(conn, sha1)
            db_insert_file(conn, sha1, original_filename, doc_type, brief, language)
            inserter = DB_INSERTERS.get(doc_type)
            if inserter:
                inserter(conn, sha1, structured, meta)
            conn.commit()
    except Exception:
        for path in created:
            try:
                os.remove(path)
            except OSError:
                pass
        raise

    result: dict = {
        "sha1": sha1,
        "status": "ingested",
        "original_filename": original_filename,
        "document_type": doc_type,
        "brief_description": brief,
        "language": language,
    }
    if existing:
        result["reprocessed"] = True
        result["previous_import_timestamp"] = existing["import_timestamp"]
    if doc_type == "lab_result":
        result["indicators_count"] = len(structured.get("indicators") or [])

    # Auto-create archive_owner.json on the first ingest with a non-null
    # patient name. The user is never asked to type their name.
    patient_name = meta.get("patient_full_name")
    policy_number = meta.get("policy_number")
    if patient_name and read_owner_file(base_path) is None:
        owner = write_owner_file(
            base_path,
            patient_full_name=patient_name,
            patient_dob=meta.get("patient_dob"),
            policy_numbers=[policy_number] if policy_number else None,
        )
        result["owner_file_created"] = True
        result["owner"] = owner["owner"]
    elif policy_number and read_owner_file(base_path) is not None:
        # Record any new policy number on the owner card so future files that
        # lack a patient name can still be verified by policy number.
        updated = owner_add_policy_number(base_path, policy_number)
        if updated is not None and normalize_policy_number(policy_number) in {
            normalize_policy_number(p)
            for p in (updated["owner"].get("policy_numbers") or [])
        }:
            result["policy_numbers"] = updated["owner"].get("policy_numbers")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Save a JSON extraction to the medical archive."
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
    parser.add_argument("--force", action="store_true",
                        help="Overwrite even if SHA-1 already in database")
    parser.add_argument("--allow-patient-mismatch", action="store_true",
                        dest="allow_patient_mismatch",
                        help="Skip the patient-identity check. Only use after explicit "
                             "user confirmation that this file belongs to the same person "
                             "as the rest of the archive (e.g. a name spelled differently).")
    parser.add_argument("--add-alias", dest="add_alias", default=None,
                        help="Append this name to archive_owner.json owner.aliases before "
                             "the identity check (so a same-person-different-spelling file "
                             "can be ingested without --allow-patient-mismatch). Requires "
                             "archive_owner.json to already exist.")
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

    try:
        extraction = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.file_path):
        print(f"File not found: {args.file_path}", file=sys.stderr)
        sys.exit(1)

    try:
        result = save_to_archive(args.file_path, args.base_path, extraction,
                                 force=args.force,
                                 allow_patient_mismatch=args.allow_patient_mismatch,
                                 add_alias_first=args.add_alias)
    except Exception as e:
        try:
            sha1 = sha1_of_file(args.file_path)
        except OSError:
            sha1 = "-"
        log_ingest_error(args.base_path, sha1, os.path.basename(args.file_path), e)
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
