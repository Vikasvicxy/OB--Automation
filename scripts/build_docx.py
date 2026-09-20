#!/usr/bin/env python3
"""Build docs/TeamHR_Automation_Project_Documentation.docx

Professional, editable Word documentation for the TeamHR Automation project.

Regenerate after doc changes:
    pip install python-docx
    python scripts/build_docx.py

No candidate PII is used anywhere in this document (synthetic examples only).
"""

from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "TeamHR_Automation_Project_Documentation.docx"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def add_field(paragraph, instr):
    """Insert a Word field (TOC, PAGE, …) into a paragraph."""
    run = paragraph.add_run()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), instr)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "—"
    r.append(t)
    fld.append(r)
    run._r.addprevious(fld)


def set_font_style(doc, name="Calibri", size=11):
    doc.styles["Normal"].font.name = name
    doc.styles["Normal"].font.size = Pt(size)


def code_block(doc, text):
    """Monospaced preformatted block."""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.2)
    p.paragraph_format.space_after = Pt(6)
    for i, line in enumerate(text.rstrip().split("\n")):
        if i:
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Inches(0.2)
            p.paragraph_format.space_after = Pt(0)
        run = p.add_run(line)
        run.font.name = "Consolas"
        run.font.size = Pt(9)
        # force east-asian font mapping for monospace
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.append(rfonts)
        rfonts.set(qn("w:ascii"), "Consolas")
        rfonts.set(qn("w:hAnsi"), "Consolas")


def h1(doc, text):
    doc.add_heading(text, level=1)


def h2(doc, text):
    doc.add_heading(text, level=2)


def h3(doc, text):
    doc.add_heading(text, level=3)


def table(doc, rows, header=True, widths=None):
    t = doc.add_table(rows=len(rows), cols=len(rows[0]))
    t.style = "Light Grid Accent 1"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            cell = t.cell(ri, ci)
            cell.text = str(val)
            for p in cell.paragraphs:
                p.style = doc.styles["Normal"]
                for run in p.runs:
                    run.font.size = Pt(10)
                    if header and ri == 0:
                        run.font.bold = True
    if widths:
        for ci, w in enumerate(widths):
            for ri in range(len(rows)):
                t.cell(ri, ci).width = Inches(w)
    doc.add_paragraph()
    return t


def bullets(doc, items):
    for it in items:
        doc.add_paragraph(it, style="List Bullet")


def page_number_footer(section):
    footer = section.footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("TeamHR Automation  ·  Project Documentation  ·  Page ")
    run.font.size = Pt(9)
    add_field(p, "PAGE")


