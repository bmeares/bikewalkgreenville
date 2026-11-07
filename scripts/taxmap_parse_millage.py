"""Parse Greenville County Auditor millage sheets (pdftotext -layout) into one CSV.

Each sheet row: DIST COUNTY SCHOOL_NAME SCHOOL [FIRE_NAME FIRE] [OTHER_NAME OTHER]
[CITY_NAME CITY] [SAN_NAME SAN] [ARENA] TOTAL DIST. Named columns are assigned by
the x-position of the page header, because positions shift between pages and years.
Every row is checked: its parts must add up to the sheet's total.

Sheets: https://greenvillecountysc.gov/Auditor/Forms/millage_<year>.pdf (2021-2025 so far).
Usage (needs poppler's pdftotext):
    mkdir -p data/millage && curl -o data/millage/millage_2025.pdf <url>   # one per year
    python3 -I scripts/taxmap_parse_millage.py data/millage projects/tax-per-acre/millage.csv
"""
import csv, re, subprocess, sys, pathlib

NAMED = {'SCHOOL': ('SCHOOL',), 'SEWER': ('SEWER',), 'FIRE': ('FIRE',),
         'OTHER': ('DISTRICTS', 'DISTRICT'), 'CITY': ('CITY',), 'SANITATION': ('SANITATION',)}
ROW = re.compile(r'^\s*([0-9A-Z]{3})\s+(\d+\.?\d*)\s+(.*?)\s+(\d+\.?\d*)\s+\1\s*$')
TOKEN = re.compile(r'([A-Za-z][A-Za-z .&\'/-]*?)\s+(\d+\.?\d*)|(\d+\.?\d*)')


def parse(pdf: pathlib.Path, year: int):
    text = subprocess.run(['pdftotext', '-layout', str(pdf), '-'], capture_output=True,
                          text=True, check=True).stdout
    rows = []
    for page in text.split('\f'):
        lines = page.splitlines()
        first = next((i for i, l in enumerate(lines) if ROW.match(l)), None)
        if first is None:
            continue
        # Header words may sit on different lines (older sheets stack them).
        pos = {}
        for col, words in NAMED.items():
            for l in lines[:first]:
                hits = [m.start() for w in words for m in re.finditer(r'\b' + w + r'\b', l)]
                if hits:
                    pos[col] = hits[0]; break
        for line in lines:
            m = ROW.match(line)
            if not m:
                continue
            dist, county, middle, total = m.groups()
            offset = m.start(3)
            rec = {'year': year, 'dist': dist, 'county': float(county), 'total': float(total)}
            for t in TOKEN.finditer(middle):
                x = offset + t.start()
                if t.group(3):  # bare number -> ARENA (only unnamed column)
                    rec['arena'] = float(t.group(3)); continue
                col = min(pos, key=lambda c: abs(pos[c] - x))
                rec[col.lower() + '_name'] = t.group(1).strip()
                rec[col.lower()] = float(t.group(2))
            rows.append(rec)
    return rows


if __name__ == '__main__':
    src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    allrows = []
    for pdf in sorted(src.glob('millage_*.pdf')):
        allrows += parse(pdf, int(pdf.stem.split('_')[1]))
    fields = ['year', 'dist', 'county', 'school_name', 'school', 'fire_name', 'fire',
              'other_name', 'other', 'sewer_name', 'sewer', 'city_name', 'city', 'sanitation_name', 'sanitation',
              'arena', 'total']
    with open(out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(allrows)
    # Check: components must sum to the sheet's total.
    bad = [r for r in allrows if abs(sum(r.get(k, 0) for k in
           ('county', 'school', 'sewer', 'fire', 'other', 'city', 'sanitation', 'arena')) - r['total']) > 0.15]
    from collections import Counter
    print('rows per year:', dict(Counter(r['year'] for r in allrows)))
    print('rows not summing to total:', len(bad))
    for r in bad[:8]:
        print('  ', r)
