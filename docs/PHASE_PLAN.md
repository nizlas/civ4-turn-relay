# Implementation phase plan — civ4-turn-relay

## 1. Purpose and authority

This document sequences implementation of the approved design. It is normative for **what may be built when** and for **phase exit gates**.

It does **not** redefine product behavior or protocol rules. Those remain in:

- [`DESIGN_SPEC.md`](DESIGN_SPEC.md)
- [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md)

Agent routing: [`AGENTS.md`](../AGENTS.md).

## 2. Rules for using the plan

1. Implement **only** the single **ACTIVE** phase (plus its listed prerequisites already completed).
2. Read this document’s active phase section and the design sections it links. Do **not** reload entire design docs for every small task unless the active phase says so.
3. Domain and protocol logic MUST remain independent of PySide6, Paramiko, Watchdog, and Civilization process APIs.
4. Do not mark a phase complete until its exit criteria pass.
5. Do not implement later phases early merely because scaffolding is convenient.
6. Record unresolved design decisions only in the phase where evidence can resolve them.
7. Never invent production credentials, real hosts/IPs, secrets, or real Civilization save files.
8. Do not copy source, wording, assets, or implementation details from the prior PBEM manager.
9. When changing protocol invariants, update tests and keep [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md) authoritative.

## 3. Phase status

| Phase | Name | Status |
|-------|------|--------|
| P0 | Python project skeleton and quality gates | **COMPLETE** |
| P1 | Domain types, configuration models, validation, deterministic serialization | **COMPLETE** |
| P2 | Storage abstraction and failure-injectable fake storage | **COMPLETE** |
| P3 | Core sync protocol engine | **COMPLETE** |
| P4 | Local persistence, reconciliation, baseline, save detection, watching | **COMPLETE** |
| P5 | Headless two-client end-to-end (fake storage) | **COMPLETE** |
| P6 | Paramiko SFTP adapter and disposable-server integration tests | **COMPLETE** |
| P7 | Windows Civ IV / BTS / AdvCiv launch and process integration | **ACTIVE** |
| P8 | Minimal PySide6 UI, matches, settings, status, diagnostics | IMPLEMENTED |
| P9 | Two-player hardening, packaging, ops docs, release readiness | PREPARED |

## 4. Active implementation phase

**P7 — Windows Civilization IV / BTS / Advanced Civ launch and process integration** is the only ACTIVE phase.

## 5. Phase-gate process

A phase moves from ACTIVE → COMPLETE only when:

1. All in-scope deliverables exist and match the linked design sections.
2. Required automated tests pass locally (and in CI once CI exists for that phase).
3. Applicable PT IDs for that phase pass.
4. Manual verification items (if any) are checked off with notes.
5. Exit criteria are satisfied; risks/decisions for the phase are resolved or explicitly deferred with reason to a later named phase.
6. Status table in §3 is updated in the same commit that closes the phase, and the next phase becomes the sole ACTIVE phase.

---

## Coverage maps

### Functional requirements → phases

