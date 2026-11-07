"""Build projects/tax-per-acre/zoning_families.csv: (jurisdiction, zone) -> family, for the map's zoning colors.

Names come from the county GIS "Abbreviated Zoning Classification List"
(https://www.gcgis.org/website/Docs/ZonAbbrClass.pdf).
`source` says how each family was decided:
  list      the code and its name are in that list for this jurisdiction
  list-other  not in this jurisdiction's section, but the same code is listed for another one
  inferred  not in the list at all; family guessed from the code (REVIEW THESE)
Usage: python3 -I scripts/taxmap_zoning_families.py <lots_zones.csv> projects/tax-per-acre/zoning_families.csv
where lots_zones.csv has columns jurisdiction, zone, lots.
"""
import csv, sys

RES, MIX, COM, IND, PLAN, CIVIC, AG, UNZ, UNK = (
    'Residential', 'Mixed-use', 'Commercial & office', 'Industrial & services',
    'Planned development', 'Civic, parks & special', 'Agricultural', 'Unzoned', 'Unclassified')

# (jurisdiction, code) -> (family, listed name). Families are our grouping, not the county's.
LISTED = {}
def add(j, codes, family, name):
    for c in codes.split():
        LISTED[(j, c)] = (family, name)

J = 'GREENVILLE COUNTY'
add(J, 'R-R3 R-R1', RES, 'Rural Residential District')
add(J, 'R-S', RES, 'Residential Suburban District')
add(J, 'R-20 R-20A R-15 R-12 R-10 R-7.5 R-6', RES, 'Single-Family Residential District')
add(J, ' '.join(f'R-M{n}' for n in range(2, 21)) + ' R-MA', RES, 'Multifamily Residential District')
add(J, 'R-MHP', RES, 'Residential Manufactured Home Park District')
add(J, 'BTD', COM, 'Business Technology District')
add(J, 'O-D', COM, 'Office District')
add(J, 'C-1 C-2 C-3', COM, 'Commercial District')
add(J, 'NC', COM, 'Neighborhood Commercial District')
add(J, 'POD', COM, 'Planned Office District')
add(J, 'S-1', IND, 'Services District')
add(J, 'I-1', IND, 'Industrial District')
add(J, 'I-2', IND, 'Industrial Park District')
add(J, 'PD', PLAN, 'Planned Development District')
add(J, 'FRD', PLAN, 'Flexible Review District')
add(J, 'ESD-PM', CIVIC, 'Environmentally Sensitive District Paris Mountain')
add(J, 'AG', AG, 'Agricultural Preservation')
add(J, 'UNZONED', UNZ, 'Area Not Zoned')

J = 'FOUNTAIN INN'
add(J, 'R-15 R-12 R-10 R-7.5 R-6', RES, 'Residential District')
add(J, 'R-M', RES, 'Residential Multifamily District')
add(J, 'R-MHP', RES, 'Residential Manufactured Home Park District')
add(J, 'O-D', COM, 'Office District')
add(J, 'C-1', COM, 'Central Business District')
add(J, 'C-2', COM, 'Commercial District')
add(J, 'NC', COM, 'Neighborhood Commercial District')
add(J, 'POD', COM, 'Planned Office District')
add(J, 'S-1', IND, 'Services District')
add(J, 'I-1', IND, 'Industrial District')
add(J, 'PD', PLAN, 'Planned Development District')
add(J, 'FRD', PLAN, 'Flexible Review District')

J = 'GREENVILLE'
add(J, 'RH-A RH-B RH-C RH-D', RES, 'House Scale')
add(J, 'RN-A RN-B RN-C', RES, 'Neighborhood Scale')
add(J, 'RNX-B RNX-C', RES, 'Neighborhood Scale - Neighborhood Flex (residential, limited commercial)')
add(J, 'RC-2 RC-3 RC-5', RES, 'Community Scale')
add(J, 'MX-2 MX-3 MX-5', MIX, 'Mixed Use')
add(J, 'MX-D', MIX, 'Downtown Mixed Use')
add(J, 'MXS-2 MXS-3 MXS-5', MIX, 'Shopfront')
add(J, 'MXS-D', MIX, 'Downtown Shopfront')
add(J, 'BG', COM, 'Business General')
add(J, 'BH', COM, 'Business Heavy')
add(J, 'IX', IND, 'Industrial Flex')
add(J, 'IG', IND, 'Industrial General')
add(J, 'CM', CIVIC, 'Special - Campus')
add(J, 'CV', CIVIC, 'Special - Civic')
add(J, 'PK', CIVIC, 'Special - Parks')
add(J, 'PD', PLAN, 'Planned Development (not in the list for the city)')

J = 'GREER'  # the county list's Greer section predates Greer's current code
add(J, 'R-S', RES, 'Residential Suburban District')
add(J, 'R-20 R-12 R-10 R-7.5 R-5', RES, 'Single-Family Residential District')
add(J, 'RM-1 RM-2', RES, 'Multiple-Family Residential District')
add(J, 'O-D', COM, 'Office District')
add(J, 'C-1', COM, 'Central Business District')
add(J, 'C-2', COM, 'Commercial District')
add(J, 'C-3', COM, 'Highway Commercial District')
add(J, 'CD', RES, 'Cluster Development')
add(J, 'DRD', PLAN, 'Design Review District')
add(J, 'PD', PLAN, 'Planned Development District')
add(J, 'S-1', IND, 'Services District')
add(J, 'I-1', IND, 'Industrial District')

