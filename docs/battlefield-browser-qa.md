# Battlefield Commander Browser Qualification

This gate preserves the existing Battlefield Commander visual direction while
making browser, keyboard, accessibility, and responsive behavior
release-blocking.

## Scope

The runner starts the real localhost `WebGuiServer` on an ephemeral port.
Only the bridge/runtime boundary and browser speech APIs use deterministic
fixtures. The browser still submits commands over HTTP, consumes status from
the server, and renders the production page.

The gate verifies:

- Chromium desktop `1440x1100` and mobile `390x844`.
- Exactly four operation lanes, four stages per card, and five standard card
  actions.
- `published` is not rendered as executing without action evidence.
- Keyboard command submission, operation selection, lane navigation, all five
  operation actions, and tactical-radio mute.
- Visible and retained focus, unique DOM IDs, and no horizontal overflow.
- axe-core serious/critical violations equal zero.
- Real `prefers-reduced-motion` and forced-colors browser contexts.
- Two overlapping voice submissions, one aggregate voice/pending surface,
  unique pending identities, and independent operation identities.
- Tracked screenshots with a per-pixel channel tolerance of `12` and a maximum
  changed-pixel ratio of `0.18`.

Actual microphone quality, speaker quality, SC2 client rendering, gameplay
feel, and human multiplayer are not automated by this gate.

## Run

Install the pinned Python environment and the pinned Playwright Chromium:

```bash
uv sync --locked --extra browser
uv run playwright install --with-deps chromium
```

Run the gate against an exact repository commit and admitted MicroMachine build
identity:

```bash
uv run python -m starcraft_commander.battlefield_browser_gate \
  --repository-sha "$(git rev-parse HEAD)" \
  --build-identity "sha256:<64 lowercase hex characters>" \
  --artifact-dir battlefield-browser-artifacts
```

Missing dependencies, missing browser binaries, Chromium launch failures,
missing baselines, assertion failures, or visual threshold failures return a
non-zero exit status. The release CI job must not convert that failure to a
skip or warning.

For local validation only, an already-installed regular executable may be
selected explicitly with `--chromium-executable <path>`. CI does not use this
override; it installs the Playwright-pinned Chromium and fails if that install
or launch is unavailable.

## Baseline Updates

Baseline changes are intentional hosted review events, not a release-gate
write mode. The authoritative gate only reads tracked baselines.

1. Add an unprivileged, PR-only diagnostic step that runs the exact candidate
   on GitHub-hosted Chromium and uploads `screenshots/desktop.png` and
   `screenshots/mobile.png` even when the tracked visual comparison fails.
2. Bind the diagnostic manifest to the candidate SHA, workflow SHA, viewport
   sizes, Playwright/Chromium versions, and both PNG SHA-256 digests.
3. Inspect both hosted screenshots before replacing
   `tests/browser_baselines/desktop.png` and
   `tests/browser_baselines/mobile.png`.
4. Remove the temporary generation step, rerun the normal read-only gate, and
   include the baseline rationale in the PR.
5. Require the exact-SHA independent reviewer to approve the resulting visual
   contract.

## Artifacts

The artifact directory contains:

- `battlefield-browser-gate.json`
- `battlefield-browser-gate.md`
- `screenshots/desktop.png`
- `screenshots/mobile.png`
- `diffs/desktop.png`
- `diffs/mobile.png`

The JSON and Markdown reports bind the result to the repository SHA and
MicroMachine build identity. They always state that manual SC2 live QA remains.
