# Greenville County property tax per acre

A 3D map of property tax per acre. Each lot's 2025 tax bill is divided by its land area; height and color both show tax per acre.

- Page: https://bwg.mrsm.io/dash/tax-per-acre (public)
- Standalone map (embeddable): https://bwg.mrsm.io/taxmap/map. It has a fullscreen button; when embedded without `allow="fullscreen"` on the iframe, the button opens the map in a new tab instead.
- Code: `plugins/taxmap.py`. Pipes: `projects/tax-per-acre.yaml` (instance `sql:bwg`). Reviewed data: `projects/tax-per-acre/*.csv`. Scripts: `scripts/taxmap_*.py`.
- Moved here from `~/projects/tax-per-acre` (served on mrsm.bmeares.com) in Nov 2026.

## Lots

The map shows stacked lots only. The county draws condo, garage and "building only" units as tiny symbols (0.0007–0.02 acres), which would show absurd per-acre values, so each unit is added to the land it sits on. (An as-drawn parcels view existed until Nov 2026 and was removed.)

How a unit is stacked (`fetch()` in the plugin):

1. A **unit** is a parcel under 0.015 acres (650 sq ft), or under 0.05 acres with a unit-style description (UNIT, FLOOR, BUILDING…) or a commercial, multi-family or industrial type. Townhome lots are real lots and are never units.
2. **contained:** a unit drawn inside a land parcel's outline joins that parcel. Land parcels are at least 0.05 acres, and ring-shaped common areas are skipped.
3. **pin:** otherwise it joins its PIN base lot (lot `00800` ← `00801`–`00899`) if that lot is within about 300 ft.
4. **cluster:** otherwise neighboring units are grouped into a building, which joins the land parcel it overlaps most.

In the 2025 data this stacks 4,605 unit parcels into 239,868 lots (from 244,473 parcels). Totals are preserved: all $785.05M is still there.

When zoomed out (below zoom 13), the map shows area-weighted grids of roughly 100, 200 and 400 m. Heights there are exaggerated up to ×100, and the control panel shows the current factor.

## Pipes (instance `sql:bwg`, tables in `public`)

| Pipe | What |
|---|---|
| `plugin:taxmap` / `parcels` / `greenville` | every parcel with its current tax bill, straight from the county's public parcel layer (`GreenvilleNJ/QueryLayers`, layer 0); EPSG:3857, true acres from EPSG:6570. Table `taxmap_parcels_county_greenville`. (BWG's `county."CAD_PARCEL_MP"` is a stale copy: bills paid through Jan 2025.) |
| `sql:bwg` / `parcel_taxes` / `greenville` | each parcel's tax and values by tax year (one row per PIN and year); 2024 was seeded from the older copy |
| `plugin:taxmap` / `zoning` / `greenville` | zoning polygons from the county's public MapServer (`GreenvilleNJ/Search_and_Select_Query`, layer 3), EPSG:6570 |
| `plugin:taxmap` / `zone_families` / `greenville` | (zoning jurisdiction, zone) → zoning family, from `projects/tax-per-acre/zoning_families.csv` |
| `plugin:taxmap` / `millage` / `greenville` | the Auditor's millage sheets by year and DIST, from `projects/tax-per-acre/millage.csv` |
| `sql:bwg` / `municipalities` / `greenville` | municipal limits (`county.BND_MUNICIPAL`, dissolved by name), EPSG:6570 |
| `sql:bwg` / `srt` / `greenville` | Swamp Rabbit Trail (`gcgis."PRISMA Health Swamp Rabbit Trail"`), merged by section and trail vs. access spur, EPSG:4326 |
| `plugin:taxmap` / `lots` / `greenville` | stacked lots, with zone, zoning jurisdiction and municipality |
| `plugin:taxmap` / `grid` / `greenville` | overview grids |

```bash
mrsm compose sync pipes --file projects/tax-per-acre.yaml
# parcels -> parcel_taxes, zoning, zone_families, millage, municipalities, srt -> lots -> grid
```

Syncing writes straight into the production database (`sql:bwg`). The parcel pull takes about 4 minutes and the lots rebuild about 4 more. The routes cache for 10 minutes, so a sync shows up on the live page without a restart.

### Tax year

The county's parcel layer has one `TOTTAX` per parcel and doesn't say which year it is. It was confirmed as **2025** by implied millage: commercial parcels in DIST 500 (6% assessment, no homestead credits) work out to 339.7 mills, the Auditor's 2025 rate of 331.5 plus about 3.5% in fees. The same calculation on the 2024 copy gives 371.3 against a 358.8 rate.

When the county switches to new bills (usually by fall, when bills go out): re-run that check, set `taxmap: tax_year` on the `parcels` pipe in `projects/tax-per-acre.yaml` and `TAX_YEAR` in the plugin, add the new millage sheet (`scripts/taxmap_parse_millage.py`), then sync everything. `parcel_taxes` keeps the previous years.

## Breakdowns by land use, zone and jurisdiction

Each lot (key `lot_id`, the PIN of its land parcel) carries:

- `proptype`, `landuse`: the land parcel's county property type and raw land-use code. Stacked units count under their land parcel's type. The county's property report (linked from each popup) prints the land-use name for a parcel, e.g. "370: Parking Garage / Basement Level"; the map shows codes only.
- `zone`, `zone_jcode`, `zone_jurisdiction`, `zone_share`, `zones`: the zoning polygon covering most of the lot (at least 5% of it), whose government issued it (JCODE 45045 = Greenville County, else the municipality), how much of the lot it covers, and how many zones touch the lot. `UNZONED` is the county's own code for unincorporated land without zoning. A lot that no polygon covers has a null `zone` and is shown as "(no zoning polygon)".
- `municipality`, `muni_share`: the municipality containing at least half of the lot, else `UNINCORPORATED`.
- `tax_district`: the county `DIST` code, named from the `millage` table: a district is the city whose millage it carries, or "Unincorporated". Codes missing from the sheet (`B`, `C`) stay unlabeled. Without the table, labels fall back to the municipality holding at least 95% of a district's lots.

Overlap areas are computed in EPSG:6570 (feet), never in the tiles' EPSG:3857. Every grouping covers every lot, so each adds up to the county total.

- Page: a "Tax revenue by land use, zone and jurisdiction" card with a group-by selector, a property-type filter, a top-15 tax-per-acre chart and a sortable table with a total row (CSV export).
- JSON: `/taxmap/breakdown/{by}` with `by` = `use_class`, `zone_family`, `proptype`, `landuse`, `zone`, `zone_code`, `municipality` or `tax_district`. Optional filters: `proptype`, `landuse`, `municipality`, `zone`, `zone_jurisdiction`, `tax_district`.
- Map: "Color" (tax per acre, use class, or zoning family) and "Show" (one use class, zoning family, property type, municipality, zoning code or tax district, with its totals). The zoomed-out grid isn't split by category, so it's hidden while a filter is on.

### Use class and zoning family

- **Use class** (`USE_CLASS_SQL`): property type split by land-use code. Apartments (2025: 1182, 1183, type MULTI-FAMILY, median 6–7 acres; 2024: 120, 122), Single-family (1100, 1101), Vacant residential (1180, 1181: no building value on 86–93%), Mobile home (1170, 1171, or type MOBILE HOME), Small multi-family (other MULTI-FAMILY codes, 110–113), then Commercial, Industrial, Agricultural, Other by property type. Townhomes and condos can't be told apart from detached houses.
- **The county recodes land use between years.** For 2025 it moved apartment complexes from 120 to 1182, and 2,175 parcels from code 0 to 1100. Re-check the code profile (median acres, building value, property type per code) whenever a new year is loaded.
- **Zoning family** (Lots view): `projects/tax-per-acre/zoning_families.csv`, built by `scripts/taxmap_zoning_families.py` from the county GIS [zoning abbreviations list](https://www.gcgis.org/website/Docs/ZonAbbrClass.pdf). Column `source` says how each row was decided: `list`, `list-other` (same code listed for another jurisdiction), `inferred` (a guess from the code: **review these**), `unknown` (→ Unclassified). Greer's section of the list predates its current code, so most Greer codes are inferred or unclassified. Fountain Inn's C-1 and Travelers Rest's CBD (central business districts), Mauldin's CRD and Simpsonville's BU count as mixed-use. After editing the CSV: `mrsm compose sync pipes --file projects/tax-per-acre.yaml -c plugin:taxmap -m zone_families`.
- **Colors:** only three hues stay distinguishable from every other one on a map (checked all pairs against the dark surface), so blue, orange and aqua each mark a family (residential, commercial, multi-family or mixed-use) and everything else is neutral grey. The legend has a checkbox per class to hide it (`?hide=uc:Industrial,...` in the URL).

## Page charts

Bar colors follow the group on every chart (`GROUP_COLORS`, the eight-hue dark palette; Greenville and single-family blue, commercial orange, apartments and mixed-use aqua, unincorporated grey). Every bar is labeled on its axis, so color is never the only cue.

- **Tax per acre, compared**: benchmark lots, Walmart and the typical house, with pickers for the lot and the typical house's zone.
- **Who has the land, who pays the tax**: two 100% bars (land, tax revenue) split by use class, municipality, zoning family or property type.
- **How concentrated the tax base is**: lots sorted from the highest tax per acre down; cumulative share of land against cumulative share of tax, countywide, City of Greenville and unincorporated (`concentration()`). In 2025 the top 1% of land pays 33% of the tax and the top 10% pays 83%.
- **Change from 2024 to 2025** (`year_change()`, from `parcel_taxes`): by municipality (via each year's tax district and millage sheet) or use class (both years classified by each parcel's current code, so recoding doesn't show as change). Groups under $1M are left out. Totals include parcels that are new in 2025.
- **Tax revenue by land use, zone and jurisdiction**: the breakdown chart and table.

## Comparisons and the details panel

- **Typical house**: the median tax per acre of single-family lots (land use 1100) of 0.1–2 acres that aren't stacked (`TYPICAL_HOUSE_SQL`), countywide or per zoning district with at least 25 of them.
- **Walmart**: the combined tax per acre of Walmart-owned lots over 10 acres.
- Page: "Tax per acre, compared" picks a lot from the chart and a zone for the typical house.
- Map details panel (docked on the right, or a bottom sheet on phones; the camera moves the lot and its matching houses into the uncovered part of the map): the lot's tax per acre as multiples of a Walmart acre and of a typical house in its own zone (countywide when the zone has too few houses), and links to the county's property report for its PIN and each stacked unit's (`https://www.gcgis.org/Apps/Property_Report_NJ/Default.aspx?pin=<PIN>`, new tab).
- **Equal-tax houses** (checkbox, on by default; `?equiv=0` turns it off): clicking a lot highlights the nearest single-family houses whose tax adds up to the lot's (capped at 3,000) and dims the rest. `/taxmap/equivalent/{view}/{id}` returns them as GeoJSON with their count, acres and radius.
- **Swamp Rabbit Trail** overlay (checkbox; `?srt=0`): `/taxmap/srt.geojson`, muted green (`SRT_COLOR`), dashed for access spurs.
- The control panel folds away (folded from the start on screens under 600 px wide or 520 px tall).

## Deploy

Same as the other BWG plugins (see HANDOFF.md). Back up the live copy first:

```bash
scp -P 2269 plugins/taxmap.py meerschaum@mrsm.io:/tmp/
ssh -p 2269 meerschaum@mrsm.io \
  'docker cp mrsm-api-bwg-1:/meerschaum/plugins/taxmap.py /tmp/taxmap.py.bak 2>/dev/null; \
   docker cp /tmp/taxmap.py mrsm-api-bwg-1:/meerschaum/plugins/ && docker restart mrsm-api-bwg-1'
```
