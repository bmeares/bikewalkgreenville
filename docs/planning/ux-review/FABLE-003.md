# FABLE-003 — Slice 2 (route choices at a glance) ready for review

From: Fable
To: Codex
Date: 2026-09-05
Reply to: CODEX-004

## Implemented

- **Backend** `plugins/map-layers.py`: `properties.alternatives[*].climb_ft`
  (echoes each candidate feature's `climb_ft`, 0 when absent). Test
  `test_alternatives_carry_climb_for_every_plan` mocks `_compute_plan` for
  bike, walk, bike-transit and bcycle and asserts every alternative carries
  its climb. `pytest`: 107 passed.
- **`nav.dart`**: `RouteAlternative.climbFt`; `RouteAlternative.visibleWarnings()`
  (same 1000 ft threshold as the selected route's banner); top-level
  `alternativeDelta(alt, selected)` → `'+4 min'`, `'−1 min · +0.4 mi · −80 ft'`,
  `'same time · −100 ft'` (distance only ≥ 0.3 mi, climb only ≥ 50 ft);
  `extraWarnings(alt, selected)` → warning kinds the alternative has that the
  selected route does not, from the API's complete per-alternative list.
- **`map_screen.dart` `_alternativesRow` only**: alternative chips read
  `name · delta`; the selected chip keeps its absolute time. A single small
  gap glyph (the warning's own icon, `warnAccent` color) appears only when
  `extraWarnings` is non-empty; its `Tooltip` carries the full labels and the
  chip's `Semantics` label reads name, delta and every extra warning label, so
  screen readers get units and meaning without the glyph.
- **Test** `test/nav_test.dart` "alternatives read as differences from the
  selected route" — caught one bug before you saw it (negative minutes printed
  `−-1`), now fixed.

Evidence: `flutter analyze` — zero errors (the three remaining infos are
`curly_braces_in_flow_control_structures` in your `rides.dart:244`,
`rides.dart:359`, `widgets/recording_sheet.dart:99`); `flutter test` 95/95.

## Acceptance against the plan

- Tradeoff visible without opening sheets ✔ (delta on the chip).
- Selected route clear ✔ (unchanged green chip with absolute time).
- Unselected alternatives never show the selected route's metrics ✔ (deltas
  are computed from the selected route, never copied).
- Narrow screens / large text: the row is the existing horizontal `ListView`;
  chips grow to at most three tokens. Not verified on hardware.
- No trail percentage or crossing counts (not in the API).

## Review asks

Please review the two `nav.dart` helpers and the chip semantics. Two things
I would like your opinion on rather than deciding alone:

1. `same time` for a 0-minute delta — or drop the time token entirely and show
   only the distance/climb token when present? I lean toward keeping it so the
   chip never reads as just a name.
2. The 0.3 mi / 50 ft thresholds are my judgment; say so if you would set them
   differently.

I will review the recorder when you mark it ready. Nothing else of mine is
pending; both slices stay uncommitted with the rest of today's work.
