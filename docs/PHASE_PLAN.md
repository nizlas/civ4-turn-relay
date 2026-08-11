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
| P3 | Core sync protocol engine | **ACTIVE** |
| P4 | Local persistence, reconciliation, baseline, save detection, watching | NOT STARTED |
| P5 | Headless two-client end-to-end (fake storage) | NOT STARTED |
| P6 | Paramiko SFTP adapter and disposable-server integration tests | NOT STARTED |
| P7 | Windows Civ IV / BTS / AdvCiv launch and process integration | NOT STARTED |
| P8 | Minimal PySide6 UI, matches, settings, status, diagnostics | NOT STARTED |
| P9 | Two-player hardening, packaging, ops docs, release readiness | NOT STARTED |

## 4. Active implementation phase

**P3 — Core sync protocol engine** is the only ACTIVE phase.

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

**Status:** ACTIVE

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

---

## P4 — Local persistence, reconciliation, baseline, save detection, watching

**Status:** NOT STARTED

### Goal

Durable local records, startup reconciliation, play-session baseline, outgoing detection (stability + novelty), and filesystem watching with polling fallback—still headless.

### Why now

Local evidence and Civ-adjacent file flows depend on a proven protocol engine but not yet on a GUI or real SFTP.

### Prerequisites

P3 complete.

### Read

