# Flutter UX plan — Codex × Fable

2026-09-05 · Slices 1–3 code review complete (FABLE-007) · hardware / screen-reader / locked-screen validation OPEN · not released · final checks: analyze clean, Flutter 109/109, pytest 107/107

## Brief

Enhance Bike Walk Greenville through small, useful improvements to everyday trips.
The user's direction: **minimal copy; features should speak for themselves.**
Keep the map central. Favor recognizable controls, visible state, and immediate
feedback. Every addition should earn its space.

This plan follows a source review of `app-native/`, including current uncommitted
changes. Device behavior and visual layouts still need verification. The retired
`bwg_app/` client is out of scope. Existing local edits belong to the user; inspect
the diff before changing overlapping files.

## Fable: open the two-way channel first

1. Confirm access to this repository and identify your checkout/branch. Codex's
   current workspace is `/home/bmeares/projects/bikewalkgreenville`.
2. If both sessions expose an existing direct messaging mechanism, exchange the
   actual session addresses and a test message/reply. Record the working mechanism
   here. Do not assume that agent names alone are routable addresses.
3. Otherwise use `docs/planning/ux-review/` as a shared mailbox. Create a separate
   Markdown file per message: `FABLE-001.md`, `CODEX-002.md`, and so on. The initial
   Codex message is already supplied. Each author uses their next unused number;
   include the message being answered, decision/request, owned files, and evidence.
4. Fable: reply to `CODEX-001` with access confirmation, proposed scope changes,
   and your first task. Codex: acknowledge that reply with `CODEX-002`. Fable:
   acknowledge receipt so both directions are verified.
5. Read new messages before editing and at each implementation/review handoff.
   File writes do not wake an idle agent. If there is no working notification
   mechanism, ask the user to resume the recipient with the message path. Separate
   checkouts need an explicit file transfer or agreed synchronization mechanism.
6. Record agreed scope and ownership in this plan. Preserve each other's messages.
   Resolve routine design and implementation decisions together; bring the user
   only decisions that materially change the intended experience or scope.

Channel status: **mailbox only**. Fable (Claude Code session `bikewalkgreenville-7b`)
confirmed the shared checkout `/home/bmeares/projects/bikewalkgreenville` on `main`
in `ux-review/FABLE-001.md`; no Codex session is routable from Fable's peer list,
so messages are files here and the user resumes the recipient with the path.
Both directions verified (FABLE-001 → CODEX-002 → FABLE-002). Fable runs a file-change monitor on this directory, so Codex writes reach Fable within seconds; Fable's writes still need the user to resume Codex.

## Product rules

- Use short action labels: `Save`, `Start`, `Pause`, `Resume`, `Retry`.
- Let selection, progress, and saved state explain the interaction. Avoid feature
  introductions, promotional copy, and mandatory onboarding.
- Keep secondary detail expandable. Do not restore the stack of warning and
  elevation cards previously removed from the route preview.
- Surface information when it changes a decision: a blocked crossing, unsent
  report, or interrupted recording. Keep necessary disclosures concise and intact.
- Minimal visible copy still needs accessible names, generous touch targets,
  legible type, and status cues beyond color alone.
- Show measured facts. Unknown route coverage stays unknown; do not infer safety,
  accessibility, official resolution, or offline readiness from missing data.

## First delivery: proposed order

### 1. Saved places

Add a save action to place cards and a compact saved row under focused search.
Reuse recent-search behavior; support rename, remove with undo, and persistence.
Start with places. Named trips and saved travel settings can follow.

Acceptance: an existing saved place can open a route preview in two taps from the
map; it survives relaunch; deleting it is recoverable; an empty saved collection
does not add a permanent panel or explanatory paragraph.

Source: `app-native/lib/app_state.dart` (recent searches),
`app-native/lib/screens/map_screen.dart` (search and place cards).

### 2. Route choices at a glance

Improve the existing alternatives instead of adding another route dashboard.
Prototype a compact comparison of time, distance, and climb. Add one meaningful
difference where supported by data, such as extra travel time or a known sidewalk
gap. Keep full terrain and warning detail in the existing route-details sheet.

Acceptance: comparing two routes makes the tradeoff apparent without repeatedly
opening detail sheets; the selected route is clear; narrow screens and large text
remain usable; unselected alternatives never display the selected route's metrics.
Verify API coverage before proposing trail percentages or crossing counts.

Source: `app-native/lib/screens/map_screen.dart` (`_routePreview`,
`_alternativesRow`, `_openHazardsSheet`), `app-native/lib/nav.dart`,
`app-native/lib/api.dart`.

### 3. Recording recovery

Persist an in-progress recording periodically and offer recovery after relaunch.
Add pause/resume with a visible paused state. Preserve separate trace segments
across pauses or GPS interruptions so gaps are not drawn as traveled roads.
Include undo for deleting a saved ride.

Acceptance: interruption preserves the last saved checkpoint; recovery avoids
duplicate rides; paused time and distance behave consistently; saving failures
are visible and retain recoverable data. Test actual interruptions on a device.

Locked-screen recording is a separate follow-up: current recording is explicitly
foreground-only. Assess Android/iOS lifecycle behavior, battery use, permissions,
and release requirements before promising pocket recording. Agree the supported
behavior with Fable and verify it on hardware.

Source: `app-native/lib/rides.dart`,
`app-native/lib/screens/rides_screen.dart`,
`app-native/lib/screens/map_screen.dart`.

## Follow-up candidates

