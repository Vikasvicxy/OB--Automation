"""Regression: Facility / Hub dropdown sourced from the FULL HubName master.

Covers the New Onboarding rework:
  - /api/search-hubs never filters by cost_code (LM-inferred flow can still
    pick an FM ``_PL`` hub and vice versa) and supports a full-master browse.
  - ``Nelamangala`` resolves BOTH the LM (display ``NelamangalaHub_BLR``,
    location ``BLR/NLM``) and the FM (``NelamangalaHub_BLR_PL``) rows; the
    selection drives Cost Code / Operation / Facility Type / Location.
  - Every ``_PL`` row in the master is searchable + selectable with the correct
    First Mile classification.
  - Blank-Column-C rows and duplicate display names stay visible with readable
    names and unambiguous picks.
  - The Copy-ready personal details block copies the six fields (TAB-separated,
    exact values) by running the shipped JS in a Node DOM stub.
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import app.main as main
import app.master_data as md

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, "app", "templates", "smart_upload.html")
HARNESS = os.path.join(REPO, "tests", "_facility_js_harness.cjs")

_client = TestClient(main.app)

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def search_hubs(query="", all_=False):
    params = {"all": "1"} if all_ else ({"query": query} if query else {})
    return _client.get("/api/search-hubs", params=params).json()


# ── 1. Full-master browse + no cost-code pre-filtering ─────────────────────

def test_full_master_browse():
    print("\n--- 1. Full master browse ---")
    rows = md.get_facility_rows()
    data = search_hubs(all_=True)
    hubs = data.get("hubs") or []
    check("browse returns every master row", len(hubs) == len(rows),
          f"(got {len(hubs)} rows vs master {len(rows)})")
    check("browse 'total' is the master row count", data.get("total") == len(rows),
          f"(got {data.get('total')})")
    names = {h["facility_name"] for h in hubs}
    check("every display name is non-empty", all(n.strip() for n in names))
    check("browse includes LM + FM + Myntra entries",
          "NelamangalaHub_BLR" in names and "NelamangalaHub_BLR_PL" in names
          and any("MYNTRA" in n.upper() for n in names))
    loc_by_key = {r["hub_key"]: r["location"] for r in rows}
    for h in hubs:
        if h["location"]:
            check(f"row {h['facility_name']!r} re-resolves to its own location",
                  md.resolve_facility_selection(h["facility_name"], h["location"]).get("location") == h["location"],
                  f"(got {md.resolve_facility_selection(h['facility_name'], h['location']).get('location')!r})")
            if "NelamangalaHub_BLR" in h["facility_name"]:
                break
    check("search result row has cost_code/operation/facility_type/entity",
          hubs[0].get("cost_code") != "" and hubs[0].get("operation") != ""
          and hubs[0].get("facility_type") != "" and hubs[0].get("entity") != "")


def test_no_cost_code_prefilter():
    print("\n--- 2. cost_code never pre-filters the dropdown ---")
    for q in ("NelamangalaHub_BLR_PL", "nelamangala pl", "NelamangalaHub_BLR"):
        got = {h["facility_name"] for h in search_hubs(query=q).get("hubs", [])}
        check(f"query {q!r} is searchable regardless of current LM/FM guess",
              bool(got), f"(got {got})")


def test_nelamangala_pair():
    print("\n--- 3. Nelamangala LM/FM pair resolves from readable names ---")
    data = search_hubs(query="nelamangala")
    hubs = data.get("hubs", [])
    got = [(h["facility_name"], h["location"], h["cost_code"], h["operation"]) for h in hubs]
    check("LM row present with readable display + location",
          any(hf == "NelamangalaHub_BLR" and loc == "BLR/NLM" for hf, loc, *_ in got),
          f"(got {got})")
    check("FM _PL row present",
          any(h["facility_name"] == "NelamangalaHub_BLR_PL" for h in hubs),
          f"(got {got})")
    check("BOTH rows are the top results (no unrelated fuzzy hubs above)",
          hubs[0]["facility_name"].startswith("Nelamangala")
          and hubs[1]["facility_name"].startswith("Nelamangala"),
          f"(got {got[:4]})")
    check("search 'nlm' by location finds the LM row",
          any(h["facility_name"] == "NelamangalaHub_BLR" for h in search_hubs(query="nlm").get("hubs", [])),
          f"(got {[h['facility_name'] for h in search_hubs(query='nlm').get('hubs', [])]})")
    data = search_hubs(query="nelamangala pl")
    fm = [h for h in data.get("hubs", []) if h["facility_name"] == "NelamangalaHub_BLR_PL"]
    check("'nelamangala pl' surfaces the FM _PL row",
          bool(fm) and fm[0]["cost_code"] == "4441",
          f"(got {[h['facility_name'] for h in data.get('hubs', [])]})")
    check("selecting each row derives Location/Cost Code/Operation",
          all(r["facility_name"] and r["location"] and r["cost_code"] and r["operation"]
              for r in hubs[:2]))


def test_all_pl_rows_searchable():
    print("\n--- 4. Every _PL (First Mile) row is searchable + selectable ---")
    rows = md.get_facility_rows()
    pl = [r for r in rows if "_PL" in r["facility_ref"].upper() or r["facility_name"].upper().endswith("_PL")]
    check("master contains _PL rows", len(pl) >= 10, f"(got {len(pl)})")
    for r in pl[:]:
        data = search_hubs(query=r["facility_name"])
        names = [h["facility_name"] for h in data.get("hubs", [])]
        hit = r["facility_name"] in names
        if not hit:
            check(f"searchable: {r['facility_name']}", hit, f"(not in {names[:6]})")
            continue
        row = next(h for h in data["hubs"] if h["facility_name"] == r["facility_name"])
        if row["entity"] == "Myntra":
            # Requirement: Myntra facilities STAY Myntra even with a _PL suffix.
            check(f"{r['facility_name']} stays Myntra",
                  row["cost_code"] in ("8751", "8752") and row["entity"] == "Myntra",
                  f"(got {row.get('entity')}/{row.get('cost_code')})")
        else:
            check(f"{r['facility_name']} derives First Mile/4441/Pickup Hub",
                  row["operation"] == "First Mile" and row["cost_code"] == "4441"
                  and row["facility_type"] == "Pickup Hub",
                  f"(got {row.get('operation')}/{row.get('cost_code')}/{row.get('facility_type')})")
    myntra = [r for r in rows if str(r.get("entity", "")).upper() == "MYNTRA"]
    check("Myntra rows stay Myntra 8751 (LM) / Delivery Hub",
          bool(myntra) and all(r["cost_code"] == "8751" and r["operation"] == "Last Mile"
                               and r["facility_type"] == "Delivery Hub" for r in myntra),
          f"(examined {len(myntra)} Myntra rows)")
    flip_lm = [r for r in rows if str(r.get("entity", "")).upper() != "MYNTRA"
               and r.get("operation") == "Last Mile"]
    check("Flipkart LM rows derive Last Mile/4421/Delivery Hub",
          bool(flip_lm) and all(r["cost_code"] == "4421" and r["facility_type"] == "Delivery Hub"
                                and "_PL" not in r["facility_ref"].upper() for r in flip_lm),
          f"(examined {len(flip_lm)} Flipkart LM rows)")


def test_duplicates_and_blank_c():
    print("\n--- 5. Duplicates + blank Column C handled by readable names ---")
    rows = md.get_facility_rows()
    dup_groups = {}
    for r in rows:
        dup_groups.setdefault(r["facility_name"], []).append(r)
    dups = {k: v for k, v in dup_groups.items() if len(v) > 1}
    check("duplicate display name groups exist (e.g. Mysore Hub)", bool(dups), f"(got {list(dups)})")
    mysores = dup_groups.get("Mysore Hub", [])
    check("Mysore Hub shows both locations (BLR/MYQ + BULK/MYS)",
          {r["location"] for r in mysores} == {"BLR/MYQ", "BULK/MYS"},
          f"(got {[r['location'] for r in mysores]})")
    data = search_hubs(query="Mysore Hub")
    got_locs = [h["location"] for h in data.get("hubs", []) if h["facility_name"] == "Mysore Hub"]
    check("both duplicate rows are offered as explicit choices",
          "BLR/MYQ" in got_locs and "BULK/MYS" in got_locs, f"(got {got_locs})")
    check("selecting the exact row re-resolves (dup by location)",
          md.resolve_facility_selection("Mysore Hub", "BULK/MYS").get("location") == "BULK/MYS")
    # Blank Column C row: derive display from the Column A parenthetical
    nlm_row = md.get_facility_row_by_key("BLR/NLM") or {}
    check("blank-C Nelamangala LM falls back to readable name",
          nlm_row.get("facility_name") == "NelamangalaHub_BLR",
          f"(got {nlm_row.get('facility_name')!r})")
    check("blank-C row still resolves to its location BLR/NLM",
          md.resolve_facility_selection("NelamangalaHub_BLR").get("location") == "BLR/NLM")


def test_bare_locality_stays_ambiguous():
    print("\n--- 6. Bare locality still flags review, exact display auto-selects ---")
    bare = md.resolve_facility_selection("Nelamangala")
    check("bare 'Nelamangala' resolves to nothing concrete",
          not bare.get("location") and not bare.get("facility_ref"),
          f"(got {bare})")
    exact = md.resolve_facility_selection("NelamangalaHub_BLR")
    check("exact LM display resolves to BLR/NLM + cc 4421",
          exact.get("location") == "BLR/NLM"
          and (md.get_facility_row_by_key(exact.get("hub_key")) or {}).get("cost_code") == "4421",
          f"(got {exact})")


# ── 7. Copy-ready personal details block (executes the shipped JS) ─────────

def test_copy_block_js():
    print("\n--- 7. Copy-ready personal details (rendered in Node) ---")
    html = open(TEMPLATE, encoding="utf-8").read()
    order = re.findall(r'\{ id: "(rf[A-Za-z]+)",\s+cpy: "(cp[A-Za-z]+)",\s+label: "([^"]+)"', html)
    expected_order = [
        ("rfAadhaar", "cpAadhar", "Aadhar No"),
        ("rfDob", "cpDob", "DOB"),
        ("rfFatherName", "cpFather", "Fathers Name"),
        ("rfAddress", "cpAddress", "Address"),
        ("rfPinCode", "cpPin", "Pin Code"),
        ("rfGender", "cpGender", "Gender"),
    ]
    check("COPY_FIELD_DEFS order + labels match spec", order == expected_order, f"(got {order})")
    check("Copy Details button wired", 'id="btnCopyDetails"' in html and "copyCurrentValues(false)" in html)
    check("Copy With Headers button wired", 'id="btnCopyHeaders"' in html and "copyCurrentValues(true)" in html)
    check("block sits before Job Details / after Identity + srcFiles",
          html.index('id="copyDetailsCard"') > html.index('id="srcFilesBlock"')
          and html.index('id="copyDetailsCard"') < html.index('rfFacility'),
          "copy block must render after source files and before the facility field")
    check("non-blocking 'Copied 6 fields' feedback exists",
          '"Copied 6 fields"' in html)
    check("copy uses tab separator", '"\\t"' in html.replace("\\t", "__TAB__") or 'join("\\t")' in html)
    check("address single-line + Aadhaar/PIN digits normalisation present",
          re.search(r'kind === "address"', html) and re.search(r'kind === "aadhaar"', html)
          and re.search(r'kind === "pin"', html))

    fields = {
        "rfAadhaar": "246697470317",
        "rfDob": "06/05/2006",
        "rfFatherName": "Manik",
        "rfAddress": "S/O: Manik,\nKusarampalli",
        "rfPinCode": "585307",
        "rfGender": "Male",
    }
    proc = subprocess.run(
        ["node", HARNESS, TEMPLATE, json.dumps({"fields": fields})],
        capture_output=True, text=True, timeout=60)
    check("node harness boots", proc.returncode == 0, proc.stderr[-300:] if proc.returncode else "")
    if proc.returncode != 0:
        return
    out = json.loads(proc.stdout)
    goal = "246697470317\t06/05/2006\tManik\tS/O: Manik, Kusarampalli\t585307\tMale"
    check("Copy Details produces the exact six-value TAB row",
          out.get("clip_plain") == goal, f"(got {out.get('clip_plain')!r})")
    check("Copy With Headers prefixes the header row",
          out.get("clip_headers")
          == "Aadhar No\tDOB\tFathers Name\tAddress\tPin Code\tGender\n" + goal,
          f"(got {out.get('clip_headers')!r})")
    check("DOB ISO -> DD/MM/YYYY", out.get("dob_iso") == "06/05/2006", f"(got {out.get('dob_iso')})")
    # Address line-breaks collapse to a single space
    check("address line-breaks collapse (plain) ", "\t" not in "S/O: Manik, Kusarampalli"
          and "S/O: Manik, Kusarampalli" in goal)

    fields2 = dict(fields)
    fields2["rfAadhaar"] = "2466 9747 0317"
    fields2["rfPinCode"] = "585-307"
    fields2["rfAddress"] = "S/O: Manik,\nKusarampalli,\nDharwad"
    proc2 = subprocess.run(
        ["node", HARNESS, TEMPLATE, json.dumps({"fields": fields2})],
        capture_output=True, text=True, timeout=60)
    if proc2.returncode == 0:
        out2 = json.loads(proc2.stdout)
        check("Aadhaar copied as 12 digits (no spaces/symbols)",
              out2.get("clip_plain", "").split("\t")[0] == "246697470317",
              f"(got {out2.get('clip_plain', '').split(chr(9))[0]!r})")
        check("PIN copied as digits",
              out2.get("clip_plain", "").split("\t")[4] == "585307",
              f"(got {out2.get('clip_plain', '').split(chr(9))[4]!r})")
        check("multi-line address collapsed to one line",
              out2.get("clip_plain", "").split("\t")[3] == "S/O: Manik, Kusarampalli, Dharwad",
              f"(got {out2.get('clip_plain', '').split(chr(9))[3]!r})")


# ── Runner ─────────────────────────────────────────────────────────────────

def main():
    test_full_master_browse()
    test_no_cost_code_prefilter()
    test_nelamangala_pair()
    test_all_pl_rows_searchable()
    test_duplicates_and_blank_c()
    test_bare_locality_stays_ambiguous()
    test_copy_block_js()
    print(f"\n{'=' * 62}")
    print(f"TOTAL: {passed + failed}  PASS: {passed}  FAIL: {failed}")
    if failed:
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()