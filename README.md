# ModernMount

A native Linux app for automatic mounts, LUKS unlocking, and TPM2 enrollment.
Built with Python, GTK4 and libadwaita. The interface is packaged for Flatpak;
privileged TPM operations belong to a separate host service.

> **Pre-release:** do not use this version on irreplaceable data. Real boot and
> TPM behavior still needs validation on disposable volumes and physical hardware.

## Run it

Get the source from [GitHub](https://github.com/KaizerFox/ModernMount):

```sh
git clone https://github.com/KaizerFox/ModernMount.git
cd ModernMount
```

Requires Python 3.10+, PyGObject, GTK 4.12+ and libadwaita 1.5+.
Use your distribution's Python with GI bindings, rather than an isolated pip
environment. UDisks2 and a desktop Polkit authentication agent are required
for real disk operations.

```sh
# Explore sample drives without contacting UDisks or changing disks.
make demo

# Discover actual drives. Launch as your regular desktop user, never root.
make run

# Tests do not mutate disks or contact the system bus.
make test
```

The app is a working development version, not a released disk-management utility.
Real startup mounting and TPM enrollment still need validation on disposable
test volumes and a physical TPM before release. Development testing uses
simulated D-Bus responses and the demo backend; it does not enroll this machine's
TPM or edit its mount configuration.

## What works

- Native drive sidebar, search, filesystem details, status, and manual refresh.
- Discovery through UDisks, pairing LUKS containers with their unlocked filesystem.
- Mount/unmount and password unlock through UDisks and its Polkit policies.
- Startup, on-access and manual mount configuration for ext2/3/4, Btrfs,
  XFS, vfat, exFAT and NTFS/NTFS3 (host filesystem support is required).
- Btrfs subvolume paths and compression settings.
- Review and copy the exact proposed `fstab` and `crypttab` entries before saving.
- UDisks configuration writes with stale-state checks and best-effort rollback
  if a later write fails. Existing unrelated options are retained.
- Optional Polkit-gated TPM2 enrollment for unlocked LUKS2 data volumes.
  Enrollment adds a slot; it never wipes existing passwords or tokens.
- A native `lsblk` read-only fallback when the system bus is unavailable.
  The Flatpak requires UDisks and does not use host-command escape permissions.

Saving configuration does not remount anything. Restart to activate startup
and on-access settings. With an encrypted on-access mount, unlocking happens
at startup and filesystem mounting happens on first access. Password unlocking
can therefore still show a boot-time prompt. Manual mode requires explicitly
unlocking and mounting the drive.

## Flatpak

Install Flatpak and flatpak-builder using your distribution's package manager.
With Flathub configured:

```sh
flatpak install --user flathub org.gnome.Platform//49 org.gnome.Sdk//49
flatpak-builder --user --install --force-clean build-dir packaging/io.github.modernmount.ModernMount.json
flatpak run io.github.modernmount.ModernMount --demo
```

The manifest grants access only to the UDisks and ModernMount helper system-bus
names, plus display/GPU permissions. It does not request home-directory access,
host shell access, network access, or direct block-device access. These D-Bus
permissions still allow powerful disk operations; authorization is enforced
by the host services. The helper cannot be installed inside the Flatpak.

The Flatpak build has not yet been executed in the development environment,
which does not have `flatpak` or `flatpak-builder` installed. The app ID is a
development placeholder and should be replaced with an owned identity before
publishing a Flatpak release. Screenshots remain to be added for a Flathub
submission.

## Native install and host helper

Install the interface into `/usr/local`:

```sh
sudo make install
```

The optional helper additionally requires systemd, cryptsetup, UDisks2,
PyGObject/GLib, Polkit and TPM2 support in the host's `systemd-cryptenroll` build.
Install it only on a host where you intend to enable enrollment:

```sh
sudo make install-helper
sudo systemctl daemon-reload
sudo systemctl reload dbus.service
```

It is activated on demand by the system bus; it does not need to be enabled
at boot. D-Bus reload support can vary by distribution; restarting the desktop
or computer also loads the installed policy. For distribution packages, stage
files without modifying the running system:

```sh
make install PREFIX=/usr DESTDIR="$PWD/work/package"
make install-helper DESTDIR="$PWD/work/package"
```

The helper uses a fixed `systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7`
operation, with the existing passphrase passed through an anonymous memory
file descriptor. It offers no arbitrary command execution or file-writing API.
The passphrase is transmitted over the local system bus and exists temporarily
in Python memory; Python does not guarantee secure erasure. No secret is put
in command arguments, environment variables, logs or persistent files.

PCR 7 binds to Secure Boot policy/state. Firmware or Secure Boot changes can
require the existing recovery password. This is not a claim of complete
measured-boot protection; root-drive policy, UKIs, signed PCR policies, TPM PINs,
and automatic reenrollment after updates are outside this version's scope.
An existing systemd TPM slot is detected and kept, not replaced. Detection
confirms token/keyslot presence, not successful TPM unsealing after a reboot.

## Current boundaries

- Data volumes only. Mounted/configured system and home volumes are view-only.
- No formatting, repartitioning, encryption conversion, or keyslot deletion.
- Unlock a LUKS volume before editing its inner filesystem settings.
- Existing keyfile configurations and multiple mount entries are preserved
  but not editable yet; keyfile setup and multiple Btrfs mounts are next steps.
- Btrfs subvolume selection is a path field; automatic subvolume browsing is
  not implemented yet.
- No root/initramfs integration, LVM/nested encrypted layout editing, hotplug
  automount rules, per-login mounts, or non-systemd boot integration.
- Configuration changes across two files are not one atomic transaction.
  Rollback failures are surfaced explicitly; a process crash or external edit
  can still require manual recovery.
- Filesystem availability, TPM libraries and Polkit behavior vary by distro.

## Layout

```text
modernmount/app.py       Native UI; background jobs and review dialogs
modernmount/model.py     Pure validation and configuration planning
modernmount/backend.py   UDisks D-Bus adapter and isolated demo backend
helper/                 Polkit-gated system-bus TPM enrollment service
data/                   Styles, icons, desktop and AppStream metadata
packaging/              Launcher and Flatpak manifest
tests/                  Planner, discovery, transaction and helper tests
```

Keyboard shortcuts: **Ctrl+R** refreshes drives; **Ctrl+W** closes the window.

## Technical references

- [UDisks block configuration API](https://storaged.org/udisks/docs/gdbus-org.freedesktop.UDisks2.Block.html)
- [UDisks encrypted-volume API](https://storaged.org/udisks/docs/gdbus-org.freedesktop.UDisks2.Encrypted.html)
- [systemd-cryptenroll](https://www.freedesktop.org/software/systemd/man/latest/systemd-cryptenroll.html)
- [Flatpak manifests](https://docs.flatpak.org/en/latest/manifests.html)

## License and contributions

ModernMount is licensed under the GNU General Public License, version 3 or
(at your option) any later version: **GPL-3.0-or-later**. See [LICENSE](LICENSE)
for the full terms and [COPYRIGHT](COPYRIGHT) for the project notice and
separately licensed material. Attribution remains “ModernMount contributors.”

Contributions are accepted under the same GPL-3.0-or-later terms; no separate
contributor license agreement is required. See [CONTRIBUTING.md](CONTRIBUTING.md).
Remaining GitHub setup work is tracked in [REPOSITORY_SETUP.md](REPOSITORY_SETUP.md).
