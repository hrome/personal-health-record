# Personal Health Record — Claude Code Skill

A skill for [Claude Code](https://claude.ai/code) that helps you organize and analyze your personal medical documents: lab results, doctor visits, imaging studies (MRI, CT, ultrasound, ECG), discharge summaries, prescriptions, and vaccination records.

Supports **Russian and English** documents. Everything is stored locally on your machine.

---

## What it does

**Add documents** — attach a PDF in chat or point Claude to a folder. Claude reads and understands the document, extracts all structured data, and saves it to a local archive.

**Ask questions in plain language:**
- "What was my hemoglobin last January?"
- "Show all diagnoses from the past two years"
- "Have I ever been prescribed metformin?"
- "Show my cholesterol trend"
- "When did I last see a cardiologist?"

**Timeline and trends** — get a chronological view of your medical history or track how any lab indicator changes over time.

**Export** — export your data to CSV at any time.

---

## How it works

Every document goes through three layers stored in a folder of your choice:

```
your-archive/
├── original_files/       # original PDFs, stored unchanged
├── json_extractions/     # full structured extraction of each document
└── structured_database/
    └── medical.db        # SQLite database — fast queries across all documents
```

When you ask a question, Claude queries the database first. No re-reading PDFs on every question.

---

## Requirements

- [Claude Code](https://claude.ai/code) installed
- Python 3.10 or newer

---

## Installation

### Option 1 — One command (no git required)

Open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/romahakov/personal-health-record/main/install.sh | bash
```

The script downloads the skill, places it in the right folder, and installs dependencies automatically.

---

### Option 2 — Download ZIP

1. Click the green **Code** button at the top of this page
2. Choose **Download ZIP**
3. Unzip the downloaded file
4. Rename the folder from `personal-health-record-main` to `personal-health-record`
5. Move the folder to `~/.claude/skills/`
6. Open Terminal and run:
   ```bash
   pip install -r ~/.claude/skills/personal-health-record/requirements.txt
   ```

---

### Option 3 — Git clone

```bash
git clone https://github.com/romahakov/personal-health-record \
  ~/.claude/skills/personal-health-record

pip install -r ~/.claude/skills/personal-health-record/requirements.txt
```

Updates: `git pull` inside `~/.claude/skills/personal-health-record`.

---

## First use

Open Claude Code, start a new conversation, and say:

> "Set up my medical records archive"

Claude will ask where to store your archive and how you'd like to add files, then guide you through the rest.

---

## Adding documents

**In chat** — attach one or several PDFs directly in the message. Claude reads them and extracts all data within the same session. No API key needed for this mode.

**From a folder** — tell Claude the folder path:
> "Import all PDFs from ~/Downloads/medical-docs"

This mode calls the Anthropic API once per file and requires an API key (see [Configuration](#configuration)).

---

## Configuration

For **bulk folder imports** only, you need an Anthropic API key:

```bash
cp ~/.claude/skills/personal-health-record/.env.example \
   ~/.claude/skills/personal-health-record/.env
```

Open `.env` and set:
```
ANTHROPIC_API_KEY=sk-ant-...
```

When adding files one by one through the chat, no API key is needed.

---

## Supported document types

| Type | What gets extracted |
|------|---------------------|
| Lab results | Every indicator: name, value, unit, reference range, in/out of range flag |
| Doctor visits | Diagnosis, ICD code, treatment plan, prescriptions, recommendations |
| Imaging studies | MRI, CT, ultrasound, X-ray, ECG, EEG — description, conclusion |
| Discharge summaries | Admission/discharge dates, diagnoses, procedures, discharge medications |
| Prescriptions | Medication, dosage, frequency, duration |
| Vaccinations | Vaccine name, dose number, next dose date |

---

## Privacy

- All files and extracted data are stored **on your machine only**
- When adding files through the chat, no data leaves the Claude Code session
- When using bulk folder import, PDF text is sent to the Anthropic API — see [Anthropic's privacy policy](https://www.anthropic.com/privacy)
- Do not use this skill for other people's medical records without their consent

---

## License

MIT
