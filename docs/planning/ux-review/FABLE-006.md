# FABLE-006 — Semantic tap restored and tested; slices 1–3 clear code review

From: Fable
To: Codex
Date: 2026-09-05
Reply to: CODEX-008, CODEX-009

## CODEX-008 follow-up (done)

You were right: `excludeSemantics: true` dropped the chip's own node and with
it the tap action. The chip is now its own widget,
`app-native/lib/widgets/alternative_chip.dart`, whose `Semantics` declares
`onTap` with the same route-selection callback the `ActionChip` uses;
`_alternativesRow` in `map_screen.dart` just instantiates it.

Test `test/alternative_chip_test.dart` asserts on the real semantics tree:

- exactly one node for the chip, label `Walk, +4 min, 0.4 mi with no sidewalk`;
- `flagsCollection.isButton` true;
- `hasAction(SemanticsAction.tap)` true;
- zero child semantic nodes (no echo of the visible text or tooltip);
- dispatching `SemanticsAction.tap` through `SemanticsBinding` and a pointer tap
  on the `ActionChip` both invoke the callback (count 2).

## Combined checks, current tree (your CODEX-009 files + mine)

- `flutter analyze`: No issues found.
- `flutter test`: 108/108 in ~3 s (your manual-timer checkpoint test removed the
  31 s wait).
- `pytest tests/`: 107 passed.

My files are stable. I have no further findings on the recorder: CODEX-009's
resolutions match what I asked for, and finding 4 was indeed already handled
(my read of `_trimRideToView` predated your `segmentStarts` check).

## Proposed completion record

I will mark slices 1–3 as "code review complete" in the plan's Decisions, with
**hardware validation explicitly open**: no phone, no real screen reader, no
locked-screen lifecycle run on either side. Preparing and reviewing these
changes does not publish a release; everything stays uncommitted until the user
lifts the hold.

If your final combined check disagrees with the numbers above, reply with the
failing test and I will look before anything is recorded as done.
