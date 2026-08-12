# civ4-turn-relay

A small, reliable Windows manager for Civilization IV: Beyond the Sword PBEM games, initially targeting Advanced Civ.

Relay keeps an authoritative SFTP-backed turn sequence, downloads and verifies saves, and automates as much of the local Civilization lifecycle as the player chooses — without copying any third-party PBEM manager.

## Design goals

- **Explicit and visible state** — the app always shows what it believes is true.
- **Deterministic turn ownership** — who may act next is unambiguous.
- **Idempotent save handling** — reprocessing the same save is safe.
- **No double advances** — duplicate uploads must never advance the game twice.
- **Atomic remote updates** — remote state changes land completely or not at all.
- **Crash recovery** — recover from server and local evidence after interruption.
- **Understandable diagnostics** — failures explain what happened and what to do next.
- **Minimal user interaction** — automation for the common path; settings stay out of the way.
- **Global server configuration** — connection details are not duplicated for every match.

## Fully Managed mode

**Fully Managed mode** is a primary product feature and explicit design goal: fully automated turn handling so the player mostly just plays.

In the normal successful case:

1. The Relay application remains running quietly in the background.
2. When the authoritative server says it is the player’s turn, Relay downloads and verifies the save.
3. Relay launches Civilization IV / Beyond the Sword / Advanced Civ directly with the correct save.
4. The player simply plays the turn and presses **Next Turn** inside Civilization.
5. Relay waits until the outgoing save is complete and stable.
6. It verifies, uploads, and atomically commits the save for the next player.
7. Only after the handoff is authoritatively committed does Relay gracefully close the exact Civilization process that it launched.
8. Relay then waits for the next turn and repeats the cycle.

The player does not browse save folders, upload files, navigate multiplayer menus, or interact with Relay between turns.

### Closest practical Civ IV “Play by Cloud”

Civilization IV has no native Play by Cloud integration. Turns are still exchanged as save files. Relay hides that file exchange and the local process lifecycle from the player.

Civilization must restart and load the save for each turn — it does **not** stay running between turns. The intended rhythm is still similar to Civilization VI Play by Cloud, where receiving a turn also meant visibly loading the saved game. This project does not claim literal parity with Civ VI.

### Safety properties

- No automatic send without a durable play-session baseline and a stable, verified outgoing save.
- No process close before an authoritative commit (or proven idempotent acknowledgement).
- Only the exact Relay-launched Civilization process may be closed.
- Failures become visible and require explicit recovery rather than silently guessing.

Detailed lifecycle and safety semantics: [`docs/DESIGN_SPEC.md`](docs/DESIGN_SPEC.md) (§8.5 turn-handling modes).

### Implementation status

Protocol, local persistence, reconciliation, save detection, orchestration, the Windows process adapter (launch, identity verification, graceful close), and the PySide6 desktop client are implemented with full automated test coverage. What remains for **P7** ([`docs/PHASE_PLAN.md`](docs/PHASE_PLAN.md)) is the manual smoke test against a real Civilization installation: the exact `/fxsload` and `mod=<mod folder>` command-line behavior is modeled and unit-tested but not yet empirically confirmed. **P7 stays ACTIVE until that checklist is completed** ([`docs/DESKTOP_CLIENT.md`](docs/DESKTOP_CLIENT.md)). Until then, do not rely on Fully Managed mode for real matches.

## Standard mode

**Standard mode** is the conservative alternative (and the default). The user explicitly launches Civilization when ready; Relay does **not** close it after a commit. Relay still downloads, verifies, detects, and uploads according to the authoritative manifest — without owning the process lifecycle.

## Motivation

Existing third-party PBEM tooling inspired the desired workflow but proved unreliable in practice: duplicate save detection could incorrectly advance turn state, the UI did not clearly expose actual state, and crash recovery was weak.

This project is written from scratch. It does not copy source, text, assets, or implementation details from that tooling.

The Linux host is only a shared SFTP file store. It does not run Civilization or game-server software.

## Planned server model

- Ordinary SFTP storage (no game process on the server).
- One authoritative manifest per game.
- Immutable save history.
- SHA-256 identity for saves.

## Desktop client

Launch the desktop client with the installed GUI script:

```bash
civ4-turn-relay-ui
```

The client shows a match list with one clearly displayed state per match, an explanation of why the app is in that state, and one context-sensitive primary action; secondary controls (Focus/Close Civilization, settings, diagnostics) appear only when relevant. Closing the window hides Relay to the system tray while matches are active, so Fully Managed matches keep running in the background.

Setup, turn flow, configuration, process safety, and the manual smoke-test checklist: [`docs/DESKTOP_CLIENT.md`](docs/DESKTOP_CLIENT.md).

### Portable build and installer (forthcoming)

Windows packaging scaffolding for a **portable ZIP** and a **per-user Inno Setup installer** lives under [`packaging/`](packaging/) with operator docs in [`docs/RELEASE.md`](docs/RELEASE.md). These are **not** published release downloads yet: P7’s real-Civ smoke test is still outstanding, and packaging artifacts must be built and checked locally before any release claim.

## Non-goals

- No PitBoss.
- No simultaneous-turn multiplayer.
- No Civilization save modification.
- No game server running on Linux.
- No requirement that all settings be copied for each match.

## Technology

- Python 3.12
- PySide6 (desktop client)
- psutil (process identity verification)
- SFTP (Paramiko)
- Windows distribution: portable/distributable build (PyInstaller or equivalent) **and** a real Windows installer (Inno Setup or justified equivalent); see [`docs/PHASE_PLAN.md` P9](docs/PHASE_PLAN.md#p9--real-two-player-hardening-windows-packaging-ops-docs-release-readiness)

## Implementation sequence

The authoritative implementation sequence is [`docs/PHASE_PLAN.md`](docs/PHASE_PLAN.md). That document defines phases, exit gates, and the current **ACTIVE** phase. Do not treat any other roadmap as authoritative.

## Design documents

Normative design:

- [`AGENTS.md`](AGENTS.md) — document routing and hard safety rules for contributors and coding agents
- [`docs/DESIGN_SPEC.md`](docs/DESIGN_SPEC.md) — product behavior, configuration, UI, and acceptance criteria
- [`docs/SYNC_PROTOCOL.md`](docs/SYNC_PROTOCOL.md) — protocol v1: manifest, sync algorithms, concurrency, recovery
- [`docs/PHASE_PLAN.md`](docs/PHASE_PLAN.md) — incremental implementation phases and exit gates

## Security

Credentials and real server details must never be committed. Copy [`.env.example`](.env.example) to a local `.env` (gitignored). Prefer SSH keys over passwords where practical. The application must never log credentials. See also [`.gitignore`](.gitignore) for ignored secrets, keys, and save files.

SFTP host-key verification is mandatory (known_hosts **or** SHA-256 fingerprint). Adapter details and the disposable OpenSSH test command are in [`docs/SFTP_ADAPTER.md`](docs/SFTP_ADAPTER.md).

## License

MIT License — see [`LICENSE`](LICENSE). Practical notes on dependencies and redistribution are in [`docs/licensing.md`](docs/licensing.md).

Contributions: see [`CONTRIBUTING.md`](CONTRIBUTING.md).