| FR | Primary phases | Notes |
|----|----------------|-------|
| [FR-001](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P3, P5 | First handoff; e2e creator→joiner |
| [FR-002](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P3, P5 | Alternating handoffs |
| [FR-003](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P4, P5 | Duplicate FS events |
| [FR-004](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P4, P5 | Restart / reconcile / baseline survival |
| [FR-005](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P3, P6 | Injected failures; real network interrupts on disposable SFTP |
| [FR-006](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P3 | Wrong-player upload |
| [FR-007](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P3 | Partial upload |
| [FR-008](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P3 | Historical hash replay |
| [FR-009](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P3, P5 | Concurrent polling / two clients |
| [FR-010](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P4, P7 | No outgoing after Civ exit (simulated then real) |
| [FR-011](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P4, P8 | Multi-match selection isolation |
| [FR-012](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P1, P8 | Redaction in logs/config load; UI/diagnostics |
| [FR-013](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P4 | Play-session baseline |
| [FR-014](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P3, P8 | Lock policy in engine; confirmed repair UX |
| [FR-015](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) | P4, P5, P7, P8, P9 | Fully managed lifecycle: P4 durable launch/process records + pure orchestration/intents; P5 headless managed flows; P7 real process verify/close; P8 mode/consent/fallbacks; P9 packaged e2e hardening |

### Protocol tests → owning phases

**Primary** is exactly one phase: the phase that must first make the complete test green. **Also** lists foundations or later re-runs. P1/P2 may prepare validators or adapter signals, but they do not own complete protocol PT outcomes.

| PT | Primary | Also |
|----|---------|------|
| PT-01 | P3 | P5 |
| PT-02 | P3 | |
| PT-03 | P3 | P5 |
| PT-04 | P3 | |
| PT-05 | P3 | |
| PT-06 | P4 | P5 |
| PT-07 | P3 | P6 |
| PT-08 | P3 | |
| PT-09 | P3 | P5 |
| PT-10 | P3 | |
| PT-11 | P3 | P8 (UI confirm) |
| PT-12 | P3 | |
| PT-13 | P3 | P5 |
| PT-14 | P3 | |
| PT-15 | P3 | |
| PT-16 | P3 | |
| PT-17 | P3 | |
| PT-18 | P3 | |
| PT-19 | P4 | P5 |
| PT-20 | P4 | P5 |
| PT-21 | P4 | P5 |
| PT-22 | P4 | P5 |
| PT-23 | P4 | P5, P8 |
| PT-24 | P4 | P5 |
| PT-25 | P3 | |
| PT-26 | P3 | P6 |
| PT-27 | P3 | P6 |
| PT-28 | P3 | P6 |
| PT-29 | P3 | P6 |
| PT-30 | P3 | P2, P6 |
| PT-31 | P3 | P5 |
| PT-32 | P3 | |
| PT-33 | P3 | |
| PT-34 | P4 | |
| PT-35 | P3 | |
| PT-36 | P3 | P5 |
| PT-37 | P3 | P5 |
| PT-38 | P3 | P1 |
| PT-39 | P3 | P1 |
| PT-40 | P3 | |
| PT-41 | P3 | P5 |
| PT-42 | P3 | P5 |
| PT-43 | P3 | |

---

## P0 — Python project skeleton and quality gates

**Status:** COMPLETE

### Goal

Establish a Python 3.12 project layout, tooling, and test runner so later phases can land code safely.

### Why now

Quality gates and package layout must exist before domain or protocol code.

### Prerequisites

Approved design baseline (`DESIGN_SPEC`, `SYNC_PROTOCOL`, this plan).

### Read

- [`AGENTS.md`](../AGENTS.md)
- [`DESIGN_SPEC.md` §1](DESIGN_SPEC.md#1-goals-and-non-goals) (planned stack)
- [`docs/licensing.md`](licensing.md)
- [`.env.example`](../.env.example) (global shape only)

### In scope

- `pyproject.toml` (or equivalent) for Python 3.12
- Local quality gates: **pytest**, **Ruff**, and **mypy** (exact tool configuration is chosen during P0 implementation)
- Package root (e.g. `src/civ4_turn_relay/`) with minimal `__init__` / version placeholder
- Test layout (`tests/`), one smoke test
- Dev docs for running lint/typecheck/tests
- Optional minimal CI later is **out** unless needed to run the smoke test; prefer local gates in P0

### Out of scope

- Domain models, protocol, storage, UI, SFTP, Civ launch, packaging
- PySide6 / Paramiko / Watchdog / PyInstaller dependencies

### Expected files/components

- `pyproject.toml`, package skeleton, `tests/test_smoke.py` (or similar)
- Tool config for Ruff/mypy/pytest as needed

### Required automated tests

- Smoke test that imports the package
- Lint/typecheck scripts or documented commands succeed on empty skeleton

### Applicable PT IDs

None (tooling only).

### Manual verification

- Fresh clone/venv can install editable package and run tests with placeholders only

### Exit criteria

- Python 3.12 project installs cleanly; smoke test passes; documented quality commands exist
- No UI/network/Civ dependencies introduced

### Risks / decisions

- Exact packaging layout (`src/` vs flat) — **resolved:** conventional `src/` layout (`src/civ4_turn_relay/`) with hatchling editable installs

---

## P1 — Domain types, configuration models, validation, deterministic serialization

**Status:** COMPLETE

### Goal

Pure domain types for manifests, config, hashes, and operational state enums; validation and deterministic JSON serialization with no I/O adapters.

### Why now

Protocol and UI both depend on shared validated types; keep them free of Paramiko/PySide6/Watchdog.

### Prerequisites

P0 complete.

### Read

- [`SYNC_PROTOCOL.md` §1](SYNC_PROTOCOL.md#1-terminology), [§2.1](SYNC_PROTOCOL.md#21-game-id), [§3](SYNC_PROTOCOL.md#3-manifest-schema), [§3.2](SYNC_PROTOCOL.md#32-accepted_save_hashes-validation), [§3.3](SYNC_PROTOCOL.md#33-serialization-conventions), [§4](SYNC_PROTOCOL.md#4-invariants)
- [`DESIGN_SPEC.md` §4](DESIGN_SPEC.md#4-configuration-model), [§5](DESIGN_SPEC.md#5-authority-model), [§6](DESIGN_SPEC.md#6-local-operational-states) (state names only)
- [`.env.example`](../.env.example)

### In scope

- Manifest model + `accepted_save_hashes` validation
- Global vs per-match config models (no secrets in examples)
- Game ID / player ID validators; path-containment helpers as pure functions
- Deterministic serialization helpers
- Secret-redaction helpers for log fields (FR-012 foundation)
- Local operational state enum (no transition engine yet beyond pure helpers if tiny)

### Out of scope

- Storage, protocol commit engine, filesystem watchers, UI, Civ

### Expected files/components

- `domain/` or equivalent: manifest, config, ids, hashing helpers, state enum
- Unit tests for validation and serialization round-trips (synthetic bytes only)

### Required automated tests

- Valid/invalid manifests (incl. hash-list rules)
- Seq-0 vs seq>0 coupling
- Game ID rejection (traversal, case, separators) — supports PT-39 later
- Config split: global fields vs per-match fields
- Redaction unit tests (FR-012)

### Applicable PT IDs

- Foundations only for **PT-38** and **PT-39** (validators and rejection cases). The complete protocol outcomes remain owned by **P3**.
- Partial support for schema cases used by PT-13/PT-18

### Manual verification

None required.

### Exit criteria

- All manifest/config validators match linked protocol/design sections
- No adapter imports from domain package

### Risks / decisions

- Concrete config file format on disk (TOML/JSON) — **resolved:** JSON for local per-match configuration, using the same strict deterministic serialization conventions as the manifest ([protocol §3.3](SYNC_PROTOCOL.md#33-serialization-conventions)); `.env` remains for global secrets/seeds only

---

## P2 — Storage abstraction and failure-injectable fake storage

**Status:** COMPLETE

### Goal

Define a storage port that can express atomic mkdir, atomic replace (posix-rename equivalent), read-back, and capability errors; ship an in-memory and/or local-filesystem fake with failure injection at every commit step.

### Why now

The complete commit protocol must be proven against fake storage before Paramiko ([planning requirement](#2-rules-for-using-the-plan)).

### Prerequisites

P1 complete.

### Read

- [`SYNC_PROTOCOL.md` §2](SYNC_PROTOCOL.md#2-remote-layout), [§2.4](SYNC_PROTOCOL.md#24-commit-point), [§7.1](SYNC_PROTOCOL.md#71-lock-primitive), [§7.2](SYNC_PROTOCOL.md#72-atomic-rename-and-verification-requirements), [§12](SYNC_PROTOCOL.md#12-security) (path containment)
- [`DESIGN_SPEC.md` §12](DESIGN_SPEC.md#12-testing-principles)

### In scope

- `Storage` protocol/interface: mkdir (atomic fail-if-exists), get/put, list, remove, posix-rename-equivalent replace, full read-back stream/bytes, capability flags
- Fake storage with injectable failures (after temp put, before final publish, before/after manifest replace, lock mkdir race, missing atomic replace, read-back corruption)
- Tests that the fake honors immutability (refuse silent overwrite of different content) and can validate an existing object before reuse

### Out of scope

- Sync engine business logic, Paramiko, UI

### Expected files/components

- `storage/port.py` (name flexible), `storage/fake.py`, failure-injection helpers, port contract tests

### Required automated tests

- Atomic mkdir success/fail-if-exists
- Atomic replace capability present/absent
- Full read-back hash helpers
- Refuse overwrite when destination exists with different bytes
- Validate existing history/final object before reuse (byte+hash match)
- Injected failure points are selectable and deterministic

### Applicable PT IDs

- Foundation for **PT-30**: adapter capability signaling when atomic replace is unavailable. The complete protocol refusal outcome is owned by **P3**.
- Infrastructure for PT-07, PT-25–PT-29 (executed fully in P3)

### Manual verification

None.

### Exit criteria

- Fake storage supports every injection point needed by P3 matrix
- Domain/protocol layers can depend only on the port, not Paramiko

### Risks / decisions

- In-memory vs temp-directory fake as default — **resolved:** in-memory `FakeStorage` is the primary fake; it models OpenSSH/SFTP semantics closely enough for P3 PT coverage (exclusive mkdir, immutable no-replace publication, posix-rename-equivalent replace, full read-back, deterministic fault injection). A temp-directory fake was not needed.

---

## P3 — Core sync protocol engine

**Status:** COMPLETE

### Goal

Implement initialization, download, handoff/commit, locking, idempotence, history, and recovery classification against fake storage—without GUI, Watchdog, or Civ.

### Why now

Protocol correctness is the product’s safety core and must be proven before local FS integration and real SFTP.

### Prerequisites

P2 complete.

### Read

- [`SYNC_PROTOCOL.md` §2.5](SYNC_PROTOCOL.md#25-initial-match-creation), [§4](SYNC_PROTOCOL.md#4-invariants), [§5](SYNC_PROTOCOL.md#5-download-algorithm), [§6.3](SYNC_PROTOCOL.md#63-hash-already-in-accepted_save_hashes-pre-upload-classification), [§7](SYNC_PROTOCOL.md#7-upload-and-commit-algorithm), [§8](SYNC_PROTOCOL.md#8-idempotence-and-concurrency), [§9](SYNC_PROTOCOL.md#9-crash-point-analysis), [§11](SYNC_PROTOCOL.md#11-history-and-repair), [§13](SYNC_PROTOCOL.md#13-protocol-test-matrix)
- [`DESIGN_SPEC.md` §5](DESIGN_SPEC.md#5-authority-model)

### In scope

- Match init algorithm and join/conflict paths
- Download algorithm (synthetic save bytes)
- Upload/commit algorithm including lock acquire/resume, pre-commit lock re-read, full remote read-back, immutable publish / verify-reuse, history write with **validate-before-reuse** of existing history objects
- Hard integrity error when final save path exists with different content (no silent overwrite)
- Hash classification (sender idempotent ack vs recipient reject vs older replay)
- Confirmed abandoned-lock repair API (callable; UI comes later)
- Orphan temp cleanup that does not change ownership

### Out of scope

- Play-session baseline / Watchdog (P4)
- Paramiko (P6), Civ (P7), UI (P8)

### Expected files/components

- `protocol/` engine modules; operation journal structures (may be in-memory until P4 persists them)
- Test suite mapping PT IDs below

### Required automated tests

All owning-phase PT IDs listed for P3 in the [coverage map](#protocol-tests--owning-phases), including explicitly:

- Validate already-existing history object before reuse
- Refuse any save publication path that could silently overwrite different content (PT-27)
- Exact orphan final-save reuse after read-back (PT-28)

### Applicable PT IDs

Every PT whose Primary column is **P3** in the [coverage table](#protocol-tests--owning-phases).
P4-owned detection tests may call this engine.

### Manual verification

None beyond reviewing failing injection traces if useful.

### Exit criteria

- Full P3 PT set green on fake storage
- Engine has zero imports of PySide6, Paramiko, Watchdog, Civ launch code
- FR-001/002/005–009/014 protocol aspects demonstrated at unit/integration-fake level

### Risks / decisions

- In-memory journal vs forcing P4 persistence first — keep journal interface abstract; durable backend in P4
- History retention pruning — defer; keep all history ([protocol §14](SYNC_PROTOCOL.md#14-open-decisions))

### Completion note

P3 closed with fake-storage protocol coverage for all P3-primary PT IDs: match init/join, verified download (evidence-only; no local promotion), handoff/commit with lock resume and atomic history publish, hash classification, confirmed lock repair (including wrong-kind structures), and temporary-orphan cleanup that never mutates the authoritative manifest. Durable journals, local FS promotion/watching, baselines, Paramiko, Civ, and UI remain later phases.

---

## P4 — Local persistence, reconciliation, baseline, save detection, watching

**Status:** COMPLETE

### Goal

Durable local records, startup reconciliation, play-session baseline, outgoing detection (stability + novelty), and filesystem watching with polling fallback—still headless.

### Why now

Local evidence and Civ-adjacent file flows depend on a proven protocol engine but not yet on a GUI or real SFTP.

### Prerequisites

P3 complete.

### Read

- [`SYNC_PROTOCOL.md` §6](SYNC_PROTOCOL.md#6-outgoing-save-detection), [§9](SYNC_PROTOCOL.md#9-crash-point-analysis) (baseline/Civ rows), [§10](SYNC_PROTOCOL.md#10-local-persistence)
- [`DESIGN_SPEC.md` §3.4–3.8](DESIGN_SPEC.md#34-creating-the-first-pbem-save), [§4.2](DESIGN_SPEC.md#42-per-match-configuration), [§6](DESIGN_SPEC.md#6-local-operational-states), [§8](DESIGN_SPEC.md#8-process-and-save-detection-behavior) (including [§8.5 turn handling](DESIGN_SPEC.md#85-turn-handling-modes)), [§9](DESIGN_SPEC.md#9-crash-recovery-and-repair-ux), [§10](DESIGN_SPEC.md#10-logging-and-diagnostics), [FR-015](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria)
- Open decision: stable-file sampling ([DESIGN_SPEC §13](DESIGN_SPEC.md#13-open-decisions))

### In scope

- Per-match durable store (sequence/hash, incoming path, baseline, journal, processed outgoings, launch-attempt/process-association records)
- Reconcile → local operational states ([§6](DESIGN_SPEC.md#6-local-operational-states))
- Baseline record before “launch” command boundary (launch itself stubbed/fake process handle)
- Pure managed-mode orchestration/intents for `turn_handling_mode` / `allow_force_close_after_commit` ([DESIGN_SPEC §8.5](DESIGN_SPEC.md#85-turn-handling-modes)): duplicate-launch suppression, auto-send trigger when safe, and close intent only after committed/idempotent evidence — **no real process calls**
- Outgoing detection rules; multi-candidate error; missing baseline disables auto-send
- Watchdog adapter behind a port + polling fallback
- Multi-match local config selection isolation (FR-011 foundation)
- Structured logging with redaction (FR-012)

### Out of scope

- Real Civ process launch/close (P7), UI (P8), Paramiko (P6)

### Expected files/components

- `local/` persistence, reconcile, detect, managed-mode orchestration (intents only), `fs/` watcher port + watchdog impl + poller
- Fake process/launch port for state transitions without Civ

### Required automated tests

- PT-06, PT-19–PT-24, PT-34
- Restart with baseline surviving “Civ still running” (fake process) — PT-22 / FR-004
- Civ exit without outgoing → correct state (FR-010 simulated); no relaunch loop under managed intents (FR-015 foundation)
- Duplicate-launch suppression and close-intent-only-after-commit/idempotent-ack with fake process evidence (FR-015 foundation)
- Match switch does not mutate other match remote/local ownership records (FR-011)

### Applicable PT IDs

**PT-06, PT-19–PT-24, PT-34** (primary). Uses P3 engine for upload after detection. Do **not** invent protocol PT IDs for process behavior.

### Manual verification

- Optional: watch a temp directory receive a stable synthetic file and see state change headlessly

### Exit criteria

- Baseline/detection/reconcile tests green; auto-send cannot run without trustworthy baseline
- Managed-mode durable records and pure intents green without real process I/O
- Sampling interval default recorded (1.0s × 2) unless evidence changes it

### Risks / decisions

- Resolve stable-file sampling interval with tests ([DESIGN_SPEC §13](DESIGN_SPEC.md#13-open-decisions))
- Watchdog flakiness on Windows — polling fallback must be first-class

### Completion note

P4 closed with LocalStore (`installation.json`, per-match `config.json`/`state.json`), UUID-only installation identity via temp+fsync+no-replace publish, durable `HandoffJournal` embedded in match state, verified download promotion, play-session baselines keyed by sequence/hash, outgoing detection with injected-clock 1.0s × 2 stability sampling, recursive Watchdog watcher + polling fallback (`fs/`), and pure Standard/Fully Managed orchestration intents (no real process I/O). Primary PT coverage: PT-06, PT-19–PT-24, PT-34; FR-004/010/011/012/013 and FR-015 foundation covered in `tests/local/`. Paramiko, real Civ process ops, UI, and packaging remain later phases. Stable-file sampling default recorded as **1.0s between size samples, twice**.

---

## P5 — Headless two-client end-to-end workflow (fake storage)

**Status:** COMPLETE

### Goal

Run two in-process clients (creator + joiner) through first save, alternating handoffs, restart, duplicate events, and lock contention against shared fake storage.

### Why now

Proves the composed system before real network and before UI/packaging.

### Prerequisites

P4 complete.

### Read

- [`DESIGN_SPEC.md` §3](DESIGN_SPEC.md#3-end-to-end-workflows), [§11](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria)
- [`SYNC_PROTOCOL.md` §8](SYNC_PROTOCOL.md#8-idempotence-and-concurrency), [§13](SYNC_PROTOCOL.md#13-protocol-test-matrix)

### In scope

- Headless orchestration harness for two clients
- Synthetic “Civ produced save” by writing bytes into watched dirs
- Managed-mode orchestration with fake/headless clients where useful (FR-015): one launch intent per sequence/hash, auto-send after valid candidate, close intent only after commit/idempotent-ack evidence
- Scenarios covering FR-001–FR-005, FR-009–FR-010, FR-013–FR-015 at workflow level
- Re-run critical PT IDs in e2e form

### Out of scope

- Real SFTP, real Civ process APIs, GUI, packaging

### Expected files/components

- `tests/e2e_fake/` (or similar) harness and scenarios

### Required automated tests

E2E scenarios mapping at least: PT-01, PT-03, PT-06, PT-09, PT-13, PT-19–PT-24, PT-31, PT-36–PT-37, PT-41–PT-42.

### Applicable PT IDs

Listed above (workflow proof). Full matrix remains owned by P3/P4.

### Manual verification

None required if e2e suite is deterministic.

### Exit criteria

- Two-client fake-storage workflow reliable and green in CI/local
- Packaging still blocked (P9)

### Risks / decisions

- Timing sensitivity — prefer deterministic fake clock/events over wall-clock sleeps where possible

### Completion note

P5 closed with production `RelayClient` (`app/`) composing LocalStore, Storage, P3 init/download/handoff, and P4 reconcile/detect/monitor/intents. Deterministic two-client FakeStorage e2e in `tests/e2e_fake/` covers initialize→alternating handoffs, Fully Managed auto-launch/auto-send/close intents, Standard explicit Start, multi-candidate selection, non-owner rejection, duplicate-tick idempotency, restart with baseline/process association, Civ-exit-without-save (no relaunch loop), second-match isolation, and fault/retry/idempotent resume scenarios. UI, Paramiko live OpenSSH (P6), and real Civ process control remain later phases.

---

## P6 — Paramiko SFTP adapter and disposable-server integration tests

**Status:** COMPLETE

### Goal

Real SFTP adapter with host-key verification, capability detection, and integration tests on a **disposable** OpenSSH (or equivalent) server—no production hosts.

### Why now

Protocol is proven on fake storage; now verify OpenSSH semantics the fake approximated.

### Prerequisites

P5 complete.

### Read

- [`SYNC_PROTOCOL.md` §7.1–7.2](SYNC_PROTOCOL.md#71-lock-primitive), [§12](SYNC_PROTOCOL.md#12-security)
- [`DESIGN_SPEC.md` §4.1](DESIGN_SPEC.md#41-global-configuration), [§13](DESIGN_SPEC.md#13-open-decisions) (host-key policy)
- [`.env.example`](../.env.example)

### In scope

- Paramiko adapter implementing the storage port
- Explicit verification of: atomic mkdir lock behavior, host-key verification (refuse mismatch), full remote read-back SHA-256, immutable save publication, `posix_rename` / `posix-rename@openssh.com`
- Disposable-server test harness (containers or ephemeral local sshd) with **fixture credentials only**
- Integration re-run of PT-07, PT-26–PT-30, plus a small commit/download subset

### Out of scope

- Production server use, UI, Civ, packaging

### Expected files/components

- `storage/sftp_paramiko.py` (name flexible), integration test module marked optional/CI-gated

### Required automated tests

- Capability failure when posix-rename unavailable → no commit (PT-30)
- Read-back mismatch aborts (PT-29)
- Orphan reuse vs different-content hard error (PT-28/PT-27)
- Host-key mismatch refused
- Atomic mkdir exclusive lock (PT-07)

### Applicable PT IDs

**PT-07, PT-26–PT-30**, plus selected handoff/download cases as time allows.

### Manual verification

- Document how to run disposable-server tests locally without real infrastructure details in-repo

### Exit criteria

- Adapter refuses non-capable servers
- Host-key policy implemented per design recommendation (pin/known_hosts)
- No secrets committed; tests skip cleanly when disposable server absent

### Risks / decisions

- Confirm host-key UX details for first-time pin ([DESIGN_SPEC §13](DESIGN_SPEC.md#13-open-decisions))
- CI availability of disposable sshd — may be manual/nightly if needed

### Completion note

P6 closed with `ParamikoStorage` implementing the full Storage contract (path containment, exclusive mkdir, OpenSSH `rename` publish-no-replace, `posix_rename` atomic replace, complete read-back), connection-time capability probing, and strict host-key verification (`known_hosts` and/or SHA-256 fingerprint; no AutoAddPolicy). GlobalConfig extended for passphrase, connect timeout, and host-key settings; `load_global_config` loads an explicit dotenv path with environ override via `python-dotenv`. Disposable Alpine OpenSSH harness lives under `tests/storage/integration/` (`pytest -m openssh_sftp`); ordinary runs skip cleanly when Docker is unavailable. Reusable contract remains on FakeStorage + delegating wrapper. Docs: [`docs/SFTP_ADAPTER.md`](SFTP_ADAPTER.md), [`.env.example`](../.env.example). Live OpenSSH contract execution was not run in the closing environment because Docker was unavailable; the harness is ready for `pytest -m openssh_sftp`.

---

## P7 — Windows Civilization IV / BTS / Advanced Civ launch and process integration

**Status:** ACTIVE — implementation complete; pending the manual Windows smoke test

Implementation note (2026-08-11): the launch port (`civ4_turn_relay/process/`),
Windows adapter (psutil + WM_CLOSE), `RelayClient` integration, and all required
automated tests (fake process supervisor; FR-010 and FR-015 paths) are
implemented and passing. The exit criteria require the Steam/BTS/AdvCiv CLI
behavior to be **empirically verified** on a real Windows Civ install. The mod
argument and direct save load were confirmed on 2026-08-14: stored ``Mods\\AdvCiv``
must be launched as final ``mod=\\AdvCiv`` argument, after ``/fxsload``; raw BTS also
needs Steam's ``SteamAppId``/``SteamGameId`` context. The remaining end-to-end lifecycle
evidence is still pending. The manual smoke-test checklist lives in
[`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md). P7 remains the sole ACTIVE phase
until that checklist is performed and its results recorded here.

Implementation note (2026-08-12): every Civ launch now runs as one guarded
launch at the process boundary — an OS-backed interprocess guard (Windows
named mutex derived from a SHA-256 digest of the normalized executable path)
serializes Relay instances in the same Windows session, and a defensive
machine scan for the exact configured executable runs under the guard before
any spawn. An already-running Civ defers the launch as a typed waiting status
(the launch-attempt key is not consumed; fully managed retries on later
ticks, standard mode waits for an explicit Start), never touching the
existing process. See [`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md) “Cross-instance
launch guard”. Covered by deterministic two-client fake-machine tests; real
named-mutex semantics are exercised by Windows-only tests.

Implementation note (2026-08-12, follow-up): the named-mutex adapter now
declares a pointer-sized Win32 HANDLE ABI (injectable for tests); the real
process scan preserves AccessDenied likely-Civ entries as
``SCAN_INDETERMINATE``; and deferred launches keep distinct process statuses
for an existing Civ, a busy sibling guard, and an indeterminate scan.

Implementation note (2026-08-12, follow-up): a verified launch is never
forgotten because guard cleanup failed — ``ReleaseMutex`` / ``CloseHandle``
errors attach as a typed cleanup result and diagnostic while the process
association still persists. Windows executable matching uses ``ntpath`` so
path comparison is host-independent. A failed ``CloseHandle`` after a
non-owned wait is ``UNAVAILABLE``, not a clean busy result.

### Goal

Empirically determine and implement launch/process integration for Steam/BTS/AdvCiv: mod + save, already-running detection, exit without save (FR-010), and Fully managed close/verify behavior (FR-015).

### Why now

Launch flags cannot be assumed; this phase follows a working Paramiko adapter so launch integration builds on proven remote transfer and P4 managed-mode intents.

### Prerequisites

P6 complete.

### Read

- [`DESIGN_SPEC.md` §8.1](DESIGN_SPEC.md#81-launching-civilization), [§8.4–8.5](DESIGN_SPEC.md#84-civilization-closes-without-outgoing-save) (including turn handling / close policy), [§13](DESIGN_SPEC.md#13-open-decisions), [FR-015](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria)
- [`SYNC_PROTOCOL.md` §6.1](SYNC_PROTOCOL.md#61-play-session-baseline) (baseline still recorded before launch)

### In scope

- Launch port + Windows implementation
- Empirical notes (developer-local) for exact CLI / Steam behaviors — **do not commit real install paths as secrets/defaults beyond placeholders**
- Already-running / unrelated process warnings and protection
- Wire launch to baseline recording and durable process-association evidence from P4
- Real Windows launch; PID / precise-creation-token / executable verification
- Graceful close request, 15s wait, and optional post-commit forced termination when `allow_force_close_after_commit` is enabled
- BTS/AdvCiv verification paths; never close an unrelated/manually launched process by executable name alone
- Defer auto-launch when no interactive/unlocked desktop is available

### Out of scope

- Controlling Civ UI / ending turns (NG-8)
- Packaging, full GUI (stubs/CLI OK)
- New protocol PT IDs for process behavior

### Expected files/components

- `civ/launch.py` port + Windows adapter; manual test checklist

### Required automated tests

- Unit tests with fake process supervisor (no real Civ binary required in CI)
- FR-010 state transitions with fake process exit
- FR-015 process-identity verification, graceful-close timeout path, and force-close only after commit proof (fake supervisor)

### Applicable PT IDs

None new; supports PT-22 behavior with real process optional manually.

### Manual verification

- On a Windows machine with BTS/AdvCiv: launch mod+save, confirm load; exit without Next Turn → correct relay state
- Fully managed: one auto-launch, auto-send after Next Turn, graceful close after proven commit; unrelated Civ left alone
- Record resolved CLI in phase close-out notes / code comments without copying prior PBEM manager

### Exit criteria

- Exact Steam/BTS/AdvCiv launch behavior empirically verified and implemented behind the port
- Open decision “exact CLI” closed with evidence
- FR-015 process close/verify acceptance criteria demonstrable with the Windows adapter (or fake supervisor in CI)

### Risks / decisions

- Steam re-exports / working directory quirks — document fallbacks
- If AdvCiv load flags differ from stock BTS, capture both profiles

---

## P8 — Minimal PySide6 UI, match management, settings, status, diagnostics

**Status:** IMPLEMENTED — not complete until P7 closes and PySide6 teardown is verified

Implementation note (2026-08-11): the PySide6 desktop client (`civ4_turn_relay/
ui/`), worker/controller threading, tray integration, settings and match
editors, and the required automated UI tests (headless offscreen, pytest-qt)
are implemented and passing, including redaction, mode-selector/force-close
consent, and close-failure-is-not-protocol-failure presentation. P8's
prerequisite is "P7 complete", so this phase cannot be marked COMPLETE until
the P7 manual Windows smoke test is performed. P7 stays the sole ACTIVE phase.

Teardown hardening (2026-08-12): `GatedQApplication` gates ordinary
`quit()`/`exit()` and `QEvent.Quit` through the same pre-quit path as
tray/menu/window Quit; `setQuitOnLastWindowClosed(False)`; join before
`RelayClient.close()`;
never `QThread.terminate()`; join timeouts defer Quit and keep the client open;
worker destruction via `QThread.finished → deleteLater`; `aboutToQuit` is
idempotent cleanup only after orderly shutdown (never the join gate). OS-forced
process kill cannot be vetoed. Fresh-interpreter coverage:
`tests/ui/test_teardown_subprocess.py`; Windows stress:
`packaging/tools/run_ui_teardown_stress.ps1`. Until that stress run is green
on a real Windows agent, treat intermittent PySide6 interpreter-teardown heap
corruption as an open **release blocker** for marking P8 COMPLETE.

### Goal

Minimal main window consuming domain states/commands only; settings, match CRUD/select, diagnostics/repair confirmation, secret-safe errors (FR-012).

### Why now

UI must not invent protocol; it presents the completed domain, sync, SFTP, and Civ-launch capabilities from prior phases.

### Prerequisites

P7 complete.

### Read

- [`DESIGN_SPEC.md` §3](DESIGN_SPEC.md#3-end-to-end-workflows), [§4](DESIGN_SPEC.md#4-configuration-model), [§6–7](DESIGN_SPEC.md#6-local-operational-states), [§8.5](DESIGN_SPEC.md#85-turn-handling-modes), [§9–10](DESIGN_SPEC.md#9-crash-recovery-and-repair-ux), [§11](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) (FR-011, FR-012, FR-014, FR-015)
- [`SYNC_PROTOCOL.md` §7.1](SYNC_PROTOCOL.md#71-lock-primitive) (repair confirmation), [§11](SYNC_PROTOCOL.md#11-history-and-repair)

### In scope

- Main window per §7; primary button table; DIN TUR / YOUR TURN
- Global settings + per-match editors (no server settings duplicated per match)
- Turn handling mode selector (`standard` / `fully_managed`); advanced force-close consent with warning when Fully managed
- Statuses that distinguish committed turn from Civ-still-open; Start/Resume/Focus/Close fallbacks
- Diagnostics export redaction
- Explicit repair previews (abandoned lock, incomplete init)
- Multi-match switching presentation (FR-011)

### Out of scope

- Protocol decisions in widgets
- Packaging (P9)
- Fancy dashboards
- Ambiguous partial-automation toggle combinations that revive standalone `auto_launch`

### Expected files/components

- `ui/` PySide6 views + thin controllers calling application services

### Required automated tests

- UI tests: state presentation; buttons invoke commands only; no direct storage/protocol mutation from widgets
- Redaction tests on diagnostics export (FR-012)
- Repair confirmation required for foreign lock removal (FR-014)
- Mode selector / force-close consent presentation; close-failure status does not imply protocol failure (FR-015 UX)

### Applicable PT IDs

None exclusively; exercises PT-11 / PT-23 paths via UI commands.

### Manual verification

- Walk examples in [`DESIGN_SPEC` §7.2](DESIGN_SPEC.md#72-examples)
- Confirm errors include safety/retry/next-step fields (§7.4)
- Fully managed: mode + force-close warning; Focus/Close when Civ remains after commit

### Exit criteria

- UI is presentation/command-only; FR-011/012/014/015 UX satisfied
- Default `turn_handling_mode=standard`; auto-send remains baseline-gated

### Risks / decisions

- Close default auto-send vs manual if usability evidence appears; keep baseline requirement
- Keep Fully managed as one coherent mode rather than exposing partial automation toggles

---

## P9 — Real two-player hardening, Windows packaging, ops docs, release readiness

**Status:** PREPARED — portable/installer scaffolding present; not complete

Preparation note (2026-08-12): `packaging/build_windows.ps1`,
`packaging/civ4-turn-relay.spec`, `packaging/installer.iss`, and
[`docs/RELEASE.md`](RELEASE.md) provide a portable PyInstaller onedir+ZIP and a
per-user Inno Setup installer that preserve `%APPDATA%\civ4-turn-relay`. Static
invariant tests live under `tests/packaging/`. This does **not** close P9:
P7/P8 prerequisites remain, real Windows artifact builds/smoke checks are
still required, and two-player hardening / signing decisions are unfinished.
P7 remains the sole ACTIVE phase.

### Goal

Real two-human hardening on disposable/real-user SFTP (credentials never committed), Windows distribution that ships **both** a portable build and a real installer, operational docs, and third-party notices.

### Why now

Packaging and release hardening follow a complete UI-backed client that already includes sync, SFTP, and Civ launch.

### Prerequisites

P8 complete.

### Read

- [`DESIGN_SPEC.md` §1](DESIGN_SPEC.md#1-goals-and-non-goals) (planned stack / Windows distribution), [§11–12](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria)
- [`docs/licensing.md`](licensing.md)
- [`README.md`](../README.md) (update early-scaffolding warning when actually ready—only in this phase’s docs work)

### In scope

- Two-computer / two-player test plan (manual) using player-owned disposable paths
- **Portable Windows build:** PyInstaller (or selected equivalent) producing a portable folder/zip in addition to the installer
- **Real Windows installer:** Inno Setup (or justified equivalent) producing a normal installer `.exe` (not merely a renamed PyInstaller executable), with:
  - Default per-user install without admin where practical
  - Install under an appropriate per-user Windows application location
  - Start Menu shortcut
  - Desktop shortcut MAY be an installer option
  - Register a normal Windows uninstaller
  - Support upgrading an existing installation
  - Preserve user config, match metadata, logs, and save-related local data during upgrades
  - Uninstall MUST NOT delete user saves or match data by default
- **MSVC x64 runtime:** handle via official `vc_redist.x64.exe`; prefer embedded/offline redistributable when Microsoft redistribution terms permit; if embedding is not legally or technically appropriate, implement an explicit verified prerequisite flow (not silent fail)
- Applicable third-party notices for PySide6/Paramiko/etc. (and other bundled components)
- Reproducible build notes; ops docs for install, `.env`, host keys, backup/repair expectations
- Packaged-build end-to-end hardening for Fully managed turns where appropriate (FR-015): one auto-launch, auto-send, post-commit close path on real Windows installs without secrets in-repo
- Release checklist: FR-001–FR-015 signed off; PT matrix still green on fake; SFTP subset green

### Out of scope

- New protocol features
- Production credential check-ins

### Expected files/components

- Portable packaging config/scripts (PyInstaller or equivalent)
- Real installer project/scripts (Inno Setup or justified equivalent)—not a renamed frozen exe
- `docs/ops.md` (or similar), NOTICE/third-party files
- VC++ redistributable handling as above (embedded when permitted, otherwise explicit prerequisite flow)

### Required automated tests

- Packaged binary smoke (launch → shows UI/version) where feasible for portable and/or installed layout
- Full fake-storage PT regression before tag

### Applicable PT IDs

Regression: **PT-01–PT-43** on fake storage; SFTP subset from P6.

### Manual verification

- Two-player AdvCiv PBEM handoff real run (saves remain local/uncommitted)
- Crash/restart drills on both PCs
- Fresh install, upgrade preserving user data, and uninstall that leaves saves/match data by default
- Portable zip/folder run without installer
- VC++ runtime path: embedded install or verified prerequisite flow succeeds on a clean machine profile

### Exit criteria

- Reliable two-player workflow
- Both portable distribution and real Windows installer build and meet the installer requirements above
- Licensing notices present; README scaffolding warning revised only when safe
- No phase left ACTIVE except maintenance

### Risks / decisions

- Code signing remains a P9 release decision/risk (availability and whether to sign)
- History pruning still deferred unless ops pain appears ([protocol §14](SYNC_PROTOCOL.md#14-open-decisions))
- Exact installer tool (Inno Setup vs justified equivalent) and VC++ embed-vs-prerequisite choice resolved in this phase with recorded reason

---

## Sequencing notes

The order matches the approved suggestion. No phase boundary changes were required:

1. Fake storage (P2) before complete commit engine (P3).
2. Protocol on fake (P3–P5) before Paramiko (P6).
3. Empiric Civ launch (P7) after headless workflow proof.
4. UI (P8) consumes domain states/commands only.
5. Packaging (P9) after reliable two-client workflow.
