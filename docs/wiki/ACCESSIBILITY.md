# Accessibility (app-native)

How the Flutter app works with TalkBack (Android), VoiceOver (iOS) and the
browser screen readers on the web build, what is automated, what to check by
hand before a release, and what is still missing. First pass: release 1.22.0.

## What the app does

### Labels
- Every `IconButton`, rail button, FAB and chip has a `tooltip` or a
  `Semantics(label:, button: true)`. `test/a11y_test.dart` runs
  `labeledTapTargetGuideline` on every screen and sheet that can be pumped
  without the map.
- Custom controls that set `excludeSemantics: true` (alternative chips, vote
  buttons, the ride code, the disclaimer line) declare their own `onTap`.
  Without it the node has no tap action and a double-tap does nothing. Vote
  buttons had this bug; they now read "Confirm this exists, 3" / "Report this
  is wrong or gone, 0" and report `selected` for your vote.
- **The map** is one node: `Semantics(label: 'Map of Greenville',
  excludeSemantics: true)` around `MapLibreMap`. Screen readers never enter
  the native map view. Everything you can do on the map (search, the rail,
  bottom cards, sheets) sits above it and stays reachable.
- Decorative images and icons stay out of the tree: the logo next to titles
  uses `excludeFromSemantics`, icons carry no `semanticLabel`, and the ride
  summary stats and elevation chart are each read as one sentence
  ("Distance 4.3 mi", "Elevation profile, 940 to 1080 feet").
- Headers (`Semantics(header: true)`) mark sheet titles, settings sections,
  layer groups and group-ride sections, so screen-reader heading navigation
  jumps between them.
- List tiles that combine several texts with a trailing control use
  `MergeSemantics`: community history rows while selecting, nearby group
  rides, group-ride members.

### Live regions (announced without moving focus)
| What | Where | Announces |
|---|---|---|
| Search state | `map_screen.dart` `_searchStatusLine` | "Searching…", "3 results", "No results", "Search failed…" ("No results" also shows on screen) |
| Route found | `widgets/map_cards.dart` `RoutePreviewCard` | "3.4 mi · 21 min, Bike route" when a route plans or replans |
| Finding a route | `_planningChip` | "Finding your route" |
| Turn instruction | `_navChrome` | only when the instruction changes. The distance above it updates every second and is not announced |
| GPS lost | `_gpsStatusLine` | "GPS lost — searching…" |
| Recording state | `widgets/recording_sheet.dart` | "Recording" / "Paused" / "Recovered ride" on each change |
| Draw tools | route / area draw bars, trim panel | waypoint / corner count, kept distance |
| Pick on map | `_pickBanner` | "Tap the map to set your destination" |
| Sign-in | `widgets/sign_in_sheet.dart` | "We emailed a 6-digit code…", errors |
| Group ride ended | `group_ride_sheet.dart` + `toast` | "The ride has ended" |
| Toasts | `theme.dart` `toast()` | uses `SnackBar`, which is a live region |
| Welcome tour | `widgets/welcome_tour.dart` | "Page 2 of 4: Navigate" |

### Touch targets
- `materialTapTargetSize: padded` in both themes, so web also gets 48 dp hit
  areas (Flutter shrink-wraps web and desktop targets by default).
- `VisualDensity.compact` is gone from the rail, the route card's Start
  button and the directions-sheet field buttons. Compact density shrank the
  hit area to 40 dp.
- Vote buttons have a 48 × 48 minimum, the travel-mode variant menus are
  48 × 48 (they were 32 × 40), and the disclaimer line is a single 48 dp tall
  target.
- Trim handles: the dot is 22 dp, and a 13 dp translucent white halo makes
  the grab area 48 dp. The halo counts because MapLibre hit-tests circle
  radius plus stroke.
- The tests run `androidTapTargetGuideline` (48 dp) and
  `iOSTapTargetGuideline` (44 pt).

