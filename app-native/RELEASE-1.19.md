# Release 1.19.0 (59)

## Changes

- Satellite view uses the public SC RFA imagery cache shared with trail-counter,
  over a USGS fallback. The SC tile endpoint returned imagery and the appropriate
  CORS header for BWG's web origin.
- Attribution has a reserved footer below map actions. Web hides the duplicate
  MapLibre GPS button while retaining the app's locate action and location source.
- Clear search and map-tap dismissal invalidate pending search responses, so late
  responses cannot reopen dismissed results.
- A dedicated map editor supports numbered vertices, coordinate edits, dragging,
  insertion, deletion, extension from either end, sampled quadratic curves,
  freehand strokes, vertex erasing, and undo/redo. Tools wrap on phone screens.
  Curves and pen strokes remain ordinary editable vertices after publication.
  The eraser joins remaining neighbors; the editor explicitly explains this.
  Reopening an edit fetches the full authoritative feature rather than a geometry
  fragment clipped to the currently displayed map tile.
- No-entry polygons publish immediately with the same revision history and
  rollback as other community contributions. New routes check complete itinerary
  geometry, including bus rides and access links, against active areas. Invalid,
  self-crossing, oversized, or incorrectly categorized polygons are rejected.
- Automatic OSM service-drive and parking-aisle shortcuts are excluded. The
  explicit Springer-to-Briar connection remains, pending the user's preference
  about that specific connection.
- A* now retains arrival direction in its search state, penalizes reversals, and
  favors a simple right turn over unnecessary left turns when other costs permit.
  Unavoidable reversals remain possible. The reported exact two-left-turn example
  still needs its endpoints/intersection for a geographic regression check.
- Legacy imported access metadata containing SQL NULL/NaN now falls back to the
  access-aware OSM cache instead of failing graph startup.

## Validation

- 96 Python tests passed, including polygon publication/edit/rollback, first/last
  access links, complete bus itineraries, arrival-direction routing, parking
  exclusion, and malformed imported access metadata.
- 88 Flutter tests passed; Flutter analysis reported no issues.
- Browser checks passed for vertex insertion, curves, pen strokes, erasing, undo,
  responsive layout, delayed-search dismissal, map-tap search dismissal, one GPS
  control, and attribution below Report. The Start button also stayed above
  attribution at both 1280 px and 390 px widths on the deployed website.
- A browser regression check exposed 3 rendered vertices from an 8-vertex route;
  the editor correctly loaded all 8 from the authoritative contribution. Polygon
  submission produced a closed ring and the no-entry category. Its network write
  was intercepted: no test contribution was published.
- The editor's canvas uses direct pointer positions rather than accessibility
  synthetic taps at the center. A separate accessible action adds a vertex at
  the map center; coordinates can also be edited with text fields.
- The real routing graph contains 43,940 nodes and rebuilt in about 49 seconds.
  A local 30-trip bike/walk/roll sample returned 29 routes and refused one endpoint
  without a permitted bike connection. Successful calculations had a 104 ms
  median. This is service calculation time, not end-to-end network latency.
- Live Springer routing returned HTTP 200 and a 237.2 m route; candidate checks
  also passed for walking and rolling.
- Android release preflight passed with zero errors. Its metadata warning refers
  to standard Gradle metadata. Android and iOS release builds completed.

Map access and hazards remain dependent on mapped data and community accuracy.
Physical-device gesture testing remains a tester task; the interactive checks ran
in Chromium at desktop and phone-sized viewports.

## Deployment

The backend and web build 59 are deployed; the live version endpoint confirmed
`1.19.0` / `59`. TestFlight build 59 passed processing, received testing notes,
was attached to BWG Testers, and was submitted successfully for beta review.
Its state is `WAITING_FOR_BETA_REVIEW`, with automatic notification enabled.
The superseded build 58 was expired to clear Apple's same-version review conflict.
Google Play build 59 (`1.19.0`) was uploaded, validated, and committed to the
open testing (`beta`) track on 2026-09-05 UTC after publishing permissions were
granted. A separate track status query confirmed version code `59` with release
status `completed`. Release edit: `14619849174352734340`.
The status command's unrelated vitals query returned a metrics-combination
error; its track query succeeded and verified the release.

The previous production plugin and web bundle are backed up in the API container
at `/meerschaum/bwg-release-backups/1.18.0-57/`.

Backend SHA-256:
`56becfee1398f05787c93b26da4a5f60da1f9d1172664418f0a2992b1d972e36`.

Android bundle SHA-256:
`3e7563efd0a0ce9de9e4477c8867e9273973b1013573f6778d1c59ffe595aa63`.
