# Design specification — civ4-turn-relay

**Status:** Normative product design for protocol version 1 clients.
**Companion:** [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md) (wire protocol, remote layout, algorithms).
**Language:** `MUST` / `SHOULD` / `MAY` as defined in [`AGENTS.md`](../AGENTS.md).

This document defines client product behavior. Protocol commit semantics, remote invariants, and concurrency outcomes live only in `SYNC_PROTOCOL.md`.

---

## 1. Goals and non-goals

### Goals

| ID | Goal |
|----|------|
| G-1 | Simple PBEM turn relay for two or more human players |
| G-2 | Compatible with Civilization IV: Beyond the Sword, including Advanced Civ |
| G-3 | Obvious current state: what the app believes and why |
| G-4 | Automatic discovery and transfer of completed PBEM saves |
| G-5 | Safe restart after application or Civilization crashes |
| G-6 | Useful diagnostics without exposing secrets |
| G-7 | Support for multiple configured matches |

Multiple matches MAY be configured. The first implementation MAY focus on one selected match at a time while preserving a multi-match configuration model.

### Non-goals (initial)

| ID | Non-goal |
|----|----------|
| NG-1 | Implementing Civilization game rules |
| NG-2 | Inspecting or modifying Civ save contents |
| NG-3 | A custom server daemon or game process on Linux |
| NG-4 | Real-time / simultaneous-turn multiplayer |
| NG-5 | PitBoss |
| NG-6 | Email delivery |
| NG-7 | Mobile or web clients |
| NG-8 | Silently controlling Civilization’s UI (clicking menus, ending turns, etc.) |

The application manages saves, verification, transfer, and launching only. It MUST NOT pretend to perform gameplay actions inside Civilization.

### Planned client stack (later implementation)

