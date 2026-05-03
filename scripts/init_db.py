#!/usr/bin/env python3
"""Initialize the SQLite database with all 8 medical record tables."""

import argparse
import os
import sqlite3
import sys


SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    sha1                TEXT PRIMARY KEY,
    original_filename   TEXT,
    import_timestamp    TEXT,
    document_type       TEXT,
    brief_description   TEXT,
    language            TEXT
);

CREATE TABLE IF NOT EXISTS lab_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    file_sha1               TEXT REFERENCES files(sha1),
    collection_date         TEXT,
    patient_full_name       TEXT,
    patient_dob             TEXT,
    laboratory_name         TEXT,
    laboratory_address      TEXT,
    laboratory_city         TEXT,
    ordering_doctor         TEXT,
    performing_doctor       TEXT,
    report_date             TEXT,
    total_indicators_count  INTEGER,
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS lab_indicators (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lab_event_id        INTEGER REFERENCES lab_events(id),
    file_sha1           TEXT REFERENCES files(sha1),
    collection_date     TEXT,
    indicator_name      TEXT,
    indicator_name_en   TEXT,
    value_raw           TEXT,
    value_numeric       REAL,
    unit                TEXT,
    ref_range_low       REAL,
    ref_range_high      REAL,
    ref_range_text      TEXT,
    ref_range_notes     TEXT,
    range_status        TEXT,
    flag                TEXT,
    method              TEXT,
    other_notes         TEXT
);

CREATE TABLE IF NOT EXISTS doctor_visits (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    file_sha1               TEXT REFERENCES files(sha1),
    visit_date              TEXT,
    patient_full_name       TEXT,
    patient_dob             TEXT,
    doctor_full_name        TEXT,
    doctor_specialty        TEXT,
    clinic_name             TEXT,
    clinic_address          TEXT,
    chief_complaint         TEXT,
    anamnesis               TEXT,
    objective_findings      TEXT,
    diagnosis_main          TEXT,
    diagnosis_icd           TEXT,
    diagnosis_secondary     TEXT,
    treatment_plan          TEXT,
    medications_prescribed  TEXT,
    recommendations         TEXT,
    next_visit_date         TEXT,
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS imaging_studies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    file_sha1           TEXT REFERENCES files(sha1),
    study_date          TEXT,
    study_type          TEXT,
    body_region         TEXT,
    patient_full_name   TEXT,
    patient_dob         TEXT,
    referring_doctor    TEXT,
    performing_doctor   TEXT,
    radiologist         TEXT,
    clinic_name         TEXT,
    equipment_model     TEXT,
    protocol            TEXT,
    contrast_used       TEXT,
    description         TEXT,
    conclusion          TEXT,
    recommendations     TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS discharge_summaries (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_sha1                   TEXT REFERENCES files(sha1),
    patient_full_name           TEXT,
    patient_dob                 TEXT,
    admission_date              TEXT,
    discharge_date              TEXT,
    ward                        TEXT,
    hospital_name               TEXT,
    attending_doctor            TEXT,
    admission_reason            TEXT,
    diagnosis_on_admission      TEXT,
    diagnosis_on_discharge      TEXT,
    diagnosis_icd               TEXT,
    procedures_performed        TEXT,
    treatment_summary           TEXT,
    discharge_condition         TEXT,
    discharge_medications       TEXT,
    follow_up_instructions      TEXT,
    notes                       TEXT
);

CREATE TABLE IF NOT EXISTS prescriptions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    file_sha1           TEXT REFERENCES files(sha1),
    prescription_date   TEXT,
    patient_full_name   TEXT,
    patient_dob         TEXT,
    doctor_full_name    TEXT,
    doctor_specialty    TEXT,
    clinic_name         TEXT,
    medication_name     TEXT,
    dosage              TEXT,
    form                TEXT,
    frequency           TEXT,
    duration            TEXT,
    instructions        TEXT,
    prescription_number TEXT,
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS vaccinations (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    file_sha1               TEXT REFERENCES files(sha1),
    vaccination_date        TEXT,
    patient_full_name       TEXT,
    patient_dob             TEXT,
    vaccine_name            TEXT,
    disease_targeted        TEXT,
    manufacturer            TEXT,
    batch_number            TEXT,
    dose_number             TEXT,
    clinic_name             TEXT,
    administering_doctor    TEXT,
    next_dose_date          TEXT,
    notes                   TEXT
);
"""

TABLES = [
    "files", "lab_events", "lab_indicators", "doctor_visits",
    "imaging_studies", "discharge_summaries", "prescriptions", "vaccinations",
]


def init_db(base_path: str) -> str:
    db_dir = os.path.join(base_path, "structured_database")
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, "medical.db")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    return db_path


def main():
    parser = argparse.ArgumentParser(description="Initialize medical records SQLite database.")
    parser.add_argument("--base-path", required=True, help="Root archive directory")
    args = parser.parse_args()

    try:
        db_path = init_db(args.base_path)
        print(f"Database initialized at {db_path}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
