# Release 1.18.0 (57)

## Shipped behavior

- Community places, corrections, and drawn paths publish immediately. Public
  history supports reasoned rollback; stale edits return a conflict. This is a
  BWG overlay, not an upstream OSM editor. Routing picks up eligible paths after
  its background rebuild.
- Off-course navigation reroutes from the actual GPS position, keeps speaking,
  and retries failures while retaining the user's destination and itinerary.
- Routing respects mapped private access, gates, and mode restrictions across
  source layers and fallback paths. Crossing costs consider busy roads even when
  the approach streets are quiet. Synthetic links cannot invent arterial crossings.
- Public service roads and parking aisles can connect routes. The Springer tunnel
  stays separate from Church Street above it, and fence vertices no longer falsely
  block its portal.
- Web panels intercept map clicks and dismiss without spawning replacement menus;
  stale asynchronous map queries are cancelled. Mode variants have explicit
  dropdown arrows. Marker image scaling no longer applies pin size twice.
- Non-endorsement, personal-risk, access, helmet, and traffic-law notices appear in
  navigation and contribution flows.

## Validation

- Python: 84 tests passed, including route restrictions, crossing costs, and
  community API publication/edit/rollback/write-failure cases.
- Flutter: 80 tests passed; static analysis passed.
- Release web, signed Android bundle, and signed iOS archive built successfully.
- Android build 57 preflight: zero errors. Bundle signature verified. The scanner's
  metadata warning refers to standard Gradle app metadata.
- Browser: five repeated web panel open/dismiss cycles passed on the deployed
  website with no console errors; checked a 390-pixel viewport and downtown
  high-zoom map rendering.
- Live API: community history and GeoJSON returned HTTP 200; Springer routing
  returned HTTP 200 with the tunnel geometry. Deployed backend source hash matches
  the checked local release source.
- Real graph: 30 seeded local bike/walk/roll trips succeeded; median calculation
  81.6 ms, maximum 284.5 ms. Disk graph cache loaded in 2.7 seconds versus an
  85.3-second cold rebuild. These are local service measurements, not a network SLA.
- The real database accepted and returned a community revision in an isolated
  temporary table; that test table was removed. No synthetic public submissions
  were published.

The reported native ONE City Plaza icon glitch still needs physical-device
confirmation. Rendering and navigation tests do not certify on-the-ground access
or crossing safety; routing depends on mapped data. Anonymous community edits have
rate limits, bounds, and rollback, but no account reputation system.

## Deployment record

- Web and API deployed to https://bwg.mrsm.io/bwg-app/; live version endpoint
  confirmed `1.18.0`, build `57`.
- Google Play open testing is blocked by HTTP 403 for the configured publishing
  account, `play-console-cli@sra-play-console.iam.gserviceaccount.com`. The final
  build 57 release attempt failed while creating the edit, before uploading.
  Grant this account BWG app access and testing-track release permissions.
  The signed bundle is ready at
  `build/app/outputs/bundle/release/app-release.aab`.
- iOS build 57 passed Apple processing, received testing notes, and was attached
  to the external `BWG Testers` group. Beta review was submitted successfully;
  Apple's external state is `WAITING_FOR_BETA_REVIEW`, with automatic notification
  enabled. External tester availability awaits Apple's approval.

Android bundle SHA-256:
`f835eca6eab0403b31d795e0e22aec80316110bfd89911e8b5f3f9649bfb6bb3`.

Backend SHA-256:
`cd91f0ecb344fb7717d129bd124ca8358486e8053d1ddc2c230700cdb43e5ea1`.

The previous production plugin and web bundle are backed up in the running API
container under `/tmp/bwg-release-backup-56/`. Community rollback is separate:
it appends a revision and restores the previous feature without deleting history.
