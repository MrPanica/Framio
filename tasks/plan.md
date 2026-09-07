# Implementation Plan: Multi-zone capture corrections

## Overview

Restore the intended distinction between replacing a single selection and adding protected multi-zones, make group actions terminate selection-mode UI cleanly, expose all captured images through Windows-compatible clipboard data, and add a repeatable Windows build workflow.

## Task List

### Phase 1: Capture correctness

- [x] Restore outside-drag replacement for a single zone while preserving multi-zone selections.
- [x] Make multi-image clipboard include both the Framio payload and file URLs for all images.
- [x] Make multi-zone save deterministic, create missing directories, and report failed writes.

### Checkpoint: Capture correctness

- [x] Focused regression tests pass for outside drag, clipboard URL count, and multi-save paths.

### Phase 2: Group-action UX

- [x] Hide/disable selection overlay during mass video/GIF actions and close it after the final recording ends.
- [x] Increase recent-media thumbnails and keep the quick menu usable.

### Checkpoint: Group actions

- [x] Full component suite passes and recording windows remain independently controllable.

### Phase 3: Build and audit

- [x] Add GitHub Actions workflow for Windows compile checks and PyInstaller artifact build.
- [x] Audit hot paths for avoidable work without changing behavior.
- [x] Assess polygon/freeform regions; keep it as a separate follow-up because the current MP4/GIF contract is rectangular.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Windows apps expose only one standard image item from a clipboard | High | Keep the first image as the normal image, add a custom list payload, and add CF_HDROP-compatible file URLs for every image. |
| Hiding the overlay during group recording changes lifecycle behavior | High | Track group mode explicitly and close the overlay only after the last recording window emits its close signal. |
| Arbitrary polygon video capture requires per-frame masking | Medium | Do not claim support from a screenshot-only mask; add it only with a recorder test and a clear output contract. |