Python 3.12, PySide6, Paramiko (SFTP), Watchdog (filesystem events). Windows distribution (P9) MUST provide both a portable/distributable build (PyInstaller or selected equivalent) and a real Windows installer (Inno Setup or justified equivalent)—not only a standalone executable. The remote side is ordinary SFTP storage with no custom server-side process. Correctness comes from the client protocol and the authoritative server manifest ([`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md)). Packaging and installer requirements are owned by [`PHASE_PLAN.md` P9](PHASE_PLAN.md#p9--real-two-player-hardening-windows-packaging-ops-docs-release-readiness).

---

## 2. Users and assumptions

| Role / assumption | Definition |
|-------------------|------------|
| Human relay participant | A person whose civilization is in the ordered human player list and who runs a relay client for their player ID |
| Match creator | Creates the match configuration and publishes the first outgoing PBEM save |
| Joining participant | Imports or joins an existing match and waits for ownership before playing |
| Client platform | Windows is the initial supported client platform |
| Game installs | All humans MUST use compatible Civ IV / BTS / mod installations for the match |
| AI players | Handled entirely by Civilization; MUST NOT appear in the relay’s human turn order |

Civilization’s visible game turn number is not assumed to equal the protocol sequence. See terminology in [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md#1-terminology).

---

## 3. End-to-end workflows

Legend: **Relay** = user action in the app · **Civ** = action inside Civilization · **Auto** = background relay behavior.

### 3.1 First-time application setup

| Step | Actor | Action |
|------|-------|--------|
| 1 | Relay | Enter global server settings (host, port, user, auth, remote root) and Civ launch profile(s) |
| 2 | Relay | Validate connectivity without logging secrets |
| 3 | Auto | Persist global config locally; secrets remain in local secret storage / `.env` (never committed) |

### 3.2 Creating a relay match

| Step | Actor | Action |
|------|-------|--------|
| 1 | Relay | Create match: stable game ID, display name, ordered human players, local player ID, mod, PBEM save location, launch profile |
| 2 | Auto | Persist per-match config locally; initialize remote match atomically per [`SYNC_PROTOCOL.md` §2.5](SYNC_PROTOCOL.md#25-initial-match-creation) (valid seq-0 `manifest.json` is the init commit point) |
| 3 | Auto | Enter `WAITING_FOR_MY_FIRST_SAVE` if this client owns the opening handoff |

### 3.3 Joining or importing a match

| Step | Actor | Action |
|------|-------|--------|
| 1 | Relay | Provide game ID (and display metadata as needed); select which human player this client owns |
| 2 | Auto | Fetch and validate remote manifest; refuse join if player ID is not in the human order |
| 3 | Auto | Enter `WAITING_FOR_OTHER_PLAYER` or download path if already current owner |

### 3.4 Creating the first PBEM save

| Step | Actor | Action |
|------|-------|--------|
| 1 | Relay / Auto | Before launch from `WAITING_FOR_MY_FIRST_SAVE`, record durable play-session baseline ([§8.1](#81-launching-civilization)) |
| 2 | Civ | Match creator creates the PBEM game and produces the first outgoing human save |
| 3 | Auto | Detect post-baseline outgoing candidate → verify → upload/commit per protocol |
| 4 | Auto | Transition to waiting for the next human in order; in `fully_managed`, post-commit Civ close follows [§8.5](#85-turn-handling-modes) |

Sequence 0 uses the same turn-handling rules as a received turn, except Civ launches without an incoming save ([§8.5](#85-turn-handling-modes)).

### 3.5 Receiving and playing a normal turn

| Step | Actor | Action |
|------|-------|--------|
| 1 | Auto | Poll/fetch manifest; if local player is current owner and save is new, download and verify |
| 2 | Relay / Auto | Present `YOUR TURN` / `DIN TUR`; record durable play-session baseline before launch; launch behavior depends on `turn_handling_mode` ([§4.2](#42-per-match-configuration), [§8.5](#85-turn-handling-modes)) |
| 3 | Civ | Player completes turn and clicks **Next Turn** |

In `standard`, the user starts Civ via the primary button. In `fully_managed`, Relay issues at most one automatic launch for that accepted sequence/hash after verified download and baseline recording.

### 3.6 Detecting and sending the outgoing save

| Step | Actor | Action |
|------|-------|--------|
| 1 | Auto | Detect stable outgoing candidate whose hash is absent from baseline, incoming save, `accepted_save_hashes`, and locally processed outgoing hashes ([protocol §6](SYNC_PROTOCOL.md#6-outgoing-save-detection)) |
| 2 | Auto | Upload and commit handoff per protocol (auto-send only when baseline is trustworthy; required for zero-click `fully_managed` operation) |
| 3 | Auto | Enter `WAITING_FOR_OTHER_PLAYER` when the authoritative manifest proves commit (or sender reconcile proves idempotent acknowledgement). In `fully_managed`, request Civ close only after that proof ([§8.5](#85-turn-handling-modes)) |
| 4 | Auto | Show next expected human player |

### 3.7 Restart after application or Civilization crash

| Step | Actor | Action |
|------|-------|--------|
| 1 | Auto | On startup, reconcile using remote manifest, verified remote save, local cache, local files, hashes, operation journal, play-session baseline, launch-attempt/process-association records, and whether Civ is running ([§9](#9-crash-recovery-and-repair-ux)) |
| 2 | Auto | Explain recovered state in the UI; resume polling / upload / wait as evidence requires; never double-launch the same accepted sequence/hash or auto-close an unverified process ([§8.5](#85-turn-handling-modes)) |
| 3 | Relay | Use explicit repair only when automatic reconciliation cannot safely proceed |

### 3.8 Switching between configured matches

| Step | Actor | Action |
|------|-------|--------|
| 1 | Relay | Select another configured match |
| 2 | Auto | Pause active watchers/transfers for the previous selection as needed; reconcile the newly selected match |
| 3 | Auto | Present that match’s derived status only |

Switching matches MUST NOT alter any match’s remote ownership.

### 3.9 Repair and recovery workflow

| Step | Actor | Action |
|------|-------|--------|
| 1 | Relay | Open diagnostics / repair; review proposed action and preview |
| 2 | Relay | Confirm explicitly |
| 3 | Auto | Apply only protocol-allowed recovery; preserve history ([`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md#11-history-and-repair)) |

---

## 4. Configuration model

Server and installation settings MUST NOT be duplicated into every match. Secrets MUST NOT be committed; [`.env.example`](../.env.example) holds **global** placeholders only.

### 4.1 Global configuration

| Concept | Editable | In `.env` example | Notes |
|---------|----------|-------------------|-------|
| SFTP host, port, username | Yes | Yes | Validated by UI (format / reachability checks) |
| Authentication method / private key path / password | Yes | Yes | Stored locally as secrets; never logged |
| Server root | Yes | Yes | Remote base path for all games |
| Polling interval | Yes | Yes | Default 10 seconds |
| Log level | Yes | Yes | Local only |
| Civ installation / default executable / launch-profile seed | Yes | Optional executable seed | Working directory and named profiles MAY live in local global config files |

`.env` MUST NOT carry per-match identity, mod, PBEM save directory, or turn-handling settings.

### 4.2 Per-match configuration

| Concept | Setting / field | Editable | Notes |
|---------|-----------------|----------|-------|
| Stable match / game ID | — | Create-time | Format enforced by protocol; immutable after remote init |
| Display name | — | Yes | Local and/or mirrored in manifest display field |
| Ordered human player IDs and display names | — | Create / admin repair | Defines relay order; excludes AI |
| Local player ownership (`player_id` for this client) | — | Yes | MUST be one of the human IDs; **not** a global `.env` value |
| Launch profile | — | Yes | References a global profile |
| Mod name or path | — | Yes | Default concept: `AdvCiv`; per-match only |
| PBEM save location and matching rules | — | Yes | Directory + filename/pattern constraints; per-match only |
| Turn handling | `turn_handling_mode` | Yes | `standard` \| `fully_managed`; **default `standard`**; per-match only; behavior in [§8.5](#85-turn-handling-modes) |
| Allow force-close after commit | `allow_force_close_after_commit` | Yes | Boolean; **default `false`**; applicable only when `turn_handling_mode` is `fully_managed`; see [§8.5](#85-turn-handling-modes) |

User-facing labels for `turn_handling_mode`:

```text
Turn handling:
- Standard
- Fully managed
```

These settings are per-match local configuration. They MUST NOT live in `.env`. They MUST NOT create remote protocol states or advance ownership by themselves.

The earlier standalone per-match `auto_launch` concept is replaced by `turn_handling_mode`. The minimal UI MUST NOT expose ambiguous combinations of partial automation toggles for the same lifecycle.

### 4.3 Storage and validation

- User-editable values are those listed above.
- The UI SHOULD validate formats before save (game ID, paths, port range, player list non-empty and unique IDs).
- `allow_force_close_after_commit` MUST be ignored unless `turn_handling_mode` is `fully_managed`.
- Local persistence MAY use `.env` for global secrets/settings plus per-match config files under the user data directory.
- Global SFTP settings apply to all matches under the configured server root.

---

## 5. Authority model

| Artifact | Authority |
|----------|-----------|
| Server manifest | Authoritative for protocol sequence and current human owner |
| Verified server save (hash-matched) | Authoritative game data for the accepted handoff |
| Local state | Cache and record of observations only |
| UI state | Derived presentation of local + remote evidence |

A detected filename, changed timestamp, timer tick, button click, or Civilization process state MUST NOT alone advance the protocol. Advancement occurs only through the commit algorithm in [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md#7-upload-and-commit-algorithm).

---

## 6. Local operational states

Remote turn ownership is never changed by the UI. States below are local operational modes derived from evidence.

### 6.1 State diagram

```mermaid
stateDiagram-v2
    [*] --> RECONCILING: startup
    RECONCILING --> WAITING_FOR_MY_FIRST_SAVE
    RECONCILING --> WAITING_FOR_OTHER_PLAYER
    RECONCILING --> DOWNLOADING
    RECONCILING --> MY_TURN_DOWNLOADED
    RECONCILING --> CIV_RUNNING
    RECONCILING --> OUTGOING_SAVE_DETECTED
    RECONCILING --> UPLOADING
    RECONCILING --> ERROR

    WAITING_FOR_MY_FIRST_SAVE --> CIV_RUNNING: launch Civ
    WAITING_FOR_MY_FIRST_SAVE --> OUTGOING_SAVE_DETECTED: stable first save

    WAITING_FOR_OTHER_PLAYER --> DOWNLOADING: local player is owner + new save
    DOWNLOADING --> MY_TURN_DOWNLOADED: verified
    DOWNLOADING --> ERROR: validation failure
    DOWNLOADING --> WAITING_FOR_OTHER_PLAYER: no longer owner / superseded

    MY_TURN_DOWNLOADED --> CIV_RUNNING: launch / managed launch
    MY_TURN_DOWNLOADED --> OUTGOING_SAVE_DETECTED: stable outgoing save
    CIV_RUNNING --> OUTGOING_SAVE_DETECTED: stable outgoing save
    CIV_RUNNING --> MY_TURN_DOWNLOADED: Civ exit, no new outgoing
    CIV_RUNNING --> WAITING_FOR_MY_FIRST_SAVE: Civ exit, seq 0, no outgoing

    OUTGOING_SAVE_DETECTED --> UPLOADING: begin handoff
    UPLOADING --> WAITING_FOR_OTHER_PLAYER: commit success
    UPLOADING --> OUTGOING_SAVE_DETECTED: retryable failure
    UPLOADING --> ERROR: non-retryable / needs user

    ERROR --> RECONCILING: user retry / auto backoff
```

`RECONCILING` and `DOWNLOADING` are transient correctness states. User-visible status SHOULD map them to short phrases (e.g. “Checking game state…”, “Downloading save…”) without implying ownership changes.

### 6.2 State catalog

| State | Meaning | Required evidence | Primary UI message | Primary action | Allowed transitions | Prohibited | Restart recovery |
|-------|---------|-------------------|--------------------|----------------|---------------------|------------|------------------|
| `RECONCILING` | Startup or post-error evidence merge | App start or explicit retry | Checking game state… | None / Cancel if long | Any stable state below | Treating reconcile as a handoff | Re-enter reconcile |
| `WAITING_FOR_MY_FIRST_SAVE` | This client must publish sequence 0→1 | Manifest seq 0 / no accepted save; local player is designated opener | Waiting for your first PBEM save | Start Civ (create game) | → `CIV_RUNNING`, `OUTGOING_SAVE_DETECTED`, `ERROR` | → `MY_TURN_DOWNLOADED` without remote save | Same if still opener and seq 0 |
| `WAITING_FOR_OTHER_PLAYER` | Another human owns the handoff | Manifest current player ≠ local | Waiting for {player} | None needed (Focus/Close fallbacks MAY appear as secondary when Civ is still closing) | → `DOWNLOADING`, `ERROR` | Upload commit; treating Civ close failure as protocol failure | Same if still not owner; close progress is secondary local status only ([§8.5](#85-turn-handling-modes)) |
| `DOWNLOADING` | Fetching accepted save for this player | Manifest says local is owner; download in progress | Downloading save… | None | → `MY_TURN_DOWNLOADED`, `WAITING_FOR_OTHER_PLAYER`, `ERROR` | Launch before verify | Resume or restart download |
| `MY_TURN_DOWNLOADED` | Verified incoming save ready | Local verified hash = manifest accepted hash; local is owner | YOUR TURN / DIN TUR | Start Civ and play | → `CIV_RUNNING`, `OUTGOING_SAVE_DETECTED`, `ERROR` | Upload incoming hash as new handoff | Restore this if evidence still holds |
| `CIV_RUNNING` | Civ process associated with this match is running | Process handle / PID observation; durable play-session baseline recorded at launch | Civilization is running | Focus / open folder (secondary) | → `OUTGOING_SAVE_DETECTED`, `MY_TURN_DOWNLOADED`, `WAITING_FOR_MY_FIRST_SAVE`, `ERROR` | Manifest rewrite because Civ started | If Civ still running → `CIV_RUNNING` with preserved baseline; else re-derive |
| `OUTGOING_SAVE_DETECTED` | Stable new outgoing candidate ready | Stable file; matching rules; hash absent from baseline, incoming, `accepted_save_hashes`, and locally processed outgoings | Outgoing save ready to send | Send now (or Auto sending…) | → `UPLOADING`, `ERROR` | Skip verify; auto-send without trustworthy baseline | Re-hash and reclassify per protocol §6.3; never treat older-hash replay as a new success |
| `UPLOADING` | Handoff commit in progress | Operation journal active | Uploading save… | None | → `WAITING_FOR_OTHER_PLAYER`, `OUTGOING_SAVE_DETECTED`, `ERROR` | Second concurrent commit for same match | Resume idempotent commit ([protocol](SYNC_PROTOCOL.md#7-upload-and-commit-algorithm)) |
| `ERROR` | Operator attention needed | Recorded failure context | Error: {specific} | Retry / Open diagnostics | → `RECONCILING` | Silent ignore | Show last error + reconcile |

---

## 7. Minimal UI

### 7.1 Main window contents

- Selected match (display name)
- One prominent status
- Current expected player (from manifest when available)
- Last successful event with timestamp
- One context-sensitive primary button
- Secondary controls: matches, settings, diagnostics
- Per-match settings MUST expose `turn_handling_mode` and, when Fully managed is selected, the advanced `allow_force_close_after_commit` consent with warning ([§4.2](#42-per-match-configuration))

### 7.2 Examples

```text
AdvCivTest

STATUS: Waiting for Ljunget
Last event: Save uploaded at 21:43

[Nothing needs to be done]
```

```text
AdvCivTest

STATUS: YOUR TURN
Save downloaded and verified

[Start Civ and play]
```

```text
AdvCivTest

STATUS: Waiting for Ljunget
Turn safely sent, but Civilization did not close.

[Focus Civ]  [Close Civ]
```

Swedish `DIN TUR` and English `YOUR TURN` MAY both be supported; the status meaning MUST be identical. The waiting example’s close message is secondary local status on `WAITING_FOR_OTHER_PLAYER` after a proven commit ([§8.5](#85-turn-handling-modes)); it is not a remote ownership failure.

### 7.3 Primary button by state

| State | Primary button | Behavior |
|-------|----------------|----------|
| `WAITING_FOR_MY_FIRST_SAVE` | Start Civ and create game (or Start/Resume after exit without save) | Launch Civ with configured mod (no incoming save required); in `fully_managed`, first launch MAY be automatic once ([§8.5](#85-turn-handling-modes)) |
| `WAITING_FOR_OTHER_PLAYER` | Disabled: Nothing needs to be done | No protocol effect; if Civ remains open after proven commit, secondary Focus/Close fallbacks MAY appear |
| `DOWNLOADING` | Disabled: Downloading… | No protocol effect |
| `MY_TURN_DOWNLOADED` | Start Civ and play (or Start/Resume after exit without save) | Launch Civ with mod + verified incoming save; in `fully_managed`, at most one automatic launch per accepted sequence/hash |
| `CIV_RUNNING` | Disabled: Civilization is running | Optional secondary: Focus / reveal save folder |
| `OUTGOING_SAVE_DETECTED` | Send save (if not auto) | Start upload/commit; auto-send remains subject to trustworthy baseline/candidate rules in both modes; `fully_managed` requires auto-send for zero-click operation |
| `UPLOADING` | Disabled: Uploading… | No protocol effect |
| `ERROR` | Retry | Re-enter `RECONCILING` / resume failed idempotent op |
| `RECONCILING` | Disabled: Checking… | No protocol effect |

Button presses MUST NOT mark a handoff accepted. Only a successful protocol commit does.

### 7.4 Error presentation

Every error MUST state:

1. What failed
2. Whether the turn and save are safe (ownership unchanged / save intact / unknown)
3. What the application will retry
4. What the user can do next

Avoid generic messages such as “No connection” without host, operation, and safety context.

---

## 8. Process and save detection behavior

### 8.1 Launching Civilization

- Launch MUST use the selected launch profile, configured mod, and—when playing a received turn—the verified incoming save path.
- Exact CLI flags are an integration detail; the design requires “mod + save” correctness, not save rewriting ([Open decisions](#13-open-decisions)).
- Immediately before launching from `WAITING_FOR_MY_FIRST_SAVE` or `MY_TURN_DOWNLOADED`, the client MUST record a durable play-session baseline of stable matching PBEM files and hashes ([`SYNC_PROTOCOL.md` §6.1](SYNC_PROTOCOL.md#61-play-session-baseline)).
- The client MUST persist launch-attempt and process-association evidence (at least intended sequence/hash, PID, process start time, and executable identity) across Relay restarts.
- The client MUST NOT launch a second associated Civ instance for the same match. If the associated process is already running, keep `CIV_RUNNING` and retain the original baseline.
- If an unrelated Civ process is running, the client SHOULD warn before launch and MUST NOT treat that process as Relay-owned.
- If no interactive/unlocked desktop is available, auto-launch MUST defer without repeated hidden launches.
- Automatic vs manual launch timing is owned by [§8.5](#85-turn-handling-modes).

### 8.2 Outgoing save completion

A candidate MUST be accepted for `OUTGOING_SAVE_DETECTED` only when all hold:

- Path is under the match PBEM directory (recursive watch MAY be used)
- Filename/matching rules for the selected game pass
- File size is stable across at least two samples separated by a short interval (or equivalent lock/readability check)
- File is readable end-to-end for hashing
- Content SHA-256 is absent from the play-session baseline
- Content SHA-256 ≠ current verified incoming hash
- Content SHA-256 is absent from manifest `accepted_save_hashes`
- Content SHA-256 is absent from locally processed outgoing candidates
- A trustworthy baseline exists when auto-selecting; otherwise auto-send stops and an explained recovery/manual-selection path is required
- If multiple plausible post-baseline candidates exist, require user selection; do not guess

Filesystem events SHOULD trigger checks; polling at the global interval MUST remain a fallback. Timestamps and events MUST NOT establish identity or handoff validity. An overwritten path is acceptable only if its new content hash satisfies the rules above. Normative detail: [`SYNC_PROTOCOL.md` §6](SYNC_PROTOCOL.md#6-outgoing-save-detection).

### 8.3 Stale, partial, duplicate, unrelated

| Case | Behavior |
|------|----------|
| Partial write | Wait until stable; do not hash/upload |
| Duplicate event / same hash | Idempotent no-op |
| Unrelated save / other game | Ignore |
| File present before launch (in baseline) | Not an automatic outgoing candidate |
| Stale/replay hash in `accepted_save_hashes` | No remote advance; classify per protocol §6.3 (reject incoming; idempotent ack only for sender reconcile; older hash → stale/replay) |
| Incoming file copied into PBEM folder | Reject as outgoing (same hash as incoming / latest accepted for recipient) |
| Missing/corrupt baseline | Disable auto-send; explained manual recovery |

### 8.4 Civilization closes without outgoing save

- If protocol sequence remains `0`, no accepted save exists, and no valid outgoing candidate was produced → transition `CIV_RUNNING` → `WAITING_FOR_MY_FIRST_SAVE`.
- Otherwise, when a verified incoming turn was in play → transition `CIV_RUNNING` → `MY_TURN_DOWNLOADED`.

Explain that no new outgoing save was detected. Do not change remote ownership. Do **not** auto-relaunch in a loop; the user MUST explicitly Start/Resume ([§8.5](#85-turn-handling-modes)).

### 8.5 Turn handling modes

Per-match `turn_handling_mode` ([§4.2](#42-per-match-configuration)) selects how much of the local lifecycle Relay automates. Neither mode weakens the authority model ([§5](#5-authority-model)): file detection, stability, upload success, process launch, and process exit MUST NEVER advance remote ownership. Only the existing manifest commit algorithm advances a turn ([`SYNC_PROTOCOL.md` §7](SYNC_PROTOCOL.md#7-upload-and-commit-algorithm)). Process closure MUST NOT become a remote protocol state.

Auto-send remains subject to trustworthy baseline and candidate rules ([§8.2](#82-outgoing-save-completion)) in **both** modes. Fully managed requires those rules to succeed for zero-click operation.

#### Standard (`standard`, default)

- Relay downloads and verifies incoming saves.
- The user launches Civ through Relay’s primary button.
- A valid outgoing save MAY be auto-sent when a trustworthy baseline exists.
- Relay MUST NOT close Civ automatically.
- Manual Send remains available when auto-send is disabled or unsafe.

#### Fully managed (`fully_managed`)

Normal successful lifecycle requires no Relay interaction after the app is opened:

1. Remote manifest shows the local player owns the turn.
2. Relay downloads and verifies the accepted save.
3. Relay records the durable play-session baseline.
4. Relay issues exactly one automatic launch for that accepted sequence/hash.
5. Civ starts with the configured BTS/AdvCiv profile and verified save.
6. The player plays and ends the turn inside Civ.
7. Relay detects a stable outgoing save that passes all baseline/hash rules.
8. Relay automatically uploads and commits it.
9. Only after the authoritative manifest proves commit, or sender reconciliation proves an idempotent acknowledgement, Relay requests graceful closure of the exact Civ process it launched.
10. Relay waits for the next player and repeats when ownership returns.

Sequence 0 follows the same model but launches Civ without an incoming save and waits for the first outgoing save.

Additional Fully managed constraints:

- Auto-launch at most once per accepted sequence/hash unless the user explicitly retries (Start/Resume).
- Duplicate polling MUST NOT launch again for the same sequence/hash.
- Partial/unstable saves MUST NEVER upload or trigger closure.
- Upload/commit failure MUST leave Civ open.
- Civ MAY be closed automatically only after authoritative commit proof (or proven idempotent acknowledgement).
- Close only the exact Relay-launched process, verified using PID, process start time, and executable identity.
- Never close an unrelated or manually launched Civ process merely because the executable name matches.
- After Relay restart, if process identity cannot be proven, do not close it automatically.
- If Civ exits without a valid outgoing save: no remote change, no relaunch loop; return to `WAITING_FOR_MY_FIRST_SAVE` (seq 0) or `MY_TURN_DOWNLOADED` (received turn) and require explicit Start/Resume ([§8.4](#84-civilization-closes-without-outgoing-save)).

#### Closing policy after authoritative commit

Applies only in `fully_managed`, and only after commit/idempotent-ack proof:

1. Request normal Windows application close for the verified Relay-owned process.
2. Wait 15 seconds.
3. If Civ exits, continue normally.
4. Otherwise show: `Turn safely sent, but Civilization did not close.`
5. Provide manual Focus/Close fallback.

Operational state after commit remains `WAITING_FOR_OTHER_PLAYER` even while Civ is closing. Close progress or failure is secondary local status attached to that waiting state; it is not remote ownership and not a protocol failure. The UI MUST distinguish “turn safely committed” from “Civ still open”.

#### Force-close opt-in (`allow_force_close_after_commit`)

- Explicit advanced opt-in with warning; default `false`; only applicable in `fully_managed` ([§4.2](#42-per-match-configuration)).
- Forced termination is allowed only after authoritative commit proof (or proven idempotent acknowledgement) and exact process verification.
- Never force-close during save creation, verification, upload, or commit.
- A close failure MUST NEVER change or roll back an already committed handoff.

---

## 9. Crash recovery and repair UX

### 9.1 Startup reconciliation inputs

1. Authoritative remote manifest (including `accepted_save_hashes`)
2. Verified remote save object (full read-back SHA-256)
3. Local cache (last sequence/hash, paths)
4. Local incoming and outgoing files
5. Recorded hashes, operation journal, and play-session baseline
6. Durable launch-attempt and process-association evidence (sequence/hash, PID, start time, executable identity)
7. Whether Civilization is running and whether that process still matches Relay-owned association evidence
8. Remote upload-lock presence/metadata (never auto-delete foreign locks)

The program MUST explain the recovered state rather than guess silently from filenames alone. If the play-session baseline is missing or corrupt while outgoing detection would otherwise run, auto-send MUST stop pending explicit recovery. Restart MUST NOT double-launch the same accepted sequence/hash or auto-close a process whose identity cannot be proven ([§8.5](#85-turn-handling-modes)).

### 9.2 Repair rules

- Repair actions MUST be explicit, previewed, and confirmed.
- Repair MUST NOT silently delete history or overwrite the authoritative accepted save.
- Abandoned foreign upload-lock removal and incomplete match-directory repair require confirmation that the original process is stopped / that overwrite is intended; both MUST be logged clearly.
- Protocol-level repair semantics: [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md#11-history-and-repair).

---

## 10. Logging and diagnostics

### 10.1 Logs

Structured, human-readable local logs MUST include where applicable:

- game ID
- operation ID
- protocol sequence
- state transition
- filenames where safe
- abbreviated hashes
- retry and error context

Logs MUST NEVER include passwords, private keys, or secret environment values.

### 10.2 Diagnostics view / export

Diagnostics SHOULD help a non-technical player classify problems as:

| Class | Examples |
|-------|----------|
| Local | Missing Civ path, save folder unreadable |
| Network | Timeout, DNS failure |
| Authentication | Key rejected, auth failure (no secret echoed) |
| Remote state | Manifest invalid, foreign lock held (possibly abandoned after informational threshold), wrong owner, incomplete match init |
| Invalid save | Hash mismatch, size mismatch, unstable file, stale/replay candidate |

Export MAY attach redacted logs and last manifest metadata (no secrets).

---

## 11. Functional requirements and acceptance criteria

| ID | Requirement | Acceptance criteria |
|----|-------------|---------------------|
| FR-001 | Initial handoff | Creator’s first stable save commits seq 0→1; next human becomes current; joiner can download |
| FR-002 | Normal alternating handoffs | Only current human can commit; sequence increments once per accepted save; next player derived from order |
| FR-003 | Duplicate file events | Re-seeing the same outgoing hash does not double-advance ([protocol tests](SYNC_PROTOCOL.md#13-protocol-test-matrix)) |
| FR-004 | Application restart | After kill mid-wait/download/upload, reconcile restores a correct non-guessed state; baseline survives when Civ still running |
| FR-005 | Network interruption | Interrupted ops leave ownership unchanged or eventually consistent via idempotent retry |
| FR-006 | Wrong-player upload | Non-owner commit attempt fails; manifest unchanged |
| FR-007 | Partial upload | Temp objects never become accepted; retry safe |
| FR-008 | Stale local save / historical replay | Hash in `accepted_save_hashes` cannot advance again; older hashes are not new successes |
| FR-009 | Concurrent polling | Two clients polling: at most one successful new handoff for a given new hash |
| FR-010 | Civ produces no save | Civ exit without candidate → `MY_TURN_DOWNLOADED` or `WAITING_FOR_MY_FIRST_SAVE` (seq 0); remote unchanged |
| FR-011 | Multiple matches | Switching selected match changes presentation only; other matches’ remote state untouched |
| FR-012 | Secret redaction | Logs, UI errors, and diagnostics export contain no passwords, keys, or secret env values |
| FR-013 | Play-session baseline | Pre-launch files are not auto-sent; missing baseline disables auto-send |
| FR-014 | Foreign locks | Never auto-broken; abandoned removal only via confirmed repair |
| FR-015 | Fully managed turn lifecycle | Verified incoming save causes one automatic launch; duplicate polling does not launch again; commit (or proven idempotent ack) is proven before close request; only the Relay-owned process is targeted; close failure leaves the committed turn safe and clearly reported; force-close is opt-in and post-commit only; restart neither double-launches nor closes an unverified process; Civ exit without outgoing save causes no remote change or relaunch loop ([§8.5](#85-turn-handling-modes)) |

---

## 12. Testing principles

Product-level principles only. Detailed protocol cases: [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md#13-protocol-test-matrix). Implementation gates: future `PHASE_PLAN.md`.

1. Domain and protocol logic MUST be testable without GUI, real SFTP, or real Civ saves.
2. Fake storage adapters MUST inject failures at every commit step.
3. UI tests SHOULD assert presentation and that actions only invoke domain/protocol APIs.
4. Integration tests against SFTP MAY come later; they MUST use disposable credentials and never require production infrastructure in CI.
5. Changing an invariant requires updating the protocol test matrix and automated coverage.

---

## 13. Open decisions

| Topic | Recommendation |
|-------|----------------|
| Exact Civ IV CLI for mod + save | Mod confirmed 2026-08-13: stored `Mods\\AdvCiv` translates to one `mod=\\AdvCiv` argument. Confirm direct `/fxsload` empirically; keep the launcher behind an adapter interface. |
| Default auto-send vs manual Send | Auto-send after valid `OUTGOING_SAVE_DETECTED` when a trustworthy baseline exists (required for zero-click `fully_managed`) |
| Default `turn_handling_mode` | `standard` ([§4.2](#42-per-match-configuration), [§8.5](#85-turn-handling-modes)); replaces standalone `auto_launch` |
| Default `allow_force_close_after_commit` | `false`; Fully managed advanced opt-in only |
| Stable-file sampling interval | 1.0s between size samples, twice; make configurable later if needed |
| Host-key policy | Verify against pinned host key / known_hosts; refuse on mismatch (no silent insecure accept) |

Lock fencing, atomic rename, full remote read-back, save path naming, and informational abandoned-lock display are decided in [`SYNC_PROTOCOL.md`](SYNC_PROTOCOL.md#14-open-decisions) and MUST NOT be weakened here.
