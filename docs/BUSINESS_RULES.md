# TeamHR Automation — Business Rules

The authoritative business rules implemented by TeamHR Automation. When the UI
and this document disagree, this document (and the code behind it) is the
source of truth.

---

## 1. Entity / Operation / Cost Codes

Four cost codes drive everything downstream:

| Cost Code | Entity | Operation | Prefix | Team (OB Format) | Facility Type |
|-----------|--------|-----------|--------|------------------|----------------|
| **4421** | Flipkart | Last Mile | LM | LAST MILE - OPERATIONS | Delivery Hub |
| **4441** | Flipkart | First Mile | FM | FIRST MILE - OPERATIONS | Pickup Hub |
| **8751** | Myntra | Last Mile | LM | LAST MILE - OPERATIONS | Delivery Hub |
| **8752** | Myntra | First Mile | FM | FIRST MILE - OPERATIONS | *Needs Review* |

- **8752 (Myntra First Mile)** has **no production hub master yet**; its
  Facility Type stays *Needs Review* and will not auto-assign.
- Entity and operation never cross-merge: Flipkart ↔ Myntra and LM ↔ FM are kept
  separate in role lists, facility lists, and classification.

## 2. Cost-Code Inference

Cost code is **not guessed from OCR text**. It is derived from the **master
data**:

1. Facility name / system reference contains **"MYNTRA"** → **8751** (Myntra LM).
2. Otherwise the facility system reference / name contains **`_PL`** or
   **"PICKUP"** → **4441** (Flipkart FM).
3. Otherwise → **4421** (Flipkart LM).

Rule 1 wins over rule 2. A manual facility selection always overrides any
stale OCR inference.

## 3. Facility Master (HubName.xlsx)

- Three columns: **FACILITY** (Column A, system reference, e.g.
  `BLR/NLM (NelamangalaHub_BLR)`), **LOCATION** (Column B, unique branch code,
  e.g. `BLR/NLM`), **FACILITY NAME** (Column C, display label).
- The **display name** is Column C; when blank it falls back to the readable
  name in parentheses inside Column A, then Column B, then Column A. A blank
  Column C **never drops the row**.
- Duplicate display names are shown once per row so the user can pick the exact
  master row.
- **Location/Branch in generated workbooks always comes from the master's
  LOCATION column** (never the facility display name).
- The facility search always searches the **complete master** (never
  pre-filtered by current cost-code inference), so an LM-inferred candidate can
  pick an FM `_PL` hub and vice versa.

## 4. Role / Designation Master

- The authoritative role list per cost code comes from
  **Designation_Master.xlsx** (merged with active admin-managed roles).
- **Prexo Delivery Executive is NOT offered for 8751 (Myntra LM)**.
- Role aliases (biker, delivery, sort, tl, team lead, prexo, …) only resolve to
  an **official** designation valid for the selected cost code — never to an
  invented name.
- Manual role selection always wins over OCR.

## 5. Excel Generation (Excel Generation.xlsx)

Two sheets generated from the SAME approved candidates:

- **OB Format** (13 columns): `Sl No, Name*, Mobile Number*, Team*, Cost Code*,
  Facility Type*, Line of Business*, Sub Type*, Role - Designation*,
  Fixed Net Take Home*, State*, Facility*, Contractor*`.
- **Mail Format** (14 columns): `Date of Joining, Name, Mobile No, Designation,
  Branch, Vertical, State, Net Salary, Aadhar No, DOB, Fathers Name, Address,
  Pin Code, Gender`.

Fixed values:

| Field | Value |
|-------|-------|
| Team | `LAST MILE - OPERATIONS` / `FIRST MILE - OPERATIONS` (by cost code) |
| Line of Business / Sub Type | `EKART` |
| State (OB Format) | `KARNATAKA` |
| State (Mail Format) | `Karnataka` |
| Migrant | `No` |
| Contractor | `TEAM HR GSA PRIVATE LIMITED` |
| Facility (OB Format) | **Location code** from the master |
| Roll (OB Format Sl No) | 1, 2, 3 … (row order) |

Rules:

- Only **Ready** candidates are generated; Draft / Needs Attention are excluded
  and reported.
- Generation requires the Mail-format fields: DOJ, Name, Mobile, Designation,
  Branch, State, Net Salary, full Aadhaar, DOB, Address, Pin Code, Gender. A
  candidate missing any of these blocks generation (never silently guessed).
- **Full Aadhaar appears ONLY in the Mail Format sheet.** It never appears in
  filenames, folder names, the daily master, logs, or any export.
- Facility Type is written as `DELIVERY_HUB` / `PICKUP_HUB`; 8752 stays
  *Needs Review*.
- Templates are never modified — a copy is opened, sample rows are cleared, real
  rows are written, and the output is saved under a timestamped name.

## 6. Normalization (Mail Format fields)

| Field | Rule |
|-------|------|
| Gender | only Male / Female / Transgender; taken from Aadhaar OCR; OCR artifacts collapsed |
| PIN Code | exactly 6 digits; more/less rejected; kept as text |
| Aadhaar | exactly 12 digits, digits only, kept as text |
| Father's Name | relation markers (S/O, D/O, C/O, W/O) stripped; blank allowed |
| DOJ / DOB | must parse as DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD or DD.MM.YYYY → stored YYYY-MM-DD, displayed DD/MM/YYYY |

## 7. "Copy Details" block (New Onboarding)

The six personal fields are copied **in this exact tab-separated order**:

```
Aadhaar No  DOB  Fathers Name  Address  Pin Code  Gender
```

- Values are copied from the **current reviewed input values** (manual edits
  included), not stale OCR.
- DOB is formatted `DD/MM/YYYY`.
- `Copy With Headers` prepends the labels on a header row.

## 8. Safety gates

- Live portal upload requires **BOTH** `REAL_UPLOAD_ENABLED` **and**
  `ESAMPARK_LIVE_TEST_MODE` = true; both default off.
- All comm providers (Email / WhatsApp / SMS / Voice) default off via
  `COMMUNICATION_ENABLED` and their per-provider flags.
- Credentials come from the **environment only** (`.env`), never from source,
  DB, logs, or generated Excel.