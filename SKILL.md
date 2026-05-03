---
name: personal-health-record
description: >
  Medical records assistant for organizing and querying personal health documents
  (lab results, doctor visits, MRI/ultrasound/X-ray/CT/ECG, discharge summaries,
  prescriptions, vaccinations). Use when users mention adding medical PDFs,
  querying health history, tracking lab indicator trends, reviewing past diagnoses,
  searching prescriptions, or organizing any medical paperwork. Trigger on casual
  mentions like "add my MRI results", "when was my last CBC", "show my vaccination
  history", "find all prescriptions from last year", "how has my hemoglobin changed",
  "что показал анализ крови", "добавь результаты анализов". Supports Russian and
  English documents (mixed-language documents are handled correctly).
  Also trigger reactively: after reading and analyzing any PDF in chat that turns
  out to be a medical document (lab results, doctor visit notes, imaging studies,
  discharge summaries, prescriptions, vaccinations) — append an offer to save it
  to the Personal Health Record archive at the end of the response, even if the
  user did not ask about the archive.
compatibility:
  - Python packages: anthropic, pdfplumber (bulk/folder mode only)
  - APIs: Anthropic API — only for bulk folder imports; in-session mode uses the current Claude session
  - Storage: SQLite local — no external services required
---

# Personal Health Record

Ingests, deduplicates, and analyzes personal medical documents using a local
3-layer archive. All data stays on the user's machine.

## Architecture

```
<BASE_PATH>/
├── original_files/          # Original PDFs, named by SHA-1 hash (never modified)
├── json_extractions/        # Full structured JSON extraction per file
└── structured_database/
    ├── medical.db           # SQLite — 8 typed tables
    └── errors.log           # Ingestion errors
```

Query order: **structured_database first** (fastest) → **json_extractions** if
SQL is insufficient → **original_files** only when the user explicitly asks.

---

## First-Run Onboarding

On first use, greet the user with:

> "Hi! This skill helps you organize and analyze your medical documents.
>
> **original_files** — your original PDFs, stored unchanged (named by SHA-1 hash).
> **json_extractions** — full structured JSON extracted from each file.
> **structured_database** — a local SQLite database with typed tables for each
>   document type (lab results, doctor visits, imaging studies, etc.).
>
> When you ask a question, I always query structured_database first (fastest).
> If that's not enough, I look at json_extractions. I only open original_files
> if you explicitly ask me to.
>
> Let's start — where should I store your medical archive?
> Please give me a folder path, e.g. `/Users/roman/medical-archive`."

After the user provides BASE_PATH, ask:

> "Got it. How would you like to provide files?
> A) Attach PDFs directly in this chat (great for 1–20 files)
> B) Give me a folder path on your computer (good for bulk imports)
> C) Other source (Google Drive, email, clinic API)"

Record BASE_PATH and the chosen method for the session.

---

## Operational Modes

| User says | Mode | Script |
|-----------|------|--------|
| PDF attached in chat | **in-session ingest** | Claude reads + `save_extraction.py` |
| "add folder", "bulk import", "process all PDFs" | **bulk ingest** | `ingest.py` |
| Any medical question | query | `query_db.py` |
| "how many files", "what's in my archive", "show summary" | status | `query_db.py` |
| "show my timeline", "history", "what happened in 2024" | timeline | `query_db.py` |
| "how has my [indicator] changed", "trend", "chart" | trends | `query_db.py` |
| "export", "give me a CSV", "backup" | export | `export_csv.py` |
| "re-extract", "update this file", "reprocess" | reprocess | `save_extraction.py --force` |
| "delete this file", "remove from archive", "удали файл из архива" | delete | `delete_from_archive.py` |

---

## Ingestion Pipeline

Two modes — choose based on how the user provides files.

### Mode A — In-Session (PDF attached in chat)

**No ANTHROPIC_API_KEY required.** Claude extracts the PDF directly in the current session,
which is faster and avoids an extra API round-trip.

**Step 1 — Hash all uploaded files first**
Before reading or extracting anything from any uploaded PDF, Claude must first compute the SHA-1
for every file in the batch and check whether each hash already exists in the `files` table.
This check is cheap and must always happen first.

Use the same helper logic as the scripts:
- compute SHA-1 from the original PDF file
- query `files` by `sha1`

