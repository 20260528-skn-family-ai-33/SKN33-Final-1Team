Creative Checker / CUA visual HTTP fixtures

Purpose
  Run the real React screen with a synthetic loopback API, without starting the
  actual backend, PostgreSQL, OCR, model server, or worker. This is a test helper,
  not a product demo route. State is memory-only and resets on process restart.
  No upload bytes are saved; every registered image uses tests/v1/poster.png.
  Video uploads are streamed, counted and hashed, then discarded.
  PDF extraction and review results are explicitly synthetic fixtures.
  Unknown routes return errors; there is no real-backend/network fallback.

Run from the repository root, in separate terminals:

  v1 fixture:
    node frontend/tests/visual/fixture-server.mjs --profile v1 --port 8991
  v1 React:
    API_PROXY_TARGET=http://127.0.0.1:8991 npm --prefix frontend run dev:v1 -- --port 8131 --strictPort
  Open in CUA:
    http://127.0.0.1:8131/projects

  legacy fixture (can run alongside v1 on a different port):
    node frontend/tests/visual/fixture-server.mjs --profile legacy --port 8992
  legacy React:
    API_PROXY_TARGET=http://127.0.0.1:8992 npm --prefix frontend run dev -- --port 8123 --strictPort
  Open in CUA:
    http://127.0.0.1:8123/review/new?sample=1

Only 127.0.0.1 is bound. --profile defaults to v1; --port defaults to 8991.
Use the Vite URL for the UI. The fixture port serves API only.
GET /__fixture identifies the fixture process. It is not a readiness/model test.

Synthetic credentials (test constants, not real accounts):
  v1 email: reviewer@example.test / fixture-password
  v1 explicit developer login: admin / admin
  legacy: tester / test-password (also accepts admin / admin)
  Sessions use a dedicated profile-specific HttpOnly test cookie.

v1:
  Existing project/draft/criteria/run/evidence come from ../v1/fixtures.mjs.
  That module has no registerMock export; this server reuses its DTO builders
  and adapts the routes documented in ../v1/browser.spec.mjs to HTTP.
  Create projects, register a fixture image, save/edit drafts, confirm versions,
  admit a run, poll, inspect evidence/OCR, export JSON, explicitly retry.
  Creates real-shaped unique fixture IDs, immutable criteria snapshots, and
  in-memory idempotency replies. Edited draft revisions make old criteria stale.
  New run progress is time-based fixture progress (~2.2 seconds), not inference.
  Developer UI availability is always true in this helper, not a production claim.

legacy:
  Authentication, health, sample and review shapes follow ../e2e/login.spec.mjs.
  Sample image uses the shared 200x300 PNG. Submitted criteria IDs/text survive.
  Results deliberately include pass/fail/review cases to inspect visual states.
  /api/requirements/extract/stream emits NDJSON progress/result test events;
  document content is not parsed. Cancellation closes only the fixture response.

legacy video (/video page, /api/video-reviews and /api/video-logos):
  Optional playback clip, read once into memory (.webm or .mp4 only):
    node frontend/tests/visual/fixture-server.mjs --profile legacy --port 8992 --video path/to/clip.webm
  Every job plays that same clip at /api/video-reviews/{id}/video with HTTP
  Range (206, 416 for bad ranges) so the player can seek. Without --video the
  route is 404, so the results player shows its playback error. The job's
  playback field follows the backend: {available:false, reason:"원본이 브라우저에서
  바로 재생됩니다."} means the original is streamed directly (only AVI gets a
  separate playback MP4, which this helper never produces).
  All jobs describe one synthetic 540x960, 12 s script (generic example
  captions, a "유료 광고 포함" line at 0-3 s and 9-12 s, excluded background
  and small text). GET /__fixture/video-script (no login) returns its cues
  {text, start_ms, end_ms, bbox_xyxy, role} so a matching clip can be rendered.
  The upload is never decoded: media size and length always come from the script.
  Frames are generated SVGs at the backend sampling times (1 s grid, plus 0.2 s
  in the first and last 3 s unless the ad kind is self; ids t%06d). They are
  served once a job reaches scene_detection.
  A new job advances on each read, no timers: queued 2 s, extracting 2 s,
  scene_detection 2 s, ocr 14 s ("프레임 d / t"), classifying 1.5 s,
  segmenting 1.5 s, review 2 s, double_check 1 s, then completed (~26 s).
  At most 3 jobs may be queued or running; the next submit gets 429.
  Results reuse the submitted criteria: by position pass, fail, whole-video
  review, review with a spacing/case mismatch (visual criteria are whole-video
  review). A fail carries a one-character mismatch; tracks read differently
  across frames add variants mismatches. Reasons say they are synthetic.
  Disclosure follows the backend R0-R6 rules over the script, except that a
  title and tag without "광고" is shown as fail (fixture-only, to see the
  blocking state). Kind self is not applicable.
  A project name containing "실패" fails during ocr with the backend error text;
  a logo job whose title contains "실패" fails during candidate search.
  Seeded at startup, older than new jobs, visible right after login:
    5eed0000-0000-4000-8000-000000000001  completed, three example criteria
    5eed0000-0000-4000-8000-000000000002  failed during ocr
  Open in CUA (legacy React as above):
    http://127.0.0.1:8123/video?id=5eed0000-0000-4000-8000-000000000001
  /api/video-logos covers status, list (?review_id=), create and get only.
  A logo job is queued ~1 s, searches ~6 s, then candidates_ready. The
  /video/logo confirmation page (index.json, data.json, frames, refs,
  compare) is not covered and returns 404.

Not covered:
  Real authentication security, DB persistence, permissions, image/PDF decoding,
  actual OCR accuracy, external models, video decoding or real frame OCR,
  logo position confirmation and comparison, request approval,
  revision comparison, manual verdict approval, or final business approval.
  Full API validation, fault injection and keyset pagination remain in the
  existing unit/E2E suites; this helper is for the normal visual click path.

Static check (does not start a listener):
  node --check frontend/tests/visual/fixture-server.mjs

Stop both helper and Vite processes with Ctrl-C after visual QA. Keep this helper
under tests; do not add fixture branches to src or point it at real services.