- [`SYNC_PROTOCOL.md` §6](SYNC_PROTOCOL.md#6-outgoing-save-detection), [§9](SYNC_PROTOCOL.md#9-crash-point-analysis) (baseline/Civ rows), [§10](SYNC_PROTOCOL.md#10-local-persistence)
- [`DESIGN_SPEC.md` §3.4–3.8](DESIGN_SPEC.md#34-creating-the-first-pbem-save), [§6](DESIGN_SPEC.md#6-local-operational-states), [§8](DESIGN_SPEC.md#8-process-and-save-detection-behavior), [§9](DESIGN_SPEC.md#9-crash-recovery-and-repair-ux), [§10](DESIGN_SPEC.md#10-logging-and-diagnostics)
- Open decision: stable-file sampling ([DESIGN_SPEC §13](DESIGN_SPEC.md#13-open-decisions))

### In scope

- Per-match durable store (sequence/hash, incoming path, baseline, journal, processed outgoings)
- Reconcile → local operational states ([§6](DESIGN_SPEC.md#6-local-operational-states))
- Baseline record before “launch” command boundary (launch itself stubbed/fake process handle)
- Outgoing detection rules; multi-candidate error; missing baseline disables auto-send
- Watchdog adapter behind a port + polling fallback
- Multi-match local config selection isolation (FR-011 foundation)
- Structured logging with redaction (FR-012)

### Out of scope

- Real Civ process (P7), UI (P8), Paramiko (P6)

### Expected files/components

- `local/` persistence, reconcile, detect, `fs/` watcher port + watchdog impl + poller
- Fake process/launch port for state transitions without Civ

### Required automated tests

- PT-06, PT-19–PT-24, PT-34
- Restart with baseline surviving “Civ still running” (fake process) — PT-22 / FR-004
- Civ exit without outgoing → correct state (FR-010 simulated)
- Match switch does not mutate other match remote/local ownership records (FR-011)

### Applicable PT IDs

**PT-06, PT-19–PT-24, PT-34** (primary). Uses P3 engine for upload after detection.

### Manual verification

- Optional: watch a temp directory receive a stable synthetic file and see state change headlessly

### Exit criteria

- Baseline/detection/reconcile tests green; auto-send cannot run without trustworthy baseline
- Sampling interval default recorded (1.0s × 2) unless evidence changes it

### Risks / decisions

- Resolve stable-file sampling interval with tests ([DESIGN_SPEC §13](DESIGN_SPEC.md#13-open-decisions))
- Watchdog flakiness on Windows — polling fallback must be first-class

---

## P5 — Headless two-client end-to-end workflow (fake storage)

**Status:** NOT STARTED

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
- Scenarios covering FR-001–FR-005, FR-009–FR-010, FR-013–FR-014 at workflow level
- Re-run critical PT IDs in e2e form

### Out of scope

- Real SFTP, real Civ, GUI, packaging

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

---

## P6 — Paramiko SFTP adapter and disposable-server integration tests

**Status:** NOT STARTED

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

---

## P7 — Windows Civilization IV / BTS / Advanced Civ launch and process integration

**Status:** NOT STARTED

### Goal

Empirically determine and implement launch/process integration for Steam/BTS/AdvCiv: mod + save, already-running detection, exit without save (FR-010).

### Why now

Launch flags cannot be assumed; this phase follows a working Paramiko adapter so launch integration builds on proven remote transfer.

### Prerequisites

P6 complete.

### Read

- [`DESIGN_SPEC.md` §8.1](DESIGN_SPEC.md#81-launching-civilization), [§8.4–8.5](DESIGN_SPEC.md#84-civilization-closes-without-outgoing-save), [§13](DESIGN_SPEC.md#13-open-decisions)
- [`SYNC_PROTOCOL.md` §6.1](SYNC_PROTOCOL.md#61-play-session-baseline) (baseline still recorded before launch)

### In scope

- Launch port + Windows implementation
- Empirical notes (developer-local) for exact CLI / Steam behaviors — **do not commit real install paths as secrets/defaults beyond placeholders**
- Already-running / unrelated process warnings
- Wire launch to baseline recording boundary from P4
- Auto-launch remains per-match and off by default

### Out of scope

- Controlling Civ UI / ending turns (NG-8)
- Packaging, full GUI (stubs/CLI OK)

### Expected files/components

- `civ/launch.py` port + Windows adapter; manual test checklist

### Required automated tests

- Unit tests with fake process supervisor (no real Civ binary required in CI)
- FR-010 state transitions with fake process exit

### Applicable PT IDs

None new; supports PT-22 behavior with real process optional manually.

### Manual verification

- On a Windows machine with BTS/AdvCiv: launch mod+save, confirm load; exit without Next Turn → correct relay state
- Record resolved CLI in phase close-out notes / code comments without copying prior PBEM manager

### Exit criteria

- Exact Steam/BTS/AdvCiv launch behavior empirically verified and implemented behind the port
- Open decision “exact CLI” closed with evidence

### Risks / decisions

- Steam re-exports / working directory quirks — document fallbacks
- If AdvCiv load flags differ from stock BTS, capture both profiles

---

## P8 — Minimal PySide6 UI, match management, settings, status, diagnostics

**Status:** NOT STARTED

### Goal

Minimal main window consuming domain states/commands only; settings, match CRUD/select, diagnostics/repair confirmation, secret-safe errors (FR-012).

### Why now

UI must not invent protocol; it presents the completed domain, sync, SFTP, and Civ-launch capabilities from prior phases.

### Prerequisites

P7 complete.

### Read

- [`DESIGN_SPEC.md` §3](DESIGN_SPEC.md#3-end-to-end-workflows), [§4](DESIGN_SPEC.md#4-configuration-model), [§6–7](DESIGN_SPEC.md#6-local-operational-states), [§9–10](DESIGN_SPEC.md#9-crash-recovery-and-repair-ux), [§11](DESIGN_SPEC.md#11-functional-requirements-and-acceptance-criteria) (FR-011, FR-012, FR-014)
- [`SYNC_PROTOCOL.md` §7.1](SYNC_PROTOCOL.md#71-lock-primitive) (repair confirmation), [§11](SYNC_PROTOCOL.md#11-history-and-repair)

### In scope

- Main window per §7; primary button table; DIN TUR / YOUR TURN
- Global settings + per-match editors (no server settings duplicated per match)
- Diagnostics export redaction
- Explicit repair previews (abandoned lock, incomplete init)
- Multi-match switching presentation (FR-011)

### Out of scope

- Protocol decisions in widgets
- Packaging (P9)
- Fancy dashboards

### Expected files/components

- `ui/` PySide6 views + thin controllers calling application services

### Required automated tests

- UI tests: state presentation; buttons invoke commands only; no direct storage/protocol mutation from widgets
- Redaction tests on diagnostics export (FR-012)
- Repair confirmation required for foreign lock removal (FR-014)

### Applicable PT IDs

None exclusively; exercises PT-11 / PT-23 paths via UI commands.

### Manual verification

- Walk examples in [`DESIGN_SPEC` §7.2](DESIGN_SPEC.md#72-examples)
- Confirm errors include safety/retry/next-step fields (§7.4)

### Exit criteria

- UI is presentation/command-only; FR-011/012/014 UX satisfied
- Default auto-send / auto-launch match design recommendations

### Risks / decisions

- Close default auto-send vs manual if usability evidence appears; keep baseline requirement

---

## P9 — Real two-player hardening, Windows packaging, ops docs, release readiness

**Status:** NOT STARTED

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
- Release checklist: FR-001–FR-014 signed off; PT matrix still green on fake; SFTP subset green

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