**Step 2 — Tell the user which files are already archived and which are new**
Before any extraction starts, Claude must report the deduplication result for every uploaded file.
This per-file status output is mandatory for every upload batch, regardless of how many files were provided and regardless of whether files are duplicates, newly saved, failed, or mixed.
For each file, show at minimum:
- file name
- SHA-1
- whether it is already present in the archive
- if already present: `import_timestamp`
- if not present: that it will now be processed and saved

If a matching row exists, do not extract that PDF again unless the user explicitly asks to re-process it with `--force`.

**Step 3 — Read only the files that are not yet archived**
Use the `Read` tool on each file path that is not already present in the archive. Skip files that already exist.

**Step 4 — Extract structured data (Claude does this inline)**
Apply the extraction schema below and produce a JSON object with the required structure for each new file.

**Step 5 — Save newly extracted files to the archive immediately**
After extraction, Claude should save each newly processed file to the archive in the same run.
Do not pause for a separate confirmation step once the user has asked to process the files.

Use:
```bash
python scripts/save_extraction.py \
  --base-path <BASE_PATH> \
  --pdf-path <path_to_original_pdf> \
  --extraction '<json_string>'
```
For long JSON, pass via stdin to avoid shell quoting issues:
```bash
python scripts/save_extraction.py \
  --base-path <BASE_PATH> \
  --pdf-path <path_to_original_pdf> \
  --extraction - <<'EOF'
{"document_type": "lab_result", ...}
EOF
```

**Step 6 — Show a user-facing detailed result for every file**
After deduplication, extraction, and saving, Claude must show a detailed result covering every uploaded file,
including files that were skipped as duplicates and files that were newly saved.
This final report is mandatory for every file in the batch, regardless of status and regardless of the total number of files.
For each file, the output must include:
- the file name
- the SHA-1
- whether it was skipped as already archived or newly saved
- if skipped: the existing import timestamp
- the detected document type
- the document date, if available
- the clinic, lab, or provider name, if available
- the patient name, if available
- the brief description
- if newly saved: the key extracted contents from `structured`

If a file was already in the archive, the report must still include its status and existing import timestamp.
If a file was newly saved, the report must include both its status and a detailed summary of what was extracted from that file.

For `lab_result`, the summary must include:
- the indicator count
- a table of extracted indicators
- the table must include, when available: indicator name, result, unit, reference range, and status
- the table must include all extracted indicators from the file, not just a subset or first few rows
- any abnormal or out-of-range indicators called out explicitly

For other document types, the summary should list the main extracted fields relevant to that type
(for example diagnosis, conclusion, medications, recommendations, visit date, study type).

---

### Mode B — Bulk Folder Import

**Requires ANTHROPIC_API_KEY.** The script calls the Anthropic API once per file.
Use for importing a folder of PDFs or when not working interactively.

```bash
# Single file
python scripts/ingest.py --base-path <BASE_PATH> --pdf-path <file.pdf>

# Entire folder
python scripts/ingest.py --base-path <BASE_PATH> --folder <folder_path>
```

The script handles deduplication, extraction, and DB writing automatically.

---

### Deduplication (both modes)

Both modes compute SHA-1 before doing any work. If the hash already exists in `files`:
> "Skipping `{filename}` — already imported on {import_timestamp}."

For Mode A specifically:
- compute hashes for the entire uploaded batch first
- report duplicate/new status to the user before any extraction
- extract and save only the files that are not already present
- still include duplicate files in the final per-file report

Use `--force` to re-process an existing file.

---

### Extraction Schema (for in-session Mode A)

Claude must produce a JSON object with this top-level structure:

```json
{
  "document_type": "lab_result | doctor_visit | imaging_study | discharge_summary | prescription | vaccination | unknown",
  "language": "ru | en | mixed",
  "brief_description": "1-2 sentence summary: document type, date, provider/lab name",
  "raw_text": "full text of the document as-is",
  "metadata": {
    "patient_full_name": null,
    "patient_dob": null,
    "clinic_or_lab_name": null,
    "doctor_full_name": null,
    "document_date": null
  },
  "structured": { }
}
```

`structured` contents depend on `document_type` — see **SQLite Schema Reference** for field names.
For `lab_result`, `structured` must also contain an `indicators` array (one object per test result).

**Rules:**
- Use `null` for any field not found — never invent values.
- Preserve original field values in their source language (ru/en).
- For `range_status` in lab indicators: `normal`, `above`, `below`, or `null` if unknown.

---

### After ingest — report to user

```
Done! Imported 3 files:
• KLA_January_2025.pdf — lab result (18 indicators) — Invitro, 2025-01-15
• Ultrasound_March_2025.pdf — abdominal ultrasound — City Hospital, 2025-03-10
• GP_Visit_April_2025.pdf — GP consultation — Dr. Ivanov, 2025-04-02
```

**Format rules:**
- For every `lab_result` file, the indicator count **must** always be shown in parentheses: `(N indicators)`. Never omit it for lab results, even if N = 1.
- For other document types (imaging, visit, etc.), the indicator count is not shown.
- In Mode A (in-session), the count is the length of the `indicators` array in the extracted JSON.
- In Mode B (bulk import), the count comes from `indicators_count` in the script's JSON output.
- In Mode A, the final interactive report must also show the SHA-1 for every file.
- In Mode A, the final interactive report must cover both skipped duplicates and newly saved files.
- In Mode A, after any upload, always produce a per-file report for every file, even if all files were duplicates, even if only one file was provided, and even if the batch contains a mix of statuses.

**If PDF has no readable text:**
> "Couldn't extract text from `{filename}`. It may be corrupted, password-protected,
> or a very low-resolution scan. Please try re-exporting or sending a clearer scan."

**If document type is `unknown` after extraction:**
> "I extracted text from `{filename}` but couldn't identify the document type.
> Could you tell me? Options: lab result, doctor visit note, imaging study,
> discharge summary, prescription, vaccination record, or something else."

---

## Querying

Use `query_db.py` for all structured data questions.

```bash
python scripts/query_db.py --base-path <BASE_PATH> --sql "<SQL>" [--format table]
```

### Common query patterns

**Status / archive overview:**
```sql
SELECT document_type, COUNT(*) AS count FROM files GROUP BY document_type
```

**All files, newest first:**
```sql
SELECT original_filename, document_type, brief_description, import_timestamp
FROM files ORDER BY import_timestamp DESC
```

**Lab result trend (e.g. hemoglobin):**
```sql
SELECT li.collection_date, li.value_raw, li.value_numeric, li.unit,
       li.ref_range_low, li.ref_range_high, li.range_status
FROM lab_indicators li
WHERE lower(li.indicator_name_en) LIKE '%hemoglobin%'
   OR lower(li.indicator_name) LIKE '%гемоглобин%'
ORDER BY li.collection_date
```

**Out-of-range indicators:**
```sql
SELECT li.collection_date, li.indicator_name, li.value_raw, li.unit,
       li.ref_range_text, li.range_status, f.original_filename
FROM lab_indicators li
JOIN files f ON li.file_sha1 = f.sha1
WHERE li.range_status IN ('above', 'below')
ORDER BY li.collection_date DESC
```

**All diagnoses from doctor visits:**
```sql
SELECT visit_date, doctor_specialty, diagnosis_main, diagnosis_icd
FROM doctor_visits
ORDER BY visit_date DESC
```

**Timeline (all event types, given year):**
```sql
SELECT '🔬 Lab' AS type, collection_date AS date, notes AS summary, file_sha1
FROM lab_events WHERE collection_date LIKE '2024%'
UNION ALL
SELECT '🏥 Visit', visit_date, diagnosis_main || ' — ' || doctor_specialty, file_sha1
FROM doctor_visits WHERE visit_date LIKE '2024%'
UNION ALL
SELECT '📷 Imaging', study_date, study_type || ' — ' || body_region, file_sha1
FROM imaging_studies WHERE study_date LIKE '2024%'
ORDER BY date
```

Always **cite the source file** (original_filename + date) for every fact stated.

---

## Deleting Files From The Archive

Deletion is supported only by **SHA-1 hash**. Do not accept filename-only deletion requests.

### Required flow

1. Ask the user for the file SHA-1 if they have not provided it yet.
2. Run:

```bash
python scripts/delete_from_archive.py --base-path <BASE_PATH> --sha1 <SHA1>
```

3. Show the user a short summary from the returned JSON before deleting anything.
   The summary must include at minimum:
   - `sha1`
   - `original_filename`
   - `import_timestamp`
   - `document_type`
   - `brief_description`
   - whether the original PDF and JSON extraction currently exist
4. Ask for explicit confirmation.
5. Only after the user confirms, run:

```bash
python scripts/delete_from_archive.py \
  --base-path <BASE_PATH> \
  --sha1 <SHA1> \
  --delete \
  --confirm
```

### What deletion must remove

After confirmation, delete all archive data associated with that SHA-1:
- `original_files/<SHA1>.pdf`
- `json_extractions/<SHA1>.json`
- the row in `files`
- all rows in typed tables where `file_sha1 = <SHA1>`

### Confirmation policy

- Never delete immediately after receiving the SHA-1.
- Always show the file summary first.
- Always require an explicit user confirmation message before running `--delete --confirm`.
- If the SHA-1 is not found, tell the user that no archived file exists for that hash and do not ask for confirmation.

---

## Quick Start

```bash
# For in-session mode — no setup needed beyond BASE_PATH
# Just attach a PDF in chat and Claude handles extraction directly.

# For bulk/folder mode — install dependencies first:
pip install anthropic pdfplumber --break-system-packages

# Configure API key (bulk mode only)
cp .env.example .env   # set ANTHROPIC_API_KEY

# Ingest a folder of PDFs via API
python scripts/ingest.py --base-path ~/medical-archive --folder ~/Downloads/medical-pdfs

# 4. Query
python scripts/query_db.py \
  --base-path ~/medical-archive \
  --sql "SELECT original_filename, document_type, brief_description FROM files" \
  --format table

# 5. Export all tables to CSV
python scripts/export_csv.py \
  --base-path ~/medical-archive \
  --output-dir ~/medical-export

# 6. Inspect a file before deleting it
python scripts/delete_from_archive.py \
  --base-path ~/medical-archive \
  --sha1 <SHA1>

# 7. Delete a file after confirmation
python scripts/delete_from_archive.py \
  --base-path ~/medical-archive \
  --sha1 <SHA1> \
  --delete \
  --confirm
```

---

## SQLite Schema Reference

### `files` — every imported file
| Column | Type | Notes |
|--------|------|-------|
| `sha1` | TEXT PK | SHA-1 of original PDF |
| `original_filename` | TEXT | Filename as provided |
| `import_timestamp` | TEXT | ISO 8601 |
| `document_type` | TEXT | lab_result / doctor_visit / imaging_study / discharge_summary / prescription / vaccination / unknown |
| `brief_description` | TEXT | 1–2 sentence summary |
| `language` | TEXT | ru / en / mixed |

### `lab_events` — one row per lab visit
| Column | Type |
|--------|------|
| `id` | INTEGER PK |
| `file_sha1` | TEXT FK |
| `collection_date`, `patient_full_name`, `patient_dob` | TEXT |
| `laboratory_name`, `laboratory_address`, `laboratory_city` | TEXT |
| `ordering_doctor`, `performing_doctor`, `report_date` | TEXT |
| `total_indicators_count` | INTEGER |
| `notes` | TEXT |

### `lab_indicators` — one row per test result
| Column | Type |
|--------|------|
| `id` | INTEGER PK |
| `lab_event_id` | INTEGER FK |
| `file_sha1` | TEXT FK |
| `collection_date` | TEXT |
| `indicator_name` | TEXT | original language |
| `indicator_name_en` | TEXT | English translation |
| `value_raw` | TEXT | e.g. `">5.0"`, `"not detected"` |
| `value_numeric` | REAL | null if not parseable |
| `unit` | TEXT |
| `ref_range_low`, `ref_range_high` | REAL |
| `ref_range_text` | TEXT | as printed |
| `ref_range_notes` | TEXT |
| `range_status` | TEXT | normal / above / below / null |
| `flag` | TEXT | H / L / ! / * |
| `method`, `other_notes` | TEXT |

### `doctor_visits`
`visit_date`, `doctor_full_name`, `doctor_specialty`, `clinic_name`, `clinic_address`,
`chief_complaint`, `anamnesis`, `objective_findings`, `diagnosis_main`, `diagnosis_icd`,
`diagnosis_secondary`, `treatment_plan`, `medications_prescribed`, `recommendations`,
`next_visit_date`, `notes`

