import re, glob, os
from datetime import datetime
from openpyxl import load_workbook

RAW = r"data\raw"
XLSX = r"outputs\hand_check_sample.xlsx"

def read_lines(path):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return f.read().splitlines()
        except UnicodeDecodeError:
            pass
    return []

def amt_pattern(a):
    a = float(a)
    ip, dp = f"{abs(a):.2f}".split(".")
    if dp == "00":
        return re.compile(rf"(?<![\d.]){ip}(?:\.0+)?(?!\d)")
    return re.compile(rf"(?<![\d.]){ip}\.{dp.rstrip('0')}0*(?!\d)")

def dates_in(line):
    out = []
    for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", line):
        try: out.append(datetime(int(y), int(m), int(d)))
        except ValueError: pass
    for d, m, y in re.findall(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", line):
        y = int(y) + (2000 if int(y) < 100 else 0)
        try: out.append(datetime(y, int(m), int(d)))
        except ValueError: pass
    return out

def clean_ref(r):
    r = "" if r is None else str(r).strip()
    return r[:-2] if r.endswith(".0") else r

def line_matches(line, pat, supplier, ref, need_amount=True):
    plain = line.replace(",", "").replace("£", "")
    if need_amount and not pat.search(plain):
        return False
    up = line.upper()
    s = supplier.upper().strip()
    if s not in up and s[:10] not in up:
        return False
    if ref and ref not in line:
        return False
    return True

wb = load_workbook(XLSX)
ws = wb["Sample"]
hdr = {c.value: c.column for c in ws[1]}
col = lambda name: hdr[name]

counts = {"Y": 0, "N": 0, "Real issue": 0, "False alarm": 0, "Unsure": 0, "blank": 0}
rows_checked = 0

for r in range(2, ws.max_row + 1):
    if ws.cell(r, col("row_id")).value is None:
        continue
    rows_checked += 1
    dept = ws.cell(r, col("department")).value
    fname = ws.cell(r, col("source_file")).value
    srow = int(ws.cell(r, col("source_row")).value)
    supplier = str(ws.cell(r, col("supplier")).value)
    amount = ws.cell(r, col("amount")).value
    ref = clean_ref(ws.cell(r, col("transaction_ref")).value)
    check = ws.cell(r, col("check_name")).value
    d0 = str(ws.cell(r, col("txn_date")).value)[:10]
    try: base = datetime.strptime(d0, "%Y-%m-%d")
    except ValueError: base = None
    pat = amt_pattern(amount)

    hits = glob.glob(os.path.join(RAW, dept, "**", fname), recursive=True)
    notes, verdict, matched = [], "", "N"
    if not hits:
        notes.append("raw file not found")
    else:
        path = hits[0]
        lines = read_lines(path)
        found = None
        for k in (0, -1, 1, -2, 2):
            n = srow + k
            if 1 <= n <= len(lines) and line_matches(lines[n-1], pat, supplier, ref, check != "zero_amount"):
                found = (n, k); break
        if not found:
            for n, ln in enumerate(lines, 1):
                if line_matches(ln, pat, supplier, ref, check != "zero_amount"):
                    found = (n, None); break
        if found:
            matched = "Y"
            n, k = found
            notes.append(f"raw line {n}" + (f" (source_row offset {k:+d})" if k is not None else " (found by search, not at source_row)"))
            if check == "zero_amount":
                notes.append("raw line: " + lines[n-1][:150])
        else:
            notes.append("no raw line matched supplier+amount+ref")

        # duplicate evidence across the whole department folder
        others = []
        for p in glob.glob(os.path.join(RAW, dept, "**", "*.csv"), recursive=True):
            for n2, ln in enumerate(read_lines(p), 1):
                if p == path and found and n2 == found[0]:
                    continue
                if line_matches(ln, pat, supplier, ref if check == "exact_duplicate" else "", True):
                    others.append((os.path.basename(p), n2, ln))
        if check == "exact_duplicate":
            if others:
                where = "; ".join(f"{f}:{n}" for f, n, _ in others[:3])
                notes.append(f"{len(others)} other matching line(s): {where}")
                if matched == "Y":
                    verdict = "Real issue"
            else:
                notes.append("no second matching line found - review")
        elif check == "probable_duplicate_payment":
            gaps = [abs((d - base).days) for _, _, ln in others for d in dates_in(ln) if base]
            gaps = [g for g in gaps if g > 0 or True]
            near = min(gaps) if gaps else None
            where = "; ".join(f"{f}:{n}" for f, n, _ in others[:3])
            notes.append(f"{len(others)} other same supplier+amount line(s): {where}")
            if near is not None:
                notes.append(f"closest date gap {near} days")
            notes.append("DECIDE: real duplicate or legitimate repeat/instalment?")
        elif check == "zero_amount":
            if matched == "Y":
                verdict = "Real issue"
                notes.append("raw shows zero/placeholder in a spend-over-25000 file")

    ws.cell(r, col("raw_matches_parsed")).value = matched
    ws.cell(r, col("verdict")).value = verdict or None
    ws.cell(r, col("notes")).value = " | ".join(notes)
    counts[matched] += 1
    counts[verdict if verdict else "blank"] += 1

wb.save(XLSX)
print(f"Rows checked: {rows_checked}")
print(f"Raw matches parsed (Y): {counts['Y']}   (N): {counts['N']}")
print(f"Real issue: {counts['Real issue']}   False alarm: {counts['False alarm']}   Unsure: {counts['Unsure']}")
print(f"Verdict still blank (you decide): {counts['blank']}")