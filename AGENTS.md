# AGENTS.md

These instructions apply to the entire ModernMount repository.

## Project purpose

ModernMount is a GTK4/libadwaita Linux desktop app for configuring data-volume
mounts, LUKS unlocking, and optional TPM2 enrollment. The Flatpak is the UI;
privileged TPM work belongs in the narrowly scoped host helper.

## Read first

Before changing behavior, read `README.md`, `SECURITY.md`, and the relevant
module. For contribution or licensing work, also read `LICENSE`, `COPYRIGHT`, and
`CONTRIBUTING.md`.

## Architecture

- `modernmount/model.py`: pure validation and configuration planning.
- `modernmount/backend.py`: UDisks D-Bus adapter and isolated demo backend.
- `modernmount/app.py`: GTK/libadwaita UI and background task coordination.
- `helper/`: Polkit-gated system-bus service for fixed TPM2 enrollment actions.
- `packaging/`: native launcher and Flatpak manifest.
- `tests/`: no-real-disk unit tests.

Keep policy and validation in the model where possible. Keep the UI responsive;
blocking D-Bus and subprocess work must stay off the GTK main thread.

## Safety invariants

- Never test against real block devices, mount configuration, LUKS headers, or
  TPM state unless the user explicitly asks and identifies disposable targets.
- System, boot, home, and protected paths remain view-only.
- Do not add formatting, repartitioning, keyslot deletion, or arbitrary host
  command execution without an explicit design and security review.
- Preserve unrelated administrator options and existing recovery methods.
- Verify device identity and configuration immediately before mutation.
- Preview persistent changes before applying them; surface partial rollback.
- Never place passphrases in argv, environment variables, logs, or files.
- The Flatpak must not gain home, host-shell, network, or direct-device access
  without a documented need and review.

## Commands

```sh
make demo
make test
python3 -m compileall -q modernmount helper
desktop-file-validate data/io.github.modernmount.ModernMount.desktop
appstreamcli validate --no-net data/io.github.modernmount.ModernMount.metainfo.xml
```

Use `make install ... DESTDIR=...` for staging. Do not install the helper on the
developer's live system during automated work.

## Code and tests

Target Python 3.10+ and use the system PyGObject stack. Keep core planning code
deterministic and testable without GTK or a system bus. Add meaningful tests for
new validation, serialization, transaction, and privilege boundaries. Demo mode
must never call the real UDisks or helper service.

## Licensing and contributions

The project is open source under GPL-3.0-or-later (GNU GPL version 3 or, at the
recipient's option, any later version). Contributions are accepted under the
same terms without a separate contributor agreement. Preserve separate license
notices identified in `COPYRIGHT`, including the Code of Conduct's CC BY-SA 4.0
and AppStream metadata's CC0-1.0. Do not
replace or relicense project files without the project owner's explicit
instruction.