### `imaging_studies`
`study_date`, `study_type` (MRI/CT/ultrasound/X-ray/ECG/EEG/endoscopy/…),
`body_region`, `referring_doctor`, `performing_doctor`, `radiologist`, `clinic_name`,
`equipment_model`, `protocol`, `contrast_used`, `description`, `conclusion`,
`recommendations`, `notes`

### `discharge_summaries`
`admission_date`, `discharge_date`, `ward`, `hospital_name`, `attending_doctor`,
`admission_reason`, `diagnosis_on_admission`, `diagnosis_on_discharge`, `diagnosis_icd`,
`procedures_performed`, `treatment_summary`, `discharge_condition`, `discharge_medications`,
`follow_up_instructions`, `notes`

### `prescriptions`
`prescription_date`, `doctor_full_name`, `doctor_specialty`, `clinic_name`,
`medication_name`, `dosage`, `form`, `frequency`, `duration`, `instructions`,
`prescription_number`, `notes`

### `vaccinations`
`vaccination_date`, `vaccine_name`, `disease_targeted`, `manufacturer`, `batch_number`,
`dose_number`, `clinic_name`, `administering_doctor`, `next_dose_date`, `notes`

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Bulk mode only | Not needed when Claude processes PDFs in-session |
| `BASE_PATH` | Optional | Default archive path (can also pass `--base-path` per command) |

Copy `.env.example` to `.env` and fill in values. The scripts read `.env` automatically.

---

## Error Handling

- **Duplicate**: `{"status": "duplicate", "imported_on": "..."}` — no files modified.
- **No text in PDF**: status `error`, message about corrupted/scanned file.
- **API failure**: status `error` with API error message. Retry or check `ANTHROPIC_API_KEY`.
- **errors.log**: one line per failure: `<timestamp> | <sha1> | <filename> | <error>`
- **Unknown document type**: `document_type = "unknown"` in DB; ask user to clarify.

**Common fixes:**

| Error | Fix |
|-------|-----|
| `ANTHROPIC_API_KEY not set` | Add key to `.env` |
| `No text extracted` | Re-export PDF or scan at higher resolution |
| `Module not found: pdfplumber` | `pip install pdfplumber` |
| `Database not found` | Run `python scripts/init_db.py --base-path <path>` |

---

## Privacy & Security

- **Local storage**: all files and extracted data are stored on the user's machine
  in the folder they specify. Nothing is sent anywhere automatically.
- **Anthropic API**: in bulk/folder mode, PDF text is sent to the Anthropic API for extraction.
  In in-session mode, the PDF is processed within the current Claude Code session only — no
  separate API call is made. Both modes are subject to
  [Anthropic's privacy policy](https://www.anthropic.com/privacy).
  Do not use this skill for third-party medical records without the subject's informed consent.
- **No cloud sync**: original_files, json_extractions, and structured_database stay local
  unless the user explicitly uses a cloud connector.
- **Backup**: this skill does not back up data. Set up your own backup of BASE_PATH.

---

## Installation

```bash
# One-line install (no git required)
curl -fsSL https://raw.githubusercontent.com/romahakov/personal-health-record/main/install.sh | bash

# Or with git
git clone https://github.com/romahakov/personal-health-record \
  ~/.claude/skills/personal-health-record
pip install -r ~/.claude/skills/personal-health-record/requirements.txt
```

---

## Proactive Detection

### After analyzing a medical PDF in chat

When Claude receives one or more medical PDFs in chat, it should:
1. compute SHA-1 for all uploaded files
2. check which ones already exist in the archive
3. tell the user that duplicate/new status before any extraction
4. extract only the files that are not already archived
5. save those newly extracted files immediately
6. return a detailed per-file report including SHA-1 and extracted contents

**If BASE_PATH is not set up yet:**
> "You haven't set up an archive yet. It only takes a second — where should I
> store your medical files? (e.g. `/Users/roman/medical-archive`)"
> Then proceed with hashing, deduplication, extraction, and saving.

### User uploads a PDF without asking for analysis

> "I see you've uploaded what looks like a medical document. Would you like me to
> first check whether it's already in your archive, and then process and save only the files that are not already there through your Personal Health Record skill? It'll be saved to your archive
> and you can reference it in future questions without re-uploading.
> If you haven't set up the skill yet, I can do that now — it only takes a minute."
