# Release packaging (P9 scaffolding)

Portable and installer scaffolding for **development / release-candidate**
builds. This is **not** a claim that P7, P8, or P9 is complete. P7 remains
ACTIVE until the real-Civ Windows smoke test in
[`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md) is recorded. P8 is implemented but
not COMPLETE until P7 closes and the PySide6 teardown risk is verified fixed
on Windows. P9 stays incomplete until those prerequisites and real artifact
checks land.

## Prerequisites

| Tool | Notes |
|------|-------|
| Windows 10/11 x64 | Target platform |
| Python 3.12+ | Same major as `requires-python` in `pyproject.toml` |
| PowerShell 5.1+ / 7+ | Runs `packaging/build_windows.ps1` |
| PyInstaller 6.x | `pip install "pyinstaller>=6,<8"` in a clean venv with `pip install -e ".[dev]"` |
| Inno Setup 6 | `ISCC.exe` on PATH for the installer |
| Optional `vc_redist.x64.exe` | Official Microsoft x64 redistributable |

Do **not** commit signing certificates, `.env` files, real host keys, or
`packaging/prereq/vc_redist.x64.exe`.

### Optional VC++ redistributable

1. Download the current **x64** `vc_redist.x64.exe` from Microsoft.
2. Verify its version / checksum / digital signature yourself.
3. Place it at `packaging/prereq/vc_redist.x64.exe` (gitignored).
4. Rebuild the installer. When the file is present, Inno Setup copies it to
   `{tmp}` and runs `/install /quiet /norestart`. When absent, that step is
   omitted and the operator must ensure a compatible runtime is already
   installed on the target machine.

## User-data location (must be preserved)

Canonical path from `civ4_turn_relay.ui.app.user_data_dir()`:

```text
%APPDATA%\civ4-turn-relay
```

Typical contents (never packaged, never deleted by upgrade/uninstall):

- `.env`
- `installation.json`
- `matches/<game_id>/config.json`
- `matches/<game_id>/state.json`
- any future logs under that tree

PBEM saves remain in the player-configured Civilization save folders and are
likewise never touched by the installer.

## Portable build

From the repository root on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m pip install "pyinstaller>=6,<8"
powershell -File packaging\build_windows.ps1
```

Outputs (under `dist/`):

- `civ4-turn-relay-<version>-win64-portable\` — runnable folder
- `civ4-turn-relay-<version>-win64-portable.zip` — versioned ZIP

Flags:

- `-SkipZip` — folder only
- `-SkipSmoke` — skip start/stop smoke of the frozen EXE

### Portable use

Unzip anywhere writable by the user and run `civ4-turn-relay.exe`. The app
still stores data in `%APPDATA%\civ4-turn-relay`, not beside the EXE.

## Installer build

After a successful portable build:

```powershell
# Optional: copy verified vc_redist.x64.exe into packaging\prereq\
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" `
  /DMyAppVersion=0.1.0 `
  packaging\installer.iss
```

Output: `dist\civ4-turn-relay-<version>-win64-setup.exe`

### Installer behavior

| Topic | Behavior |
|-------|----------|
| Scope | Per-user (`PrivilegesRequired=lowest`) |
| Install dir | `%LOCALAPPDATA%\Programs\civ4-turn-relay` |
| Shortcuts | Start Menu always; desktop optional |
| Upgrade | Same `AppId`; replaces files under the install dir only |
| Uninstall | Removes the install dir + shortcuts; **does not** remove `%APPDATA%\civ4-turn-relay` |
| Signing | Not configured — unsigned development builds until a future signing decision |

## Signing

These scripts intentionally omit `SignTool` / certificate paths. Unsigned
builds are acceptable for local development and private soak testing. Public
release signing remains an open P9 decision (certificate availability /
whether to sign).

## Artifact checklist

- [ ] Portable EXE starts (smoke or manual)
- [ ] No `.env`, keys, `known_hosts`, saves, or `matches/` tree inside the portable folder/ZIP
- [ ] Installer installs per-user without admin
- [ ] Start Menu shortcut works; optional desktop shortcut works when selected
- [ ] Upgrade leaves `%APPDATA%\civ4-turn-relay` intact
- [ ] Uninstall leaves `%APPDATA%\civ4-turn-relay` and PBEM saves intact
- [ ] Civ launch remains argv-only (`mod=Mods\AdvCiv` as one argument; no shell string)

## PySide6 teardown risk (release gate)

Orderly shutdown now:

1. Stops the worker-owned poll timer on the worker thread
2. Joins the worker before closing `RelayClient`
3. Disconnects queued signals and disposes tray/menu on the Qt thread
4. Hooks `QApplication.aboutToQuit`

Automated coverage:

- In-process UI shutdown tests under `tests/ui/`
- Fresh-interpreter checks in `tests/ui/test_teardown_subprocess.py`

**Windows stress (manual / CI agent with GUI stack):**

```powershell
powershell -File packaging\tools\run_ui_teardown_stress.ps1 -Iterations 20
```

If that stress run aborts with a native heap error, treat PySide6 teardown as
an open **release blocker** even when ordinary `pytest` is green. Do not ship
a release build until the stress run is clean or the defect is otherwise
closed with evidence.

## Remaining before a real release

1. Complete the P7 manual Civ smoke test ([`DESKTOP_CLIENT.md`](DESKTOP_CLIENT.md)).
2. Verify the UI teardown stress script on Windows.
3. Build portable + installer artifacts and run the artifact checklist above.
4. Only then consider closing P7 → P8 → P9 per [`PHASE_PLAN.md`](PHASE_PLAN.md).
