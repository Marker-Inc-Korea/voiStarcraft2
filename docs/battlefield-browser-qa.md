# Compact Controller Browser Qualification

This document describes the current compact-only browser contract. The former
Battlefield Commander dashboard, operation cards, four lanes, card actions,
LLM settings/briefing panels, and Tactical Radio UI are removed. Historical
`battlefield_browser_gate.py` code and screenshot artifacts may still be used
by trusted exact-SHA provenance workflows, but they are not a specification for
the current product surface and must not be used to restore the removed UI.

## Scope

The current qualification starts the real localhost `WebGuiServer` on an
ephemeral port and checks the production compact page. `/`, `/index.html`, and
`/companion` must return the same page.

The current contract verifies:

- One compact current-command surface with runtime status, composition summary,
  short text captions, text/browser-voice input, and emergency retreat.
- Removed legacy labels and structures are absent, including `전장 대시보드`,
  `LLM 설정`, `Legacy python-sc2 commander`, operation cards, four lanes, and
  `window.open(...)`.
- `queued` and `published` remain transport states. They are not rendered as an
  actual SC2 effect without matching runtime telemetry.
- In an ordinary browser, command submission queues/publishes through
  `/api/micromachine/modulate` without starting SC2 because no native launch
  bridge is available.
- In the installed macOS app, the same page can use the native bridge and launch
  receipt to auto-start the runtime while retaining one controller window.
- The current-command selector does not let stale telemetry or an unrelated
  standing operation replace the latest user command.

Actual microphone quality, SC2 client rendering, gameplay feel, and human
multiplayer are not automated by this browser qualification.

## Run

Run the compact page and browser-script regressions:

```bash
.venv/bin/python -m pytest -q tests/test_companion_browser.py
.venv/bin/python -m pytest -q \
  tests/test_web_gui.py::WebGuiServerHTTPTest::test_index_page_serves_only_compact_controller \
  tests/test_web_gui.py::WebGuiServerHTTPTest::test_companion_page_is_compact_and_uses_existing_runtime_apis \
  tests/test_web_gui.py::WebGuiServerHTTPTest::test_all_controller_routes_serve_the_same_compact_page
```

Run the installed-app bootstrap and single-window regressions separately:

```bash
.venv/bin/python -m pytest -q tests/test_local_cockpit.py
```

These tests verify markup, routing, queueing, native-bridge behavior, and window
policy. They do not replace the actual visible-SC2 live gate. A live pass still
requires current matching operation identity and telemetry-backed assignment
or submission plus the command-specific production, movement, engagement,
target, completion, or `effect_observed` evidence. Assignment or submission
alone is not a gameplay-success verdict.