### Contrast (WCAG 2.1: 4.5:1 text, 3:1 UI graphics)
Checked in `test/a11y_test.dart` ("palette contrast") and by
`textContrastGuideline` on every pumped screen in light, dark, light
high-contrast and dark high-contrast.

| Color | Problem | Fix |
|---|---|---|
| brandGreen `#6F9920` + white text (Report FAB, Re-center, Directions, place card, Share, submit buttons) | 3.36:1 | `brandGreenStrong` `#557A18`: 5.02:1 |
| brandGreen text (Settings section headers, BCycle "N bikes available") | 3.36:1 on white | `brandOnSurface`: 10.3:1 light, 11.3:1 dark |
| white70 subtitle on the route card | 3.67:1 on route blue, 2.88:1 on BCycle red | full white: 5.75:1 / 4.68:1 |
| `Colors.red` "GPS lost", error texts | 3.68:1 on white | `colorScheme.error` (≥ 4.5:1 on both themes) |
| warnRed `#D32F2F` text (step warnings, "End ride") | 3.76:1 on dark | `colorScheme.error` |
| `Colors.black54` sheet captions | unreadable on dark | `onSurfaceVariant` |
| Hazards badge numeral on warnAccent | 3.6:1 | `warnFg` background with `warnBg` numeral (> 7:1) |
| Group badge, white on teal `#00897B` | 4.32:1 | `groupRideBadge` `#00695C`: 6.61:1 |
| Ride / community purple `#7B1FA2` line on the dark basemap | 2.56:1 | dark and satellite bases draw `#CE93D8`: 8.8:1 |
| Tap-highlight amber `#FFC107` on the light basemap | 1.49:1 | High contrast: deep orange `#E65100` (3.46:1) |

Colors that already passed and did not change: purple `#7B1FA2` 8.2:1 on white, red `#C62828` 5.6:1 on
white / 3.3:1 on dark (graphics), teal `#00897B` 4.3:1 / 4.3:1, deep orange
`#E65100` 3.8:1 / 4.9:1, route blue + white 5.75:1, BCycle red + white 4.68:1.

**High contrast** (Settings → Accessibility) uses the stronger theme inks
that were already there. It also:
- thickens the thematic line layers 1.7x (existing, `_ensureLayer`);
- thickens the app's own lines 1.6x and raises their opacity to at least 0.9:
  route, casing, hills, gaps, ride and trim, draft, and the group leader's
  route (`_ownLineStyles`). This is re-applied live when the toggle changes
  (`_restyleOwnLines`), without reloading the style;
- swaps the highlight to deep orange on light bases.

### Text scaling
- The system font size is respected, and Settings → "Large text & controls"
  multiplies it by 1.3 (up to 2.0).
- The route preview and place cards are two rows (`widgets/map_cards.dart`),
  so Start / Navigate here never squeeze the destination name to zero width.
  Before this, both cards overflowed a 360 dp phone even at 1.0x.
- The nav trip bar, feature-sheet actions and the sign-in "Change email /
  Resend" row are `Wrap`s: at large sizes they stack instead of overflowing.
- Tests pump the route and place cards, the sign-in sheet (both steps), the
  ride summary (before and after save) and the group-ride sheet (idle and
  active) at 1.0x, 1.3x and 2.0x on a 360 × 640 phone. Primary actions are
  never ellipsized.

### Keyboard and focus (web)
- Sheets are modal routes. Focus is trapped in them (their own
  `FocusScope`), and Escape closes them via `DismissIntent`. That covers the
  native bottom sheets (tested) and the web `showGeneralDialog`
  (`barrierDismissible: true`, same mechanism, not tested separately).
- The welcome tour is not barrier-dismissible, so Escape is bound to Skip.
- Autofocus is used only in naming dialogs and on the sign-in code field when
  it appears. The main search field never autofocuses.
- Sign-in Tab order is email → Send code; once the code is sent it is
  code → Verify → Change email → Resend. Tested.

