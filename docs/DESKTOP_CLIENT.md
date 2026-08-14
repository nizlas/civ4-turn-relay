# Desktop client (P7/P8)

This guide covers the minimal PySide6 desktop client and the Windows Civilization process integration: what the client does, how a turn flows, configuration, launch prerequisites, process safety, tray behavior, and the manual Windows smoke test that closes phase P7.

Normative behavior lives in [`DESIGN_SPEC.md`](DESIGN_SPEC.md) (turn handling modes and closing policy in [§8.5](DESIGN_SPEC.md#85-turn-handling-modes), configuration model in [§4](DESIGN_SPEC.md#4-configuration-model)). This document explains usage; it does not redefine invariants.

## Overview

The desktop client (`civ4-turn-relay-ui`) is a small always-running window plus tray icon. It shows a match list, one prominent status per match with an explanation, one context-sensitive primary button, secondary Focus/Close Civilization buttons when relevant, and a diagnostics pane.

The per-match **Turn handling** setting selects how much of the local lifecycle Relay automates:

| Behavior | Standard (default) | Fully managed |
|----------|--------------------|---------------|
| Who starts Civilization | You, via Relay's primary button | Relay, automatically — exactly once per accepted turn |
| Who sends the outgoing save | Relay auto-sends when safe; manual **Send** otherwise | Relay, automatically |
| Does Relay ever close Civilization | Never | Yes — graceful close of the exact Relay-launched process, only after the turn is authoritatively committed |
| What requires clicks | Start each turn; occasionally confirm a send | Normally nothing — you just play and press Next Turn |

Fully managed mode only works while Relay stays running, so keep it in the system tray between turns (see [Tray and background behavior](#tray-and-background-behavior)). Neither mode weakens the authority model: only the manifest commit algorithm advances a turn ([`DESIGN_SPEC.md` §8.5](DESIGN_SPEC.md#85-turn-handling-modes)).

## How a turn actually flows

Civilization IV has no in-game cloud turn support: every PBEM turn is a save file, and Civilization restarts and loads that save for each turn — it does not stay running between turns. Relay hides the file exchange. In fully managed mode one turn looks like this:

1. Relay polls the authoritative server manifest until it says the turn is yours.
2. Relay downloads the accepted save and verifies its SHA-256 identity.
3. Relay records a durable play-session baseline and launches Civilization directly into the verified save with the configured mod (see [Direct save loading](#direct-save-loading-fxsload-prerequisites-and-dry-run)).
4. You play the turn and press **Next Turn** inside Civilization.
5. Relay detects the new outgoing save, waits until it is stable, verifies it, uploads it, and atomically commits the handoff.
6. Only after the commit is authoritatively proven does Relay request a graceful close of the exact Civilization process it launched (fully managed only).
7. Relay returns to waiting for the next player.

Standard mode runs the same pipeline, except you press the primary button to launch Civilization and Relay never closes it.

## Configuration

Configuration is split in two places; each fact lives in exactly one of them ([`DESIGN_SPEC.md` §4](DESIGN_SPEC.md#4-configuration-model)):

- **Global `.env`** — server and installation settings shared by all matches: SFTP host/port/username/remote root, authentication (password and/or private key), mandatory host-key trust (`known_hosts` file or `SHA256:` fingerprint pin), poll interval, log level, the Civilization executable path (`CIV4_RELAY_CIV4_EXECUTABLE`), and—on Steam—the Steam app id and client path. Placeholder shape: [`.env.example`](../.env.example); adapter details: [`SFTP_ADAPTER.md`](SFTP_ADAPTER.md).
- **Per-match settings** (Match ▸ Add/Edit match in the UI) — game ID, display name, ordered human players and which one is you, PBEM save folder and filename pattern, mod name (default `AdvCiv`), turn handling mode, and the advanced force-close opt-in.

First-run setup:

1. Copy [`.env.example`](../.env.example) to a local `.env` and fill in the placeholders. The client looks for `.env` in its user data directory (`%APPDATA%\civ4-turn-relay`) and then the current working directory; the Settings dialog offers **Create from .env.example** and **Open .env folder** to help.
2. Start the client with `civ4-turn-relay-ui`. If the configuration cannot be loaded, Relay opens without a server connection and shows the settings dialog instead of crashing.
3. Add a match (Match ▸ Add match…) with the players in turn order. The host answers **Yes** to "Initialize remote match?" to create the remote metadata; other players answer **No** and only store the local configuration.
4. The first listed player creates the PBEM game **inside Civilization itself** and saves the first turn into the configured PBEM folder. Relay detects and sends that first outgoing save; it cannot generate a Civilization save.

## Direct save loading (`/fxsload`) prerequisites and dry-run

For the Steam edition, configure both values in global `.env` (the app id is `8800` for the Beyond the Sword desktop shortcut):

```text
CIV4_RELAY_STEAM_APP_ID=8800
CIV4_RELAY_STEAM_EXECUTABLE=C:\Program Files (x86)\Steam\steam.exe
```

Relay starts Steam first when it is not already running. Steam normally retains its signed-in session; if Steam itself asks for a login or update, complete that interaction and then use **Start / Resume Civilization** again. Relay does **not** use Steam's `-applaunch` command because Steam does not faithfully pass these legacy BTS arguments through.

Relay then launches the raw BTS executable with exactly this command shape and nothing else — no shell, no extra flags (built in `src/civ4_turn_relay/process/launch_config.py`):

- the configured executable,
- if a save is being loaded, one single argument `/fxsload=<absolute save path>`, **first**,
- if a mod is configured, one single argument in Civ IV's old command-line form: the configured Civ-relative folder `Mods\AdvCiv` is translated to `mod=\AdvCiv`; Civ IV supplies the `Mods` root itself. It must be **last**, because Civ IV's legacy `mod=` parser consumes later text as part of the mod path.

Leaving the mod value empty omits the `mod=` argument entirely, deliberately deferring to the mod configured in the Civilization INI. The token is validated before use: it must be a relative folder token — absolute paths, drive letters, traversal (`..`), quotes, control characters, and stray leading/trailing whitespace are all rejected.

The dry-run preview renders the command as one Windows-quoted command line (arguments containing spaces are quoted; the `mod=` and `/fxsload=` arguments only when they contain spaces):

```text
"C:\Games\Civ4\Beyond the Sword\Civ4BeyondSword.exe" "/fxsload=C:\Users\you\Documents\My Games\Beyond the Sword\Saves\PBEM\turn.CivBeyondSwordSave" mod=\AdvCiv
```

**Empirical status:** tested against Steam/Beyond the Sword/Advanced Civ on 2026-08-14. `SteamAppId=8800` plus `SteamGameId=8800`, a raw BTS launch from its own directory, and `/fxsload=…` before `mod=\AdvCiv` opened the received PBEM turn directly. Passing `mod=Mods\AdvCiv` was rejected by Civ IV as `Mods\ods\AdvCiv`, proving that the game supplies the `Mods` root itself. Steam `-applaunch` does not preserve both legacy arguments and is intentionally not used.

Before any launch, Relay validates the plan and refuses with an actionable reason instead of launching blind: executable not configured (`CIV4_RELAY_CIV4_EXECUTABLE`), executable not found, save not found, save resolving outside the match PBEM directory (including symlinks), or invalid configuration. In fully managed mode a refusal surfaces as the launch-failed process status ("Civilization was not launched" plus the reason) rather than a silent retry loop.

To inspect the command without launching, call `RelayClient.launch_preview(game_id)`: it returns the `LaunchPlan` for the match's current state — either a ready command whose `dry_run_preview()` is the quoted line above, or the refusal outcome and reason.

## Process safety guarantees

- Process identity is always the triple **PID + precise process creation token (`process_create_time_ns`, the high-resolution creation time reported by the process backend) + normalized executable path**. A human-readable second-resolution UTC start timestamp is kept for diagnostics but is never the equality check, so even a PID recycled within the same wall-clock second is detected as a different process. Relay never acts on a PID alone.
- Relay never attaches to, focuses, or closes a manually launched Civilization process — only the exact process it launched and verified.
- Before every launch, an OS-backed cross-instance guard defers the launch while any Civilization of the configured executable is still running anywhere on the computer — see [Cross-instance launch guard](#cross-instance-launch-guard-one-civilization-per-computer).
- A graceful close (a normal Windows close request) is issued only after the turn is authoritatively committed on the server, or a retry is proven to be an idempotent acknowledgement.
- After requesting a close, Relay waits 15 seconds. If Civilization is still open it shows **"Turn safely sent, but Civilization did not close."** with manual **Focus** and **Close** fallback buttons.
- Force close is an advanced per-match opt-in (default off), available only in fully managed mode, and fires at most once — only after the graceful deadline elapsed *and* the durable post-commit entitlement *and* the exact process identity are re-verified.
- Standard mode never closes Civilization automatically, ever.
- A process failure (close refused, deadline elapsed, Relay restart) never changes or rolls back an already committed turn.

Normative closing policy and force-close rules: [`DESIGN_SPEC.md` §8.5](DESIGN_SPEC.md#85-turn-handling-modes).

## Cross-instance launch guard (one Civilization per computer)

Civilization IV cannot meaningfully run twice on one computer, and one PC
often hosts more than one Relay context: several matches in one Relay, or two
isolated Relay profiles (for example one per household player) connected to
the same server. Without coordination, the moment player A commits a turn and
their Civ is still gracefully closing, player B's Relay could download the
new turn and start a second `Civ4BeyondSword.exe`.

Every Civ launch therefore runs as one **guarded launch** operation:

1. Acquire a short-lived interprocess launch guard — a Windows named mutex
   whose name is derived from a SHA-256 digest of the normalized executable
   path (`Local\civ4-turn-relay-launch-<digest>`), so all Relay instances in
   the same Windows session targeting the same Civ installation share one
   guard.
2. While holding the guard, scan the machine's processes for the exact
   configured Civ executable (normalized absolute path, case-insensitive).
3. If a matching process is already running, release the guard and **defer**:
   nothing is spawned, and the existing process is never focused, closed,
   terminated, adopted, or associated with the match.
4. Only when the scan is clear does Relay spawn Civilization, verify the
   spawned process identity, persist the association, and release the guard.

The check and the spawn are never two separate unprotected steps, so two
Relay instances can never both pass the check and both launch. If Windows
reports the guard was abandoned by a crashed Relay, ownership is recovered
(the OS never leaves a stale lock) but a fresh process scan still runs before
any spawn.

A deferred launch is a typed waiting status, not a failure. The three
deferral reasons stay distinct:

- **Existing Civilization** (`WAITING_FOR_EXISTING_CIV`): the UI shows
  **"Your turn is ready — waiting for Civilization to close."**
- **Busy launch guard** (`WAITING_FOR_LAUNCH_GUARD`): another Relay instance
  in this Windows session is currently checking or launching Civilization.
- **Indeterminate scan** (`LAUNCH_SCAN_INDETERMINATE`): a process looks like
  the configured executable but its path could not be verified. The UI shows
  that diagnostic rather than claiming Civilization is already running.

In every deferred case:

- The verified downloaded turn stays ready, and the durable one-launch-per-
  turn key is not consumed — the deferral never counts as a launch.
- **Fully managed:** Relay retries the guarded launch on later ordinary polls
  and launches exactly once when the blocker is gone — no user click needed
  and no rapid retry loop (one attempt per poll tick).
- **Standard:** Relay never schedules a silent automatic launch; press Start
  again after the blocker has cleared.

Scan defensiveness: unrelated processes whose executables cannot be read
never block a launch; a process that *looks like* the Civ executable by name
but cannot be verified fails closed (launch deferred) rather than risking a
second instance; processes disappearing mid-scan are ignored.

**Limitation:** the guard serializes Relay instances only. It cannot prevent
a user or an unrelated external program from manually starting Civilization
at the same moment outside Relay; the scan narrows that window but cannot
eliminate it.

### Two-profile self-test on one PC

A realistic end-to-end rehearsal of a two-player household setup, using a
disposable test server and test saves only (never a live match or real
credentials):

1. Start two isolated Relay instances, each with its own data directory —
   for example by launching each from a shell with a different `%APPDATA%`
   value so their local stores never overlap.
2. Join both profiles (as different players) to the same disposable match,
   both pointing at the same Civ executable but at separate PBEM save
   folders.
3. Let profile A launch Civilization and commit a test turn while keeping
   Civilization open (or closing it slowly).
4. Confirm profile B downloads the new turn but shows
   *"Your turn is ready — waiting for Civilization to close."* instead of
   starting a second Civilization.
5. Close A's Civilization and confirm B launches exactly once on a later
   poll (fully managed) or after pressing Start again (standard).

## Tray and background behavior

- Closing the main window hides Relay to the system tray while any match is active (or whenever a tray is available), so fully managed matches keep running.
- The tray menu offers **Open Relay** and **Quit**; clicking the icon reopens the window.
- Quitting while Civilization is running or a turn operation is in flight asks for confirmation first.
- Quitting never closes Civilization and never destroys match state or retry evidence — durable records survive, and Relay resumes from them (including a pending post-commit close) on the next start.

## Manual Windows smoke-test checklist

Completing this checklist on a real Windows machine with a real Civilization IV: Beyond the Sword + Advanced Civ installation is the **exit evidence for phase P7** ([`PHASE_PLAN.md`](PHASE_PLAN.md)). Every step is non-destructive: use disposable directories, copied test saves, and a disposable server — never a live match.

1. **Configure a local test match and a disposable test save directory.** Add a match through Match ▸ Add match… whose PBEM save folder is a throwaway directory, and copy a test `.CivBeyondSwordSave` into place instead of using any real match data.
2. **Use dry-run to inspect the Civ launch command.** Call `RelayClient.launch_preview(game_id)` and check `dry_run_preview()` against the documented shape above — executable first, then `/fxsload=` with the absolute save path, then `mod=\AdvCiv` last. A non-ready plan must instead carry an actionable refusal reason.
3. **Manually test launch into AdvCiv with a copied test save.** Press the match's primary Start button (or call `request_start`) and confirm Civilization starts, loads the Advanced Civ mod, and opens the save directly without visiting the multiplayer menus. This step is what empirically confirms the `/fxsload` and `mod=<mod folder>` flag behavior.
4. **Confirm Relay records the exact process identity.** After the launch, `RelayClient.process_status(game_id)` must report `RUNNING` with the identity (PID, precise creation token, executable path, plus the diagnostic UTC start timestamp), and the match's durable state must contain the matching process association — including `process_create_time_ns` — so identity survives a Relay restart.
5. **Simulate/perform a completed test handoff.** End the turn in Civilization (or place a distinct valid save into the test folder) and confirm Relay detects a stable candidate, uploads it, and commits it against the disposable test server.
6. **Confirm graceful close.** In a fully managed test match, after the commit the process status must move through `CLOSE_REQUESTED` ("waiting for Civilization to close") to `SAFELY_CLOSED`, with Civilization exiting via a normal window close — no forced termination.
7. **Confirm no close occurs for a mismatched process.** Start Civilization manually (outside Relay) and verify Relay never focuses or closes it; a probe of a stale recorded identity must report a mismatch and the status must state that the mismatched process will not be touched.
8. **Confirm force close remains disabled unless explicitly enabled.** With the opt-in checkbox off, let the 15-second graceful deadline elapse and verify the status shows "Turn safely sent, but Civilization did not close." with Focus/Close buttons and no termination; only with the advanced opt-in enabled may the status become force-close eligible.
9. **Run the disposable OpenSSH integration tests on a Docker-capable machine before real-server use.** Run `pytest -m openssh_sftp` and confirm it passes; see [`SFTP_ADAPTER.md`](SFTP_ADAPTER.md) for the harness details.

Record the outcome of each step (with notes for any deviation) when closing P7.