def doc_control_header(section):
    header = section.header
    p = header.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = p.add_run("TEAMHR AUTOMATION — CONFIDENTIAL TO TEAM")
    run.font.size = Pt(9)
    run.font.color.rgb = None


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build() -> None:
    doc = Document()
    set_font_style(doc)

    # ---- cover / title ----
    for _ in range(6):
        doc.add_paragraph()
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = t.add_run("TEAMHR AUTOMATION")
    r.font.size = Pt(34)
    r.font.bold = True
    st = doc.add_paragraph()
    st.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = st.add_run("Recruitment & Onboarding Automation System")
    r.font.size = Pt(18)
    r.font.italic = True
    doc.add_paragraph()
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run("Project Documentation")
    r.font.size = Pt(15)
    for _ in range(8):
        doc.add_paragraph()
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run(
        "Version 1.0.0  ·  Tag teamhr-final-v1.0.0\n"
        "Final archive pass before development VM retirement\n"
        "20 September 2026"
    ).font.size = Pt(12)
    doc.add_page_break()

    # ---- header / footer on the active section ----
    section = doc.sections[0]
    doc_control_header(section)
    page_number_footer(section)

    # ---- table of contents ----
    h1(doc, "Table of Contents")
    p = doc.add_paragraph()
    add_field(p, 'TOC \\o "1-2" \\h \\z \\u')
    doc.add_page_break()

    # 1. Document Control
    h1(doc, "1. Document Control")
    table(doc, [
        ["Property", "Value"],
        ["Document title", "TEAMHR AUTOMATION — Recruitment & Onboarding Automation System — Project Documentation"],
        ["Version", "1.0.0"],
        ["Status", "Final"],
        ["Repository", "https://github.com/Vikasvicxy/OB--Automation.git"],
        ["Stable branch", "master"],
        ["Final tag", "teamhr-final-v1.0.0"],
        ["Recommended Python", "3.12 (3.10–3.13 expected to work)"],
        ["Type", "Full project documentation (editable Word document)"],
        ["Sensitive data", "None — synthetic examples only, no candidate PII"],
    ])

    # 2. Executive Summary
    h1(doc, "2. Executive Summary")
    doc.add_paragraph(
        "TeamHR Automation is a local recruitment and onboarding automation tool used by a "
        "recruitment team to convert paper-based and WhatsApp-based candidate information into "
        "clean, validated onboarding workbooks for a logistics delivery workforce. "
    )
    doc.add_paragraph(
        "The application runs entirely on a single Windows work computer. A recruiter pastes or "
        "uploads a candidate's Aadhaar card and WhatsApp chat/document; the application reads the "
        "documents with local OCR, extracts the required fields (name, mobile, DOB, gender, "
        "Aadhaar, father's name, address, PIN, facility, location, role, salary, DOJ, cost code), "
        "and presents them for review. The recruiter verifies or corrects each field, then saves "
        "the candidate as a draft or approves them. Approved candidates are written into a single "
        "Excel workbook with two sheets — OB Format and Mail Format — ready for the downstream "
        "business process. A Copy Details block copies the six personal fields for direct "
        "TAB-separated paste into other tools."
    )
    doc.add_paragraph(
        "External actions are deliberately disabled by default: no live eSampark upload, no email, "
        "WhatsApp, SMS, or voice messages are sent unless an operator explicitly enables the "
        "corresponding safety flags. Candidate PII is minimised by design: full Aadhaar and full "
        "address appear only where operationally required."
    )

    # 3. Problem Statement
    h1(doc, "3. Problem Statement")
    doc.add_paragraph(
        "The onboarding process depends on typing candidate data from photographs and WhatsApp "
        "chats into spreadsheets by hand. This is slow, error-prone, and inconsistent: fields are "
        "missed, cost codes are guessed, facility names are written informally, and the final "
        "workbook must be assembled manually. The team needs a faster, safer way to turn candidate "
        "documents into validated workbooks without relying on cloud services (which the process "
        "and company constraints do not permit at runtime)."
    )

    # 4. Current Manual Recruitment Process
    h1(doc, "4. Current Manual Recruitment Process")
    doc.add_paragraph("Before automation, the typical flow for each candidate was:")
    bullets(doc, [
        "Collect the Aadhaar card and a WhatsApp message/chat containing name, mobile, salary and "
        "location.",
        "Read the document by eye and retype name, mobile, DOB, gender, Aadhaar, father's name, "
        "address, PIN and other fields.",
        "Look up the correct facility and cost code from a master list (Flipkart/Myntra, "
        "LM/FM).",
        "Manually type the candidate into the onboarding Excel template (OB Format) and again into "
        "the mail-format spreadsheet.",
        "Repeat the identical personal fields across multiple sheets — a common source of "
        "transcription mismatches.",
    ])

    # 5. Project Objectives
    h1(doc, "5. Project Objectives")
    bullets(doc, [
        "Extract candidate fields from Aadhaar + WhatsApp documents automatically (local OCR).",
        "Give the recruiter a fast review surface with explicit Save Draft / Save & Approve.",
        "Derive cost codes and facilities from authoritative masters, never guess.",
        "Generate the onboarding workbook (OB Format + Mail Format) in one step.",
        "Keep the tool fully offline and safe: no accidental external sends, no PII leakage.",
        "Be deployable on a plain Windows work PC with a short, documented setup.",
    ])

    # 6. Scope
    h1(doc, "6. Scope")
    doc.add_paragraph("In scope: one-operator local tool; Aadhaar + WhatsApp-style document "
        "input; field extraction, review and persistence; facility/role master integration; "
        "two-sheet workbook generation; Copy Details; admin master management; backup/restore; "
        "health/release readiness; eSampark automation scaffolding (disabled by default).")
    doc.add_paragraph("Explicitly out of scope: multi-user authentication/SSO, cloud OCR, "
        "automatic live upload to eSampark, automatic emails/WhatsApp/SMS/voice, mobile "
        "applications, and a server-side production deployment.")

    # 7. High-Level Architecture
    h1(doc, "7. High-Level Architecture")
    code_block(doc, """Operator's browser
   |  HTTP (127.0.0.1)
   v
FastAPI (app/main.py)  - pages + /api/* routes
   |  Jinja2 templates + vanilla JS
   v
Resolver / rules layer (app/rules.py)  - cost codes, roles, normalisation
   |  evidence-scored fields
   v
OCR / parser (app/ocr.py, evidence.py, document_parsers/aadhaar.py)  - local only
   |
   v
SQLite (app/database.py)  - candidates, drafts, batches, audit
   |
   v
Excel generation (app/generation.py)  - OB Format + Mail Format workbook""")
    doc.add_paragraph("The optional eSampark Playwright module and the communication provider "
        "layer (email/WhatsApp/SMS/voice) are architecture islands that stay disabled by "
        "default (see sections 28 and 29).")

    # 8. Technology Stack
    h1(doc, "8. Technology Stack")
    table(doc, [
        ["Layer", "Technology"],
        ["Language", "Python 3.12 (3.10–3.13 tested range)"],
        ["Web framework", "FastAPI 0.115 + Uvicorn"],
        ["Templating", "Jinja2 3.1 (server-rendered)"],
        ["Frontend", "Vanilla HTML/CSS/JavaScript (no framework)"],
        ["Database", "SQLite (stdlib sqlite3, auto-initialised)"],
        ["OCR", "RapidOCR (ONNX Runtime), layout-aware Aadhaar parser, PyMuPDF, Pillow"],
        ["Excel", "openpyxl 3.1"],
        ["Fuzzy matching", "rapidfuzz"],
        ["Browser automation", "Playwright (Chromium) — optional, portal only"],
        ["Testing", "pytest-compatible standalone scripts; Node.js for the JS harness"],
    ])

    # 9. Folder Structure
    h1(doc, "9. Folder Structure")
    code_block(doc, """OB--Automation/
  app/                   application source
    main.py              FastAPI entry point + all routes
    rules.py             business rules / normalisation / resolver
    master_data.py       Excel master loading + effective view
    generation.py        two-sheet workbook generation
    database.py          SQLite schema + CRUD
    ocr.py, evidence.py, document_parsers/aadhaar.py   OCR pipeline
    portal/              eSampark Playwright automation (disabled)
    templates/           Jinja2 pages
    static/              CSS / JS
  data/
    masters/             HubName.xlsx, Designation_Master.xlsx, Facility_Master.xlsx
    templates/           Excel Generation.xlsx  (OB Format + Mail Format)
    database|generated|backups|logs|portal/   runtime (created, not committed)
  scripts/               Windows setup/start/stop + dev tooling
  tests/                 test suite (standalone scripts + pytest-compatible)
  docs/                  this documentation set
  .github/workflows/     CI + release workflows
  requirements.txt, README.md, CHANGELOG.md, .env.example, .gitignore""")

    # 10. New Onboarding Workflow
    h1(doc, "10. New Onboarding Workflow")
    doc.add_paragraph("The New Onboarding (Smart Upload) page takes the candidate from "
        "documents to an approved record:")
    table(doc, [
        ["Step", "Action"],
        ["1", "Paste or upload Aadhaar + WhatsApp document(s)"],
        ["2", "Local OCR extracts fields and produces a review payload"],
        ["3", "Recruiter reviews: Name, Mobile, DOB, Gender, Aadhaar, Father Name, Address, PIN, Facility, Location, Role, Salary, DOJ, Cost Code, Facility Type"],
        ["4", "Facility/Role searchable dropdowns over the complete masters"],
        ["5", "Copy Details (optional) — six fields, TAB-separated"],
        ["6", "Save Draft (resume later) or Save & Approve"],
        ["7", "Resume / Discard a stale draft"],
        ["8", "Multi-candidate session: approve several, then Generate Excel"],
    ])

    # 11. OCR Architecture
    h1(doc, "11. OCR Architecture")
    doc.add_paragraph(
        "All OCR runs locally. Files are routed through RapidOCR (ONNX Runtime) which returns "
        "text lines with bounding-box geometry and confidence. The layout-aware Aadhaar parser "
        "uses those boxes to interpret sections of the card. Extracted candidate values carry "
        "evidence (multiple candidate lines, sources) scored by the evidence module, which applies "
        "confidence thresholds and margins before auto-selecting a value. Anything below the "
        "threshold is left for the operator to review."
    )
    code_block(doc, """documents → RapidOCR → OCRLine(text, bbox, confidence, page)
          → document classification (Aadhaar vs chat/screenshot)
          → field extractors (name, DOB, Aadhaar, address, mobile, salary, role, hub…)
          → evidence scorer (candidates + confidence + margin)
          → rule resolver / normalisation → review payload → operator review""")

    # 12. Aadhaar Parsing
    h1(doc, "12. Aadhaar Parsing")
    doc.add_paragraph(
        "The Aadhaar parser is layout-aware: it recognises the header (Government of India / "
        "UIDAI), the name block, DOB label, gender, relation marker (S/O, D/O, W/O, C/O), the "
        "address block up to the postal boundary, the PIN, and the 12-digit Aadhaar number. "
        "Aadhaar is normalised to 12 digits only and rejected when the length is wrong."
    )
    bullets(doc, [
        "Aadhaar normalisation: '1234 5678 9012' and '1234-5678-9012' → '123456789012'.",
        "PIN: exactly six digits; kept as text.",
        "Gender: Male / Female / Transgender only; OCR artifacts collapsed.",
        "Address: single-line collapse for Copy Details; ends at the PIN (postal boundary); "
        "never contains the Aadhaar number or issue date.",
    ])

    # 13. Manual Review & Confidence Logic
    h1(doc, "13. Manual Review & Confidence Logic")
    doc.add_paragraph(
        "Every extracted field is a proposal, never a silent final answer. The evidence scorer "
        "computes confidence and margin; a field is auto-selected only when its confidence "
        "exceeds the auto-select threshold for that field type (e.g. DOB 80, Aadhaar 90). "
        "Otherwise the field is marked for Review. The operator edits freely; manual, "
        "user-reviewed values always take precedence over OCR. Approve (Save & Approve) runs full "
        "validation and produces inline errors instead of saving bad data."
    )
    table(doc, [
        ["Confidence level", "Meaning"],
        ["High", "Auto-selected with wide margin — operator simply verifies"],
        ["Review", "Below threshold or conflicting evidence — operator decides"],
        ["Missing", "Nothing confident found — operator supplies the value"],
        ["Conflict", "Strongly competing values — operator resolves"],
    ])

    # 14. Facility / Hub Master
    h1(doc, "14. Facility / Hub Master")
    doc.add_paragraph(
        "The facility master (HubName.xlsx) is the single source of truth for facilities. The "
        "searchable facility dropdown browses the complete master and is never pre-filtered by "
        "the current LM/FM inference, so an LM-inferred candidate can still pick an FM (_PL) hub "
        "and vice versa. A manual facility selection overrides stale OCR inference and refreshes "
        "location, cost code, facility type and the role list."
    )

    # 15. HubName.xlsx Column Mapping
    h1(doc, "15. HubName.xlsx Column Mapping")
    table(doc, [
        ["Column", "Header", "Meaning", "Example"],
        ["A", "FACILITY", "System/reference value", "BLR/NLM (NelamangalaHub_BLR)"],
        ["B", "LOCATION", "Branch code (used as Location/Branch in workbooks)", "BLR/NLM"],
        ["C", "FACILITY NAME", "Display/search label", "NelamangalaHub_BLR"],
    ])
    doc.add_paragraph(
        "When Column C is blank, the display falls back to the readable name inside Column A's "
        "parentheses, then Column B, then Column A. A blank Column C never drops the row. "
        "The Mail Format 'Branch' uses Column B (LOCATION)."
    )

    # 16. LM / FM Rules
    h1(doc, "16. LM / FM Rules")
    doc.add_paragraph("Cost codes drive everything downstream (Team, Facility Type, role list):")
    table(doc, [
        ["Cost code", "Entity / Operation", "Facility type"],
        ["4421", "Flipkart Last Mile", "Delivery Hub"],
        ["4441", "Flipkart First Mile", "Pickup Hub"],
        ["8751", "Myntra Last Mile", "Delivery Hub"],
        ["8752", "Myntra First Mile", "Needs Review (no hub master yet)"],
    ])
    doc.add_paragraph(
        "Cost code is derived from the master data, not guessed from OCR text: facility "
        "containing 'MYNTRA' → 8751; otherwise a '_PL' or 'PICKUP' reference → 4441; otherwise "
        "4421. A manual facility selection always overrides any stale OCR LM/FM inference. "
        "8752 has no production hub master; its Facility Type is not auto-assigned."
    )

    # 17. Cost Code Rules
    h1(doc, "17. Cost Code Rules")
    doc.add_paragraph(
        "Cost code is assigned only after the facility is resolved against the master. The "
        "resolver returns entity, operation, team and facility type together. Entity and "
        "operation never cross-merge (Flipkart ↔ Myntra and LM ↔ FM stay separate in role lists, "
        "facility lists and classification). Cost-code refresh is automatic when the facility is "
        "picked and when masters are reloaded."
    )

    # 18. Role / Designation Master
    h1(doc, "18. Role / Designation Master")
    doc.add_paragraph(
        "The official role lists come from Designation_Master.xlsx (DESIGNATION / COST CODE / "
        "PREFIX), merged with admin-managed roles held in SQLite. Role aliases (biker, delivery, "
        "sort, team lead, prexo…) resolve only to official designations valid for the selected "
        "cost code — never to invented names. Manual role selection always wins over OCR."
    )
    table(doc, [
        ["Designation", "Cost codes"],
        ["LM - Delivery Executive", "4421, 8751"],
        ["LM - Sorter", "4421, 8751"],
        ["LM - Team Leader", "4421, 8751"],
        ["LM - Prexo Delivery Executive", "4421 only (not 8751)"],
        ["FM - Delivery Executive / Sorter / Team Leader", "4441, 8752"],
    ])

    # 19. Salary Normalization
    h1(doc, "19. Salary Normalization")
    doc.add_paragraph("Salary is normalised from common shorthand to a numeric monthly value:")
    table(doc, [
        ["Input", "Normalised"],
        ["18k", "18000"],
        ["18.5k", "18500"],
        ["30k", "30000"],
        ["invalid / missing", "left for review (needs_attention)"],
    ])

    # 20. Data Validation
    h1(doc, "20. Data Validation")
    bullets(doc, [
        "Aadhaar: exactly 12 digits, digits only, kept as text.",
        "Mobile: validated and normalised; duplicate detection across the whole database.",
        "PIN: exactly 6 digits, kept as text.",
        "DOB / DOJ: parse DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, DD.MM.YYYY; stored YYYY-MM-DD; "
        "displayed DD/MM/YYYY.",
        "Gender: Male / Female / Transgender only.",
        "Required generation fields: any Ready candidate missing a Mail-format required field "
        "blocks generation rather than guessing.",
        "A validation engine (90-case set A/B/C) resolves through production resolvers.",
    ])

    # 21. Candidate Persistence
    h1(doc, "21. Candidate Persistence")
    doc.add_paragraph(
        "Candidates persist in SQLite with a lifecycle: draft → needs_attention / needs_review → "
        "ready → generated, plus portal status columns. Drafts survive navigation and reload and "
        "can be resumed or discarded. Every state change and edit is recorded in an audit "
        "timeline; the edit history intentionally records sensitive fields (Aadhaar, address) as "
        "'updated' markers rather than values."
    )

    # 22. SQLite Database
    h1(doc, "22. SQLite Database")
    doc.add_paragraph(
        "A single SQLite file (data/database/teamhr.db) stores candidates, batches, generated "
        "files, daily master, documents, events, drafts, admin master overrides and history, "
        "portal uploads/audit, live uploads, validation runs, UAT runs and results, follow-ups, "
        "issues, notifications, outbox and system events. The database auto-initialises on first "
        "start; indexes exist on frequently queried columns."
    )

    # 23. Excel Generation
    h1(doc, "23. Excel Generation")
    doc.add_paragraph(
        "Generation opens a copy of the template (data/templates/Excel Generation.xlsx), clears "
        "any sample rows, writes only Ready candidates, and saves a new timestamped workbook "
        "under data/generated. The template is never modified. Full Aadhaar appears only in the "
        "Mail Format sheet; the daily-master export uses a masked Aadhaar."
    )

    # 24. OB Format Sheet
    h1(doc, "24. OB Format Sheet")
    doc.add_paragraph("13 columns, one row per approved candidate:")
    code_block(doc, "Sl No, Name*, Mobile Number*, Team*, Cost Code*, Facility Type*, Line of Business*, Sub Type*, Role - Designation*, Fixed Net Take Home*, State*, Facility*, Contractor*")
    doc.add_paragraph("Fixed values: Team = LAST MILE / FIRST MILE - OPERATIONS by cost code; "
        "Line of Business / Sub Type = EKART; State = KARNATAKA; Contracted = TEAM HR GSA "
        "PRIVATE LIMITED; Facility = the master's LOCATION code; Sl No = row order.")

    # 25. Mail Format Sheet
    h1(doc, "25. Mail Format Sheet")
    doc.add_paragraph("14 columns, aligned with the OB Format rows for the same candidates:")
    code_block(doc, "Date of Joining, Name, Mobile No, Designation, Branch, Vertical, State, Net Salary, Aadhar No, DOB, Fathers Name, Address, Pin Code, Gender")
    doc.add_paragraph("Branch comes from the master LOCATION column; Vertical is the facility "
        "display name; State = Karnataka; full Aadhaar appears only here.")

    # 26. Copy Details Feature
    h1(doc, "26. Copy Details Feature")
    doc.add_paragraph("The New Onboarding page exposes two buttons that copy the current "
        "(reviewed, edited) values for the six personal fields in this exact TAB-separated "
        "order:")
    code_block(doc, "Aadhar No    DOB    Fathers Name    Address    Pin Code    Gender")
    doc.add_paragraph("Copy Details copies the value row; Copy With Headers prefixes the label "
        "row. DOB is formatted DD/MM/YYYY; Aadhaar is digits-only; the address is collapsed to a "
        "single line. Pasting directly into Excel fills one row. The order is enforced by the "
        "JavaScript COPY_FIELD_DEFS and verified by the Node-based test harness.")

    # 27. Multi-Candidate Workflow
    h1(doc, "27. Multi-Candidate Workflow")
    doc.add_paragraph(
        "A session tracks how many candidates were approved. The recruiter processes several "
        "candidates, then runs a single Generate for the current batch. Only Ready candidates are "
        "included; Draft and Needs Attention candidates are listed in the generate dialog "
        "instead of being silently skipped."
    )

    # 28. eSampark Automation Architecture
    h1(doc, "28. eSampark Automation Architecture")
    doc.add_paragraph(
        "The portal module (app/portal/) wraps Playwright automation for the eSampark portal: "
        "LDAP login (credentials from .env only), navigation, workbook upload, upload-history "
        "polling, and result parsing that updates candidate portal status. The module exists and "
        "is tested with mocks, but real uploads are disabled by default and require two safety "
        "flags."
    )

    # 29. Safety Flags / Disabled External Actions
    h1(doc, "29. Safety Flags / Disabled External Actions")
    table(doc, [
        ["Flag", "Default", "Effect"],
        ["REAL_UPLOAD_ENABLED", "false", "Master switch for eSampark uploads"],
        ["ESAMPARK_LIVE_TEST_MODE", "false", "Live-test upload mode"],
        ["COMMUNICATION_ENABLED", "false", "Master switch for all messaging"],
        ["EMAIL_ENABLED / WHATSAPP_ENABLED / SMS_ENABLED / VOICE_ENABLED", "false", "Per-channel switches"],
        ["ADMIN_FEATURE_ENABLED", "true", "Admin master management"],
    ])
    doc.add_paragraph("Live upload requires BOTH REAL_UPLOAD_ENABLED and "
        "ESAMPARK_LIVE_TEST_MODE = true. Credentials come only from the environment. "
        "No email, WhatsApp, SMS, or voice messages are sent unless explicitly enabled.")

    # 30. Admin Master Management
    h1(doc, "30. Admin Master Management")
    doc.add_paragraph(
        "The Admin / Masters page manages facilities, roles and aliases with deactivate/activate, "
        "import preview → apply, and a change-history audit. Admin records live in SQLite and "
        "override the Excel masters in the effective view; the Excel files themselves are never "
        "rewritten at runtime."
    )

    # 31. Security & PII Handling
    h1(doc, "31. Security & PII Handling")
    bullets(doc, [
        "Full Aadhaar and full address appear only on the candidate detail page and in the Mail "
        "Format sheet; masked or omitted everywhere else.",
        "Search, pipeline, data-quality drill-downs, exports and logs never include full Aadhaar "
        "or address.",
        "Logging redacts auth/token/cookie/password/secret/api-key values.",
        "Backups use an allow-list and exclude raw Aadhaar images, browser state, logs, caches "
        "and secrets; restore re-asserts safety flags off.",
        "SQL is parameterised; upload paths prevent traversal; downloads are digest-checked.",
        "No secrets, tokens or keys are committed; .env is gitignored.",
    ])

    # 32. Testing Strategy
    h1(doc, "32. Testing Strategy")
    doc.add_paragraph(
        "The suite (~380 tests) is self-contained: every test uses a throwaway database and "
        "isolated config, and never touches production data. Groups cover OCR/Aadhaar parsing, "
        "facility dropdown + HubName mapping, role dropdown, salary/PIN, review/persistence, "
        "Excel generation, security/path traversal/PII, template smoke, portal and live-upload "
        "safety, admin masters, validation cases, and UI foundation incl. JS syntax. A Node-based "
        "harness verifies the Copy Details exact order. See docs/TESTING.md for commands.")
    code_block(doc, """python -m pytest tests/ --ignore=tests/benchmark_ocr.py -q
# baseline: 378 passed in ~2.6 minutes (Linux, Python 3.12, Node 20)""")

    # 33. Validation / Blind Tests
    h1(doc, "33. Validation / Blind Tests")
    doc.add_paragraph(
        "The validation engine defines ~90 curated cases (sets A/B/C) that resolve through the "
        "production resolvers and are used to spot regressions and to sanity-check master data "
        "changes. UAT provides a catalog of manual acceptance cases with run tracking and "
        "export."
    )

    # 34. Windows Installation
    h1(doc, "34. Windows Installation")
    code_block(doc, """git clone https://github.com/Vikasvicxy/OB--Automation.git
cd OB--Automation
python -m venv venv
.\\venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000""")
    doc.add_paragraph("Or use .\\scripts\\setup_windows.ps1 once, then "
        ".\\scripts\\Start-TeamHR.bat. Page references: docs/WINDOWS_QUICK_START.md, "
        "docs/INSTALL_WINDOWS.md.")

    # 35. Linux Development
    h1(doc, "35. Linux Development")
    code_block(doc, """python3.12 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000""")

    # 36. Troubleshooting
    h1(doc, "36. Troubleshooting")
    table(doc, [
        ["Issue", "Fix"],
        ["Port 8000 busy", "Get-NetTCPConnection -LocalPort 8000 → kill PID; or --port 8001"],
        ["PowerShell blocks scripts", "Set-ExecutionPolicy RemoteSigned -Scope CurrentUser"],
        ["pip / NativeCommandError", "always use python -m pip (venv-bound)"],
        ["Buttons do nothing", "F12 console; hard refresh Ctrl+F5"],
        ["Stale draft", "Resume or Discard"],
        ["Wrong hub", "manual Facility selection overrides OCR"],
        ["Missing masters/template", "restore the tracked runtime assets"],
        ["OCR wrong", "manual review takes precedence"],
        ["Excel generation fails", "verify template, Ready candidates, output folder"],
    ])

    # 37. Git / GitHub Workflow
    h1(doc, "37. Git / GitHub Workflow")
    bullets(doc, [
        "Single protected branch: master (always green).",
        "Feature work on feature/*, bug fixes on fix/*, then a pull request.",
        "CI runs on every push/PR (Linux + Windows, Python 3.12).",
        "Commit style: imperative prefixes (feat:/fix:/docs:/test:/chore:/refactor:).",
        "Version with annotated semver tags; record changes in CHANGELOG.md.",
        "Never commit .env, databases, generated workbooks, backups, logs, candidate documents "
        "or browser state.",
    ])

    # 38. CI/CD Design
    h1(doc, "38. CI/CD Design")
    doc.add_paragraph(
        "CI (ci.yml) validates every push/PR on ubuntu-latest and windows-latest without "
        "depending on the retired VM, credentials or candidate data: install pinned deps, import "
        "checks, fresh-DB + master/template loading, focused regression (facility incl. JS copy "
        "harness, persistence, Excel, security), then the full cross-platform suite. The release "
        "workflow (release.yml, manual) runs tests and publishes a clean source archive as a "
        "GitHub Release. Future CD means validated release packaging (source / Windows EXE) — "
        "not automatic eSampark submission. The Windows EXE build is a documented TODO; it is "
        "not claimed as supported until validated on a real Windows machine."
    )

    # 39. Future Windows EXE Packaging
    h1(doc, "39. Future Windows EXE Packaging")
    doc.add_paragraph(
        "Planned but not yet built: a PyInstaller one-file EXE bundling app/, data/masters/, "
        "data/templates/, templates and static assets. The build should run the full suite "
        "against the packaged app on a clean Windows VM before declaring EXE support. The "
        "packaging recipe is documented in the release workflow and docs/CI_CD.md."
    )

    # 40. Known Limitations
    h1(doc, "40. Known Limitations")
    bullets(doc, [
        "8752 (Myntra First Mile) has no hub master; facility type stays Needs Review.",
        "Single-operator tool — no user accounts or authentication.",
        "OCR quality depends on document image quality.",
        "Full Aadhaar/address intentionally limited to required surfaces.",
        "No validated Windows EXE yet; run from Python source.",
        "A pre-sanitization version of the generation template (with real sample rows) exists in "
        "early Git history — treated as disclosed, not rewritten.",
    ])

    # 41. Future Enhancements
    h1(doc, "41. Future Enhancements")
    bullets(doc, [
        "Validated Windows PyInstaller packaging.",
        "Optional authenticated admin mode.",
        "Additional entity/cost-code masters as the business evolves.",
        "Optional CI-native UI browser tests once reliably supported in CI.",
        "Better operator onboarding: sample data, guided tours, import wizards.",
    ])

    # 42. Operational Checklist
    h1(doc, "42. Operational Checklist")
    bullets(doc, [
        "[ ] Masters present: data/masters/HubName.xlsx, Designation_Master.xlsx.",
        "[ ] Template present: data/templates/Excel Generation.xlsx.",
        "[ ] Health page: no ERROR cards; OCR backend OK.",
        "[ ] Safety flags off in .env (or no .env).",
        "[ ] Facility dropdown searches the complete master.",
        "[ ] Copy Details produces the six TAB-separated fields.",
        "[ ] Approved candidates generate the OB + Mail workbook.",
        "[ ] Daily backup created from Backup page.",
    ])

    # 43. Final Handover Notes
    h1(doc, "43. Final Handover Notes")
    doc.add_paragraph(
        "This project is handed off in a documented, CI-ready state. Final tag "
        "teamhr-final-v1.0.0 is pushed to GitHub; origin/master is synchronized; the working "
        "tree is clean; the full test suite is green; a clean Windows source release (zip) has "
        "been validated with a fresh-install smoke test that has no dependency on the retired "
        "development VM. Begin with docs/FINAL_HANDOFF.md, then docs/WINDOWS_QUICK_START.md."
    )

    # ---- Appendices (reference material, no padding) ----------------------
    doc.add_page_break()
    h1(doc, "Appendix A — API / Route Surface")
    doc.add_paragraph("Grouped route inventory (all served by app/main.py). Page routes render "
        "Jinja2 templates; /api routes return JSON.")
    table(doc, [
        ["Group", "Representative routes"],
        ["Pages", "/, /smart-upload, /manual-entry, /candidates, /candidates/{id}, /batch-review, /batches/{id}, /pipeline, /data-quality, /reports, /settings, /backups, /health, /release, /admin/master-data, /validation, /uat, /generated-files, /portal, /live-upload, /debug/ocr"],
        ["Candidate APIs", "POST /api/save-draft, /api/confirm-candidate, /api/check-duplicate, /api/edit-candidate/{id}, /api/candidates/{id}/approve, /api/candidates/{id}/mark-review, /api/candidates/bulk-action, /api/drafts, /api/drafts/discard"],
        ["OCR / upload", "POST /api/smart-extract, /api/manual-ocr-extract, /api/upload-document, /api/documents, /api/evidence-score, /api/debug/ocr-annotate"],
        ["Masters", "GET /api/search-hubs, /api/location, /api/master-status, /api/reload-masters, /api/candidates/export-selected (PII-safe)"],
        ["Generation", "GET /api/generate-preview, POST /api/generate-excel, /api/generate-onboarding-pair, /api/generate-pairs, /api/generated, /api/generated-file/{id}/result|download"],
        ["Admin masters", "GET/POST /api/admin/* facilities, roles, history, export, import-preview, import-apply"],
        ["Backups", "POST /api/backups/create, /api/backups/verify, /api/backups/restore, /api/backups/open-folder"],
        ["Health / release", "GET /api/health (no secrets), /api/release/readiness"],
        ["Validation / UAT", "POST /api/validation/run, /api/validation/run-all, /api/validation/variants, /api/validation/export (PII-safe), POST /api/uat/run/start, /api/uat/result"],
        ["Portal / live upload", "GET /api/portal/*, POST /api/live-upload/confirm|submit (submit locked unless both safety flags)"],
        ["Ops / diagnostics", "/api/follow-ups*, /api/issues*, /api/communications*, /api/notifications*, /api/diagnostics/bundle|logs, /api/version"],
    ])

    doc.add_page_break()
    h1(doc, "Appendix B — Database Schema Overview")
    doc.add_paragraph("Single SQLite database (data/database/teamhr.db). Tables (representative):")
    table(doc, [
        ["Table", "Purpose"],
        ["candidates", "Core candidate records + status + portal status"],
        ["batches / batch_events", "Onboarding batches and their event timeline"],
        ["generated_files / generation_audit", "Generated workbooks and audit trail"],
        ["daily_master", "Daily operational master (masked Aadhaar)"],
        ["documents", "Uploaded document metadata (not content)"],
        ["candidate_events / candidate_edit_history", "Per-candidate audit; sensitive values as 'updated' markers"],
        ["candidate_drafts", "Resume/Discard drafts for Smart Upload / Manual Entry"],
        ["master_facilities / master_roles / master_role_cost_codes / master_role_aliases", "Admin-managed master overrides"],
        ["master_change_history / master_load_history", "Master change + reload audit"],
        ["portal_uploads / portal_audit / live_uploads", "eSampark / live-upload tracking"],
        ["manual_validation_runs / uat_runs / uat_results", "Validation and UAT records"],
        ["follow_ups / issues / notifications / communication_outbox", "Ops surfaces"],
        ["system_events / saved_views", "System events and saved filter views"],
    ])

    doc.add_page_break()
    h1(doc, "Appendix C — Environment Variables Reference")
    table(doc, [
        ["Variable", "Default", "Purpose"],
        ["APP_ENV", "development", "development / test / production"],
        ["APP_HOST / APP_PORT", "127.0.0.1 / 8000", "Bind address and port"],
        ["SECRET_KEY", "change-me-to-a-real-secret", "Session/signing key — set a real value"],
        ["OUTPUT_DIR", "output", "Default output directory"],
        ["REAL_UPLOAD_ENABLED", "false", "Master switch for eSampark uploads"],
        ["ESAMPARK_LIVE_TEST_MODE", "false", "Live-test upload mode"],
        ["COMMUNICATION_ENABLED", "false", "Master switch for messaging"],
        ["EMAIL_ENABLED / WHATSAPP_ENABLED / SMS_ENABLED / VOICE_ENABLED", "false", "Per-channel switches"],
        ["ADMIN_FEATURE_ENABLED", "true", "Admin master management"],
        ["DEMO_MODE", "false", "Demo mode (mock data)"],
        ["OCR_DEBUG_VIEW", "false", "OCR debug overlay"],
        ["GUPSHUP_API_KEY / GUPSHUP_APP_NAME", "placeholder", "WhatsApp/SMS provider (disabled)"],
        ["SMTP_HOST / SMTP_PORT / SMTP_USERNAME / SMTP_PASSWORD / EMAIL_FROM", "placeholder", "Email provider (disabled)"],
    ])
    doc.add_paragraph("Credentials are read from .env only, never from source code, the database, "
        "logs, or generated files.")

    doc.add_page_break()
    h1(doc, "Appendix D — Candidate Fields Reference")
    table(doc, [
        ["Field", "Source", "Validation / notes"],
        ["Name", "OCR / manual", "Required for generation"],
        ["Mobile", "OCR / manual", "Validated; duplicate detection across DB"],
        ["DOB", "OCR (DOB-labelled only)", "Issue Date never used; threshold 80 for auto-select; DD/MM/YYYY display"],
        ["Gender", "OCR", "Male / Female / Transgender only"],
        ["Aadhaar", "OCR", "Exactly 12 digits; full value only on detail page + Mail Format"],
        ["Father Name", "OCR", "Relation marker stripped; blank allowed"],
        ["Address", "OCR", "Postal boundary at PIN; collapsed for Copy Details"],
        ["PIN", "OCR", "Exactly six digits, text"],
        ["Facility", "Master + manual", "Overrides OCR LM/FM inference"],
        ["Location", "Derived from facility", "Master LOCATION column"],
        ["Role", "Master + manual", "Official designation per cost code; no Prexo under 8751"],
        ["Salary", "OCR / manual", "18k → 18000, 18.5k → 18500, 30k → 30000"],
        ["DOJ", "Manual", "Parsed to YYYY-MM-DD"],
        ["Cost Code", "Derived from facility", "4421 / 4441 / 8751 / 8752"],
        ["Facility Type", "Derived", "DELIVERY_HUB / PICKUP_HUB / Needs Review (8752)"],
    ])

    doc.add_page_break()
    h1(doc, "Appendix E — Copy Details Example (synthetic)")
    doc.add_paragraph("Values below are synthetic (not a real person); they demonstrate exactly "
        "what the Copy Details buttons produce.")
    code_block(doc, """Copy Details (value row, TAB-separated):
123456789012<TAB>05/06/2001<TAB>K. Rao<TAB>S/O: K. Rao, 12 MG Road, Bengaluru<TAB>560001<TAB>Female

Copy With Headers (header row + value row):
Aadhar No<TAB>DOB<TAB>Fathers Name<TAB>Address<TAB>Pin Code<TAB>Gender
123456789012<TAB>05/06/2001<TAB>K. Rao<TAB>S/O: K. Rao, 12 MG Road, Bengaluru<TAB>560001<TAB>Female""")
    doc.add_paragraph("Pasting into Excel fills one row per line. The order and separators are "
        "enforced in smart_upload.html (COPY_FIELD_DEFS) and verified by "
        "tests/_facility_js_harness.cjs.")

    doc.add_page_break()
    h1(doc, "Appendix F — Frequently Asked Questions")
    table(doc, [
        ["Question", "Answer"],
        ["Do I need internet/cloud?", "No. OCR, database and Excel generation run fully locally."],
        ["Can I use it on any Windows PC?", "Yes — Python 3.12, the repo/zip, and the setup steps are all you need."],
        ["Does it send messages or upload anywhere by itself?", "No. Uploads and all messaging are disabled by default and require safety flags."],
        ["Why is 8752 (Myntra FM) 'Needs Review'?", "No production hub master exists for it yet, so facility type is not auto-assigned."],
        ["Why does Prexo not appear for 8751?", "Business rule: Prexo Delivery Executive is not offered under Myntra LM."],
        ["I corrected a field; will OCR overwrite it?", "No — manual values always win; only unset fields auto-fill from OCR."],
        ["Where are generated files?", "data/generated/<date>/Onboarding_<timestamp>.xlsx."],
        ["What if I lose the database?", "Create backups from the Backup page; restore re-asserts safety flags off."],
        ["How do I keep it updated?", "git pull origin master, re-run pip install, restart."],
        ["Why run Windows instead of Linux?", "The operators' machines are Windows; the app is fully supported there and documented for it."],
    ])

    doc.add_page_break()
    h1(doc, "Appendix G — Test Results Snapshot (final pass)")
    table(doc, [
        ["Item", "Result"],
        ["Full suite (Linux, Python 3.12.3, Node 20)", "378 passed, ~2.6 minutes (excl. benchmark)"],
        ["Facility dropdown (incl. JS Copy Details harness)", "7/7 passed"],
        ["Excel generation pair", "passed (OB Format 13 cols / Mail Format 14 cols)"],
        ["Security / path traversal / PII", "passed incl. OS-independent traversal tests"],
        ["Template smoke + JS syntax", "passed (node --check clean)"],
        ["Fresh install smoke (clean dir, fresh venv)", "passed — no VM dependency"],
    ])
    doc.add_paragraph("Commands and detail: docs/TESTING.md. Results are informative and will "
        "vary by environment; CI runs the same suite on Linux + Windows.")

    doc.save(OUT)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()