Creative Checker / CUA visual HTTP fixtures

Purpose
  Run the real React screen with a synthetic loopback API, without starting the
  actual backend, PostgreSQL, OCR, model server, or worker. This is a test helper,
  not a product demo route. State is memory-only and resets on process restart.
  No upload bytes are saved; every registered image uses tests/v1/poster.png.
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

Not covered:
  Real authentication security, DB persistence, permissions, image/PDF decoding,
  actual OCR accuracy, external models, video processing, request approval,
  revision comparison, manual verdict approval, or final business approval.
  Full API validation, fault injection and keyset pagination remain in the
  existing unit/E2E suites; this helper is for the normal visual click path.

Static check (does not start a listener):
  node --check frontend/tests/visual/fixture-server.mjs

Stop both helper and Vite processes with Ctrl-C after visual QA. Keep this helper
under tests; do not add fixture branches to src or point it at real services.