| Candidate | Smallest useful experience | Dependency |
| --- | --- | --- |
| Offline trips and report drafts | Download a trip; retain turns and map coverage; show pending reports with retry | Verify map download support and coverage; distinguish offline guidance from offline rerouting; prevent duplicate submissions |
| Along-route places | Parking and repair stops ordered by detour, with an add-stop action | Place coverage and waypoint routing; add water/restrooms only with reliable data |
| Community follow-through | My contributions, dated confirmations, and report status | Backend lifecycle and ownership; distinguish community updates from official action |
| Easier discovery | Useful layer presets and actionable empty states | Validate which controls users actually miss before adding hints |
| Broader accessibility | Nearby places as a list; optional haptic cues | Screen-reader and device validation |

Accessibility applies to every delivery. Review the current large-UI text-scaling
cap in `app-native/lib/main.dart`; preserve larger system preferences and let
layouts adapt. Existing high-contrast and wheelchair preferences are foundations
to extend, not new features to duplicate.

## Working agreement

Proposed roles: Fable leads interaction/layout review and copy reduction; Codex
leads implementation and verification. Both may implement agreed, separate tasks
and review each other's changes. Confirm ownership in the channel before edits,
especially in the heavily shared `map_screen.dart`. Avoid concurrent edits to the
same file; use isolated worktrees where appropriate without losing local changes.

For each slice: agree a small interaction sketch → assign files → implement →
exchange a diff and screenshots → review → resolve findings → record completion.
Fable should challenge unnecessary controls and text. Codex should flag missing
data, lifecycle limitations, and regression risks before the design depends on them.

Run `flutter analyze` and relevant Flutter tests in `app-native/`; run relevant
Python tests when backend behavior changes. Add tests for persistence, recovery,
and routing behavior where needed. Validate affected screens on a small phone,
with large text and a screen reader; exercise light/dark themes and web map panels
when touched. Report checks actually run and remaining device limitations.

Done means the agreed behavior works, both reviewers' findings are resolved,
relevant checks pass, and the interface needs no extra prose to explain routine
actions. Preparing and reviewing changes does not itself publish a release.

## Decisions

- Confirmed user preference: minimal copy; features speak for themselves.
- First scope agreed (Fable, FABLE-001): saved places → route comparison → recording recovery.
- Saved places route in two taps (focus search, tap saved row) instead of opening the place card; saved rows sit inside the existing focused-search dropdown, empty list renders nothing.
- Ownership: Fable — `app_state.dart`, `map_screen.dart` search/place-card/route-preview regions (slices 1–2); Codex — `rides.dart`, `rides_screen.dart`, ride hooks in `map_screen.dart` (slice 3), announced before editing; `nav.dart`/`api.dart` shared, announce first.
- Slice 1 status: implemented by Fable; CODEX-003 findings resolved (all saved places listed, dropdown bounded to 35 % height and scrollable, mocked-preferences relaunch test). `flutter analyze` clean, `flutter test` 94/94. No device attached to either agent; phone/large-text/screen-reader pass still open.
- Slice 3 (Codex, implemented; CODEX-005/006): flat `points` + `segment_starts` + `active_ms`, `ride_in_progress` checkpoint every 30 s / 50 points with stable ride id, pause/resume with transition lock, lifecycle pause, delete undo, `widgets/recording_sheet.dart` (320 px / 3× widget test). Fable review (FABLE-004) resolved in CODEX-009: isolated bad fixes ignored (only >15 s gaps split), lifecycle-only auto-resume, injected checkpoint timer (suite back to ~3 s), segment-aware viewport selection, revision-gated redraws, storage-ceiling note.
- Slice 2 sketch revised per CODEX-002 (FABLE-002): deltas on chips, distance/climb only when materially different, gap glyph only from the API's complete per-alternative warnings.
- Slice 2 status (Fable, FABLE-003): `climb_ft` added to `properties.alternatives` (map-layers + pytest); `alternativeDelta`/`extraWarnings` in `nav.dart`; alternative chips read `name · +4 min · −80 ft` with a gap glyph only for warning kinds the selected route lacks; semantics label carries units and warning labels. `flutter test` 95/95, `pytest` 107/107. Codex kept `same time` and the thresholds (CODEX-005). Unknown alternative climb is nullable end to end (never compared as 0); chip extracted to `widgets/alternative_chip.dart` with a single semantic node carrying label + tap action (`test/alternative_chip_test.dart`); the row has no fixed height. Trail percentage and crossing counts are not in the API; not proposed.
- **Completion (2026-09-05, FABLE-006)**: slices 1–3 agreed, implemented, cross-reviewed, findings resolved; `flutter analyze` clean, `flutter test` 108/108, `pytest` 107/107. Still open, deliberately: validation on a phone, with a real screen reader, at 3× text, and the locked-screen lifecycle run — no device was attached to either agent. Nothing committed or released; the user holds the release.
- **Final verification (Codex, CODEX-010)**: 109 Flutter tests and 107 Python tests passed; analyzer and whitespace checks clean. The added lifecycle regression prevents a queued resume from restarting GPS after another background transition. Recording controls have a 320 px / 3× widget check; route alternatives have a semantic-node and tap-action check. These automated checks do not replace the open hardware validation. All changes remain local and uncommitted.
- **Final acknowledgment (Fable, FABLE-007)**: lifecycle guard reviewed with no findings; independently confirmed the same final check counts. Both code reviews are complete. This review exchange is closed; the shared mailbox remains available for the next task when both agents are active.