### Reduce motion
- `MediaQuery.disableAnimations` (Android "Remove animations", iOS "Reduce
  Motion") stops the record icon pulse (`record_icon.dart`). The welcome tour
  then jumps pages instead of sliding. Tested.

## Manual checklist (TalkBack / VoiceOver)

Run these on a real device before each release. Turn on TalkBack (Settings →
Accessibility) or VoiceOver (triple-click side button). For each flow, also
repeat step 1 with the largest system font size and High contrast on.

1. **Search & route**
   - [ ] Launch: swiping reaches the search field, "Map of Greenville" (one
     stop, never map tiles), and the rail buttons.
   - [ ] Type "falls": you hear "Searching…" then "N results". Swipe through
     the results and double-tap one.
   - [ ] Place card: the name is read, then "Close", "Save", "Change start
     point or modes", "Navigate here".
   - [ ] Double-tap Navigate here: you hear "Finding your route", then the
     distance and time.
   - [ ] Nonsense search: you hear "No results", and it is visible.
2. **Start navigation**
   - [ ] Route card: Start, Clear route, the disclaimer ("… Safety and
     Liability Disclaimer, button"), Save, Copy link, Upcoming turns.
   - [ ] Start → the disclaimer sheet (first time): checkbox, then I Agree.
   - [ ] The instruction is announced once per turn, not every second.
   - [ ] "Mute voice" toggles, and "End" ends navigation.
3. **Record & save**
   - [ ] Rail: "Record a ride" → the sheet announces "Recording".
   - [ ] Pause → "Paused", Resume → "Recording", Stop → the summary.
   - [ ] The summary reads "Distance …", "Moving time …", "Average …". Save →
     Share a stretch / Export GPX / Done.
4. **Sign in**
   - [ ] Settings → Sign in: Email field, then Send code.
   - [ ] "We emailed a 6-digit code…" is announced, focus moves to Code, then
     Verify.
   - [ ] A wrong code announces "That code is wrong or expired."
5. **Vote**
   - [ ] Tap a community line or place, then "Confirm this exists, N,
     button". Double-tap: the count updates and it reads "selected".
   - [ ] Signed out: double-tap opens sign-in.
6. **Group ride join**
   - [ ] Menu → Group ride: headings "Group ride", "Join a ride nearby",
     "Start a group ride".
   - [ ] A nearby ride reads as one item ("Saturday Roll, Led by Alex · 3
     riders · 300 ft away, Join").
   - [ ] Join by code: the sheet shows the real ride name, and the code reads
     letter by letter ("A B C D. Tap to copy.").
   - [ ] When the leader ends the ride: "The ride has ended".

Web (Chrome + NVDA/VoiceOver): Tab through the search bar and rail, open
Layers and close it with Escape, and open the tour on a fresh profile and
close it with Escape.

## Known gaps

- **Trim handles are touch-only.** Dragging along the ride has no
  screen-reader equivalent. "Keep what is on screen" and "Whole ride" are the
  accessible alternatives; an adjustable "start / end" slider is the fix.
- **Map features can't be reached without sight.** Pins, lines, group
  members and turn arrows are MapLibre layers, not semantics nodes. Search,
  the route card and the turn list cover routing. Community places, reports
  and nearby parking still need a list view ("What's near me").
- **Drawing a route or a no-entry area** needs map taps, so it is
  inaccessible without sight.
- The layers sheet and the nav card live inside `_MapScreenState` and can't
  be pumped without the platform map, so they are only covered by the manual
  checklist. Extracting them like `map_cards.dart` would let the guidelines
  run on them.
- The trim-handle halo and the high-contrast restyle
  (`setLayerProperties`) were written against the MapLibre docs and have not
  been checked on a device yet.
- On iOS, Flutter live regions depend on the engine posting announcements;
  confirm the search and turn announcements with VoiceOver on a device.
- The dark-base orchid is used only for the purple lines the app draws.
  Greenlink routes keep their official colors (per-feature), and some of
  those are dark.
