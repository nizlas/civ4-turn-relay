# SFTP adapter and global configuration (P6)

This note documents the Paramiko storage adapter and global `.env` boundary. It is not a full end-user setup guide.

## Global `.env` versus per-match config

| Concern | Location |
|---------|----------|
| SFTP host, port, username, remote games root | Global `.env` (`CIV4_RELAY_SFTP_*`) |
| Password / private key / passphrase | Global `.env` only |
| Host-key pin (`known_hosts` and/or SHA-256 fingerprint) | Global `.env` only |
| Connect timeout, poll interval, log level | Global `.env` |
| Local player, mod, PBEM directory, turn-handling mode | Per-match config only |

Match configs must never duplicate server credentials. Placeholder shape: [`.env.example`](../.env.example). **No real server addresses, usernames, passwords, keys, or fingerprints belong in the repository.**

Load configuration with an explicit dotenv path and/or environment mapping via `civ4_turn_relay.app.load_global_config`. Environment values override the dotenv file. The loader does not search arbitrary working directories for secrets.

## Authentication

Provide at least one of:

- `CIV4_RELAY_SFTP_PASSWORD`
- `CIV4_RELAY_SFTP_PRIVATE_KEY_PATH` (optional `CIV4_RELAY_SFTP_PRIVATE_KEY_PASSPHRASE`)

A passphrase without a private-key path is rejected. Password auth remains supported for current user environments; private keys remain preferred where practical.

## Host-key verification

There is no “ignore host-key errors” mode and no `AutoAddPolicy`.

Configure one or both:

1. `CIV4_RELAY_SFTP_KNOWN_HOSTS_PATH` — OpenSSH `known_hosts` file
2. `CIV4_RELAY_SFTP_HOST_KEY_SHA256` — `SHA256:…` fingerprint

Unknown or mismatched host keys fail closed. Diagnostics may name the host and key algorithm; they must not leak credentials.

Obtain a fingerprint without copying private keys:

```text
ssh-keyscan -p 22 sftp.example.com | ssh-keygen -lf - -E sha256
```

First-run trust UI is deferred to P8.

## Adapter root-path semantics

`CIV4_RELAY_SFTP_REMOTE_ROOT` is the **games collection root** for the storage adapter. Protocol paths are relative to that root (`{game_id}/manifest.json`, …). Absolute paths, `..`, Windows separators, and symlink escapes are rejected before remote I/O.

## Capability verification

On connect, `ParamikoStorage` probes a unique contained directory under the configured root (never game data) to prove:

- exclusive `mkdir`
- atomic publish without replacement (OpenSSH `rename` refuse-to-replace)
- atomic replacement (`posix_rename`)
- complete read-back of bytes actually read from the server

If required semantics cannot be proven, the adapter raises `StorageCapabilityError` before a protocol commit can be falsely reported. Capabilities are cached for the current connection only.

## Disposable OpenSSH integration tests

```text
pytest -m openssh_sftp
```

Requires Docker. Ordinary unit tests skip these cases cleanly when Docker/OpenSSH is unavailable. The harness builds an ephemeral Alpine OpenSSH image with fixture credentials only and removes the container on shutdown.
