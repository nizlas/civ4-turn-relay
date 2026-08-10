# Agent guidance for civ4-turn-relay

Normative design lives in the documents below. This file routes work and states hard safety rules. It is not a second design specification.

## Requirement language

In project design documents:

| Term | Meaning |
|------|---------|
| **MUST** | Absolute requirement |
| **MUST NOT** | Absolute prohibition |
| **SHOULD** | Strong default; deviate only with recorded reason |
| **MAY** | Optional |

## Authoritative documents

| Document | Authority |
|----------|-----------|
| [`docs/DESIGN_SPEC.md`](docs/DESIGN_SPEC.md) | Product behavior, configuration, UI, local states, acceptance criteria |
| [`docs/SYNC_PROTOCOL.md`](docs/SYNC_PROTOCOL.md) | Protocol v1: remote layout, manifest, algorithms, concurrency, recovery |
| [`docs/PHASE_PLAN.md`](docs/PHASE_PLAN.md) | Implementation phases and gates |
| [`README.md`](README.md) | Orientation only; not the design source of truth |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Contribution and licensing expectations |
| [`docs/licensing.md`](docs/licensing.md) | Licensing policy |
| [`.env.example`](.env.example) | Placeholder configuration shape only |

Keep each fact in one place. Prefer linking over restating architecture or invariants.

## What to read

Do **not** load every document for every small task.

| Change type | Read |
|-------------|------|
| Any task | This file (`AGENTS.md`) |
| Repository orientation | `README.md` |
| User behavior, configuration, product scope, UI | `docs/DESIGN_SPEC.md` |
| State, saves, sync, remote storage, concurrency, recovery | `docs/SYNC_PROTOCOL.md` |
| Implementation sequencing | Only the **ACTIVE** section of `docs/PHASE_PLAN.md` plus its listed prerequisites |
| Contributions / license | `CONTRIBUTING.md`, `docs/licensing.md` as needed |

## Hard rules

1. **No copying** from the prior third-party PBEM manager (source, text, assets, or implementation details).
2. **Never commit** secrets, credentials, real server addresses, private keys, passwords, or real Civilization save files.
3. The **server manifest** is authoritative for protocol sequence and current human owner.
4. **UI state, timers, filenames, and local cache are not authoritative.**
5. Button presses, polling events, and process launches **MUST NOT** by themselves advance the match.
6. Save acceptance and retries **MUST** be idempotent.
7. Remote writes that commit match state **MUST** be atomic (see protocol commit point).
8. Keep **UI, domain logic, process launching, filesystem watching, and remote storage** behind explicit boundaries.
9. When changing protocol invariants, **add or update tests**.
10. **Remain within the ACTIVE implementation phase** in `docs/PHASE_PLAN.md`.

## Architecture expectation

Domain logic decides ownership and transitions from verified evidence. Adapters observe filesystems, talk SFTP, launch Civilization, and render UI. Adapters report facts; they do not redefine match ownership.
