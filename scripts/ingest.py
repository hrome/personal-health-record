#!/usr/bin/env python3
"""
Bulk ingestion pipeline: PDF → Anthropic API extraction → archive.

For single files attached in a Claude Code session, use save_extraction.py instead
(Claude extracts the PDF itself, no API call needed).
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    print("Missing dependency: pip install pdfplumber", file=sys.stderr)
    sys.exit(1)

try:
    import anthropic
except ImportError:
    print("Missing dependency: pip install anthropic", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, os.path.dirname(__file__))
from init_db import init_db
from save_extraction import save_to_archive, sha1_of_file, db_check_duplicate
import sqlite3


EXTRACTION_SYSTEM_PROMPT = """You are a medical document parser. Extract ALL information from the document and return a single JSON object.

Rules:
- Extract EVERYTHING: dates, names, addresses, phone numbers, IDs, diagnoses, medications, dosages, reference ranges, doctor signatures, clinic stamps, handwritten notes, footnotes, measurement units, equipment model numbers, accreditation numbers — everything.
- If a field is not found or not legible, use null. Never invent values.
- Do not summarize or paraphrase. Preserve original values exactly as written.
- Detect document language automatically (ru / en / mixed). Preserve field values in their original language.
- Return ONLY valid JSON. No markdown fences, no commentary, no preamble.

Identify the document type. Possible values:
  lab_result | doctor_visit | imaging_study | discharge_summary | prescription | vaccination | unknown

Top-level structure:
{
  "document_type": "<type>",
  "language": "<ru|en|mixed>",
  "brief_description": "<1-2 sentence summary: what type of document, date, provider/lab name>",
  "raw_text": "<full text of the document as-is>",
  "metadata": {
    "patient_full_name": null,
    "patient_dob": null,
    "clinic_or_lab_name": null,
    "doctor_full_name": null,
    "document_date": null
  },
  "structured": {
    // fields per document type — see SKILL.md for full schemas
  }
}

FOR lab_result — structured contains:
  collection_date, laboratory_name, laboratory_address, laboratory_city,
  ordering_doctor, performing_doctor, report_date, notes,
  indicators: [{indicator_name, indicator_name_en, value_raw, value_numeric, unit,
                ref_range_low, ref_range_high, ref_range_text, ref_range_notes,
                range_status (normal/above/below/null), flag, method, other_notes}]

FOR doctor_visit — structured contains:
  visit_date, doctor_full_name, doctor_specialty, clinic_name, clinic_address,
  chief_complaint, anamnesis, objective_findings, diagnosis_main, diagnosis_icd,
  diagnosis_secondary, treatment_plan, medications_prescribed, recommendations,
  next_visit_date, notes

FOR imaging_study — structured contains:
  study_date, study_type, body_region, referring_doctor, performing_doctor,
  radiologist, clinic_name, equipment_model, protocol, contrast_used,
  description, conclusion, recommendations, notes

FOR discharge_summary — structured contains:
  admission_date, discharge_date, ward, hospital_name, attending_doctor,
  admission_reason, diagnosis_on_admission, diagnosis_on_discharge, diagnosis_icd,
  procedures_performed, treatment_summary, discharge_condition,
  discharge_medications, follow_up_instructions, notes

FOR prescription — structured contains:
  prescription_date, doctor_full_name, doctor_specialty, clinic_name,
  medication_name, dosage, form, frequency, duration, instructions,
  prescription_number, notes

FOR vaccination — structured contains:
  vaccination_date, vaccine_name, disease_targeted, manufacturer, batch_number,
  dose_number, clinic_name, administering_doctor, next_dose_date, notes

FOR unknown — structured contains: notes"""


def extract_pdf_text(pdf_path: str) -> str:
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
    return "\n\n".join(text_parts)


def extract_via_api(pdf_text: str, api_key: str) -> dict:
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        system=[
            {
                "type": "text",
                "text": EXTRACTION_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": f"Parse this medical document:\n\n{pdf_text}"}],
    )
    return json.loads(response.content[0].text.strip())


def log_error(base_path: str, sha1: str, filename: str, error: str) -> None:
    from datetime import datetime, timezone
    log_path = os.path.join(base_path, "structured_database", "errors.log")
    ts = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{ts} | {sha1} | {filename} | {error}\n")


def process_pdf(pdf_path: str, base_path: str, api_key: str,
                force: bool = False, dry_run: bool = False) -> dict:
    original_filename = os.path.basename(pdf_path)

    # Dedup check before doing any work
    if not force and not dry_run:
        sha1 = sha1_of_file(pdf_path)
        db_path = os.path.join(base_path, "structured_database", "medical.db")
        if os.path.exists(db_path):
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                existing = db_check_duplicate(conn, sha1)
            if existing:
                return {
                    "sha1": sha1,
                    "status": "duplicate",
                    "original_filename": original_filename,
                    "imported_on": existing["import_timestamp"],
                }

    try:
        pdf_text = extract_pdf_text(pdf_path)
    except Exception as e:
        return {"status": "error", "original_filename": original_filename,
                "error": f"PDF extraction failed: {e}"}

    if not pdf_text.strip():
        return {"status": "error", "original_filename": original_filename,
                "error": "No text extracted — file may be corrupted, password-protected, "
                         "or a very low-resolution scan."}

    try:
        extraction = extract_via_api(pdf_text, api_key)
    except json.JSONDecodeError as e:
        return {"status": "error", "original_filename": original_filename,
                "error": f"API returned invalid JSON: {e}"}
    except Exception as e:
        return {"status": "error", "original_filename": original_filename,
                "error": f"API call failed: {e}"}

    if dry_run:
        sha1 = sha1_of_file(pdf_path)
        structured = extraction.get("structured") or {}
        return {
            "sha1": sha1,
            "status": "dry_run",
            "original_filename": original_filename,
            "document_type": extraction.get("document_type", "unknown"),
            "brief_description": extraction.get("brief_description", ""),
            "language": extraction.get("language", "unknown"),
            "indicators_count": (len(structured.get("indicators") or [])
                                 if extraction.get("document_type") == "lab_result" else None),
        }

    return save_to_archive(pdf_path, base_path, extraction, force=force)


def load_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Ingest medical PDF(s) via Anthropic API extraction."
    )
    parser.add_argument("--base-path", required=True, help="Root archive directory")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pdf-path", help="Single PDF file to ingest")
    group.add_argument("--folder", help="Folder — ingest all *.pdf files (sorted by name)")
    parser.add_argument("--force", action="store_true",
                        help="Re-process even if SHA-1 already in database")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extract and print result but do not write anything")
    args = parser.parse_args()

    api_key = load_api_key()
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set. Add it to .env or set the environment variable.",
              file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        init_db(args.base_path)

    pdfs = [args.pdf_path] if args.pdf_path else sorted(
        str(p) for p in Path(args.folder).glob("*.pdf")
    )
    if not pdfs:
        print(json.dumps({"status": "error", "error": f"No PDF files found in {args.folder}"}))
        sys.exit(1)

    has_error = False
    for pdf_path in pdfs:
        if not os.path.isfile(pdf_path):
            result = {"status": "error", "original_filename": pdf_path, "error": "File not found"}
            has_error = True
        else:
            result = process_pdf(pdf_path, args.base_path, api_key,
                                 force=args.force, dry_run=args.dry_run)
            if result["status"] == "error":
                has_error = True
                log_error(args.base_path, result.get("sha1", "unknown"),
                          result.get("original_filename", pdf_path), result.get("error", ""))
        print(json.dumps(result, ensure_ascii=False))

    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
