# civ4-turn-relay

A small, reliable Windows manager for Civilization IV: Beyond the Sword PBEM games, initially targeting Advanced Civ.

> **Early design / scaffolding only.** This repository is not yet safe for real games. No turn relay, save handling, or GUI is implemented. Do not use it to manage active PBEM matches.

## Motivation

Existing third-party PBEM tooling inspired the desired workflow but proved unreliable in practice: duplicate save detection could incorrectly advance turn state, the UI did not clearly expose actual state, and crash recovery was weak.

This project is written from scratch. It does not copy source, text, assets, or implementation details from that tooling.

## Intended workflow

1. The relay runs in the background while Civilization IV is closed.
2. When a remote turn becomes available, the relay downloads and verifies it.
3. Optionally, it launches Civilization IV directly into the correct save.
4. The player finishes the turn and clicks **Next Turn** inside Civilization IV.
5. Civilization IV creates the outgoing PBEM save.
6. The relay detects, verifies, and uploads that save automatically.
7. The relay transitions explicitly to waiting for the next player.

The Linux host is only a shared SFTP file store. It does not run Civilization or game-server software.

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

## Planned server model

- Ordinary SFTP storage (no game process on the server).
- One authoritative manifest per game.
- Immutable save history.
- SHA-256 identity for saves.

## Planned UI

- One clearly displayed state.
- An explanation of why the app is in that state.
- Last successful operation.
- One context-sensitive primary action.
- Secondary settings and diagnostics.

## Non-goals

- No PitBoss.
- No simultaneous-turn multiplayer.
- No Civilization save modification.
- No game server running on Linux.
- No requirement that all settings be copied for each match.

## Planned technology

- Python 3.12
- PySide6
- SFTP
- Windows distribution: portable/distributable build (PyInstaller or equivalent) **and** a real Windows installer (Inno Setup or justified equivalent); see [`docs/PHASE_PLAN.md` P9](docs/PHASE_PLAN.md#p9--real-two-player-hardening-windows-packaging-ops-docs-release-readiness)

## Implementation sequence

The authoritative implementation sequence is [`docs/PHASE_PLAN.md`](docs/PHASE_PLAN.md). That document defines phases, exit gates, and the current **ACTIVE** phase. Do not treat any other roadmap as authoritative.

## Design documents

Normative design (not yet implemented):

- [`AGENTS.md`](AGENTS.md) — document routing and hard safety rules for contributors and coding agents
- [`docs/DESIGN_SPEC.md`](docs/DESIGN_SPEC.md) — product behavior, configuration, UI, and acceptance criteria
- [`docs/SYNC_PROTOCOL.md`](docs/SYNC_PROTOCOL.md) — protocol v1: manifest, sync algorithms, concurrency, recovery
- [`docs/PHASE_PLAN.md`](docs/PHASE_PLAN.md) — incremental implementation phases and exit gates

## Security

Credentials and real server details must never be committed. Copy [`.env.example`](.env.example) to a local `.env` (gitignored). Prefer SSH keys over passwords where practical. The application must never log credentials. See also [`.gitignore`](.gitignore) for ignored secrets, keys, and save files.

## License

MIT License — see [`LICENSE`](LICENSE). Practical notes on dependencies and redistribution are in [`docs/licensing.md`](docs/licensing.md).

Contributions: see [`CONTRIBUTING.md`](CONTRIBUTING.md).