J = 'MAULDIN'
add(J, 'R-20 R-15 R-12 R-10', RES, 'Residential District')
add(J, 'R-M1', RES, 'Mixed Residential District')
add(J, 'R-M', RES, 'Residential Multifamily District')
add(J, 'O-D', COM, 'Office District')
add(J, 'CRD', MIX, 'Central Redevelopment District')
add(J, 'C-1', COM, 'Commercial District')
add(J, 'C-2', COM, 'Highway Commercial District')
add(J, 'S-1', IND, 'Services District')
add(J, 'I-1', IND, 'Industrial District')
add(J, 'PD', PLAN, 'Planned Development')
add(J, 'POD', COM, 'Planned Office District')

J = 'SIMPSONVILLE'  # data spells BL/BU as B-L/B-U
add(J, 'R-E', RES, 'Residential - Estate District')
add(J, 'R-LO', RES, 'Residential - Low Density District')
add(J, 'R-MID', RES, 'Residential - Medium Density District')
add(J, 'R-HI', RES, 'Residential - High Density District')
add(J, 'R-OI', COM, 'Residential - Neighborhood Office and Institutional District')
add(J, 'BL B-L', COM, 'Business - Limited District')
add(J, 'B-G', COM, 'Business - General District')
add(J, 'BU B-U', MIX, 'Business - Urban District')
add(J, 'B-I', IND, 'Business - Industrial District')
add(J, 'ID', PLAN, 'Innovated Development District')

J = 'TRAVELERS REST'
add(J, 'R-7.5 R-10 R-15', RES, 'Residential District')
add(J, 'RD R-D', RES, 'Residential Duplex District')
add(J, 'R-M', RES, 'Residential Multifamily District')
add(J, 'R-MHP', RES, 'Residential Manufactured Home Park District')
add(J, 'TH', RES, 'Townhouse District')
add(J, 'O-D', COM, 'Office District')
add(J, 'C-1N', COM, 'Neighborhood Commercial District')
add(J, 'C-1 C-2', COM, 'Commercial District')
add(J, 'CBD', MIX, 'Central Business District')
add(J, 'S-1', IND, 'Services District')
add(J, 'I-1', IND, 'Industrial District')
add(J, 'PD', PLAN, 'Planned Development District')
add(J, 'FRD', PLAN, 'Flexible Review District')

# Codes in the data that the list doesn't have anywhere. Guesses from the code only.
INFERRED = {
    'PD-R': (PLAN, 'Planned Development - Residential (guess)'),
    'D-MU': (MIX, 'Downtown Mixed Use (guess)'),
    'GR-1': (RES, 'General Residential (guess)'), 'GR-2': (RES, 'General Residential (guess)'),
    'R-C': (RES, 'Residential (guess)'), 'R-TR': (RES, 'Residential (guess)'),
    'R-6': (RES, 'Residential (guess)'), 'R-8': (RES, 'Residential (guess)'),
    'R-D': (RES, 'Residential Duplex (guess)'), 'RN-A': (RES, 'Neighborhood Scale (guess)'),
    'SN': (RES, 'Suburban Neighborhood (per a listing, unconfirmed)'),
    'MD': (RES, 'Medium Density Residential (per a listing, unconfirmed)'),
    'O-S': (CIVIC, 'Open Space (guess)'),
}
# Central business districts read as mixed-use downtowns, not strip commercial.
CBD_AS_MIXED = {('FOUNTAIN INN', 'C-1')}


def classify(j, z):
    if (j, z) in LISTED:
        fam, name = LISTED[(j, z)]
        if (j, z) in CBD_AS_MIXED:
            fam = MIX
        return fam, name, 'list'
    others = {LISTED[k] for k in LISTED if k[1] == z}
    if len({f for f, _ in others}) == 1:
        fam, name = next(iter(others))
        return fam, name + ' (as listed for another jurisdiction)', 'list-other'
    if z in INFERRED:
        return (*INFERRED[z], 'inferred')
    if z.startswith('(') or z == 'nan':
        return UNK, 'No zoning polygon', 'none'
    return UNK, 'Not in the county list', 'unknown'


if __name__ == '__main__':
    rows = list(csv.DictReader(open(sys.argv[1])))
    out = []
    for r in rows:
        fam, name, src = classify(r['jurisdiction'], r['zone'])
        out.append({**r, 'family': fam, 'name': name, 'source': src})
    out.sort(key=lambda r: (r['jurisdiction'], r['zone']))
    with open(sys.argv[2], 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['jurisdiction', 'zone', 'family', 'name', 'source', 'lots'])
        w.writeheader(); w.writerows(out)
    from collections import Counter
    lots = Counter(); src = Counter()
    for r in out:
        lots[r['family']] += int(r['lots']); src[r['source']] += int(r['lots'])
    print('lots by family:', dict(lots.most_common()))
    print('lots by source:', dict(src.most_common()))
    print('needs review:', [(r['jurisdiction'], r['zone'], r['lots'], r['family']) for r in out if r['source'] in ('inferred', 'unknown')])
