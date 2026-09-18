# SPDX-FileCopyrightText: 2026 ModernMount contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYRIGHT and LICENSE for the notice and full terms.

"""UDisks D-Bus integration. All public calls run off the GTK main thread."""
from copy import deepcopy
import json
import subprocess

from gi.repository import Gio, GLib

from .model import Volume, decode, build_plan

BUS = "org.freedesktop.UDisks2"
BASE = "/org/freedesktop/UDisks2"
BLOCK = BUS + ".Block"
FS = BUS + ".Filesystem"
ENC = BUS + ".Encrypted"
HELPER = "io.github.modernmount.Helper"
HELPER_PATH = "/io/github/modernmount/Helper"


def variant_item(kind, item):
    values = {}
    for key, value in item.items():
        if isinstance(value, GLib.Variant):
            values[key] = value
        elif key in ("freq", "passno"):
            values[key] = GLib.Variant("i", value)
        elif isinstance(value, bool):
            values[key] = GLib.Variant("b", value)
        else:
            values[key] = GLib.Variant("ay", decode(value).encode() + b"\0")
    # UDisks requires this field even for password/TPM entries with no keyfile.
    # It is omitted from the public Configuration property, including on rollback.
    if kind == "crypttab" and "passphrase-contents" not in values:
        values["passphrase-contents"] = GLib.Variant("ay", b"\0")
    return kind, values


def volumes_from_objects(objects):
    volumes = []
    cleartexts = {interfaces[BLOCK].get("CryptoBackingDevice"): path
                  for path, interfaces in objects.items() if BLOCK in interfaces
                  and interfaces[BLOCK].get("CryptoBackingDevice", "/") != "/"}
    for path, interfaces in objects.items():
        block = interfaces.get(BLOCK, {})
        if not block or block.get("HintIgnore"):
            continue
        is_luks = ENC in interfaces and block.get("IdType") == "crypto_LUKS"
        if is_luks and path in cleartexts:
            continue
        if FS not in interfaces and not is_luks:
            continue
        backing_path = path if is_luks else block.get("CryptoBackingDevice", "/")
        backing = objects.get(backing_path, {}).get(BLOCK, {}) if backing_path != "/" else {}
        # Unsupported encryption schemes are shown as filesystems only when unlocked.
        if backing and backing.get("IdType") != "crypto_LUKS":
            continue
        drive = objects.get(block.get("Drive", "/"), {}).get(BUS + ".Drive", {})
        if not drive and backing:
            drive = objects.get(backing.get("Drive", "/"), {}).get(BUS + ".Drive", {})
        device = decode(block.get("PreferredDevice") or block.get("Device"))
        volumes.append(Volume(
            object_path=path, device=device, name=block.get("IdLabel") or drive.get("Model", "").strip() or device.rsplit("/", 1)[-1],
            uuid=block.get("IdUUID", ""), filesystem="" if is_luks else block.get("IdType", ""),
            size=block.get("Size", 0), model=drive.get("Model", "").strip(), connection=drive.get("ConnectionBus", ""),
            mounts=[decode(m) for m in interfaces.get(FS, {}).get("MountPoints", [])],
            configuration=block.get("Configuration", []),
            encrypted_path=backing_path if backing else "", encrypted_uuid=backing.get("IdUUID", ""),
            luks_version=backing.get("IdVersion", ""), encrypted_configuration=backing.get("Configuration", []),
            locked=is_luks, read_only=block.get("ReadOnly", False)))
    return sorted(volumes, key=lambda v: (v.system, v.name.casefold(), v.device))


class UDisksBackend:
    demo = False
    readonly = False

    def _call(self, path, interface, method, params=None, timeout=120000, destination=BUS):
        connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        result = connection.call_sync(destination, path, interface, method, params, None,
                                      Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, timeout, None)
        return result.unpack() if result else ()

    def objects(self):
        return self._call(BASE, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", timeout=10000)[0]

    def discover(self):
        return volumes_from_objects(self.objects())

    def _fresh(self, volume):
        fresh = next((v for v in self.discover() if v.object_path == volume.object_path), None)
        if not fresh or fresh.uuid != volume.uuid or fresh.encrypted_uuid != volume.encrypted_uuid:
            raise ValueError("The drive changed or was disconnected. Refresh and try again.")
        return fresh

    def mount(self, volume):
        fresh = self._fresh(volume)
        if not fresh.editable:
            raise ValueError("Only writable data volumes can be mounted from this app.")
        return self._call(fresh.object_path, FS, "Mount", GLib.Variant("(a{sv})", ({},)))[0]

    def unmount(self, volume):
        fresh = self._fresh(volume)
        if fresh.system:
            raise ValueError("System volumes cannot be unmounted from ModernMount.")
        self._call(fresh.object_path, FS, "Unmount", GLib.Variant("(a{sv})", ({},)))

    def unlock(self, volume, password):
        fresh = self._fresh(volume)
        if not fresh.locked or not password:
            raise ValueError("Enter the password for a locked LUKS volume.")
        self._call(fresh.encrypted_path, ENC, "Unlock", GLib.Variant("(sa{sv})", (password, {})))

    def helper_status(self):
        try:
            return json.loads(self._call(HELPER_PATH, HELPER, "GetStatus", timeout=10000, destination=HELPER)[0])
        except GLib.Error:
            return {"available": False, "tpm2": False}

    def enroll_tpm(self, volume, password):
        if not password:
            raise ValueError("Enter an existing LUKS password.")
        fresh = self._fresh(volume)
        if not fresh.editable or fresh.luks_version != "2":
            raise ValueError("Unlock a LUKS2 data volume before enrolling TPM2.")
        return self._call(HELPER_PATH, HELPER, "EnrollTpm",
                          GLib.Variant("(ss)", (fresh.encrypted_uuid, password)),
                          destination=HELPER)[0]

    def _change(self, change, reverse=False):
        new = variant_item(change.kind, change.new)
        old = variant_item(change.kind, change.old) if change.old is not None else None
        if old is not None:
            args = (new, old, {}) if reverse else (old, new, {})
            self._call(change.object_path, BLOCK, "UpdateConfigurationItem", GLib.Variant("((sa{sv})(sa{sv})a{sv})", args))
        else:
            self._call(change.object_path, BLOCK, "RemoveConfigurationItem" if reverse else "AddConfigurationItem",
                       GLib.Variant("((sa{sv})a{sv})", (new, {})))

    def apply(self, volume, settings, reviewed_preview):
        fresh = self._fresh(volume)
        plan = build_plan(fresh, settings)
        if plan.preview != reviewed_preview or fresh.configuration != volume.configuration or fresh.encrypted_configuration != volume.encrypted_configuration:
            raise ValueError("The drive configuration changed since review. Refresh and review again.")
        if fresh.encrypted and settings.unlock == "tpm2":
            if not self.helper_status().get("tpm2"):
                raise ValueError("Install the ModernMount host helper and enable TPM2 before saving TPM unlock settings.")
            enrolled = self._call(HELPER_PATH, HELPER, "CheckTpm", GLib.Variant("(s)", (fresh.encrypted_uuid,)), destination=HELPER)[0]
            if not enrolled:
                raise ValueError("Enroll this drive’s TPM2 unlock method before saving these settings.")
        # Never take over another volume's mount location.
        for other in self.discover():
            if other.object_path == fresh.object_path:
                continue
            for kind, item in other.configuration:
                if kind == "fstab" and decode(item.get("dir")) == plan.changes[-1].new["dir"]:
                    raise ValueError("Another volume already uses that mount folder.")
            if settings.location in other.mounts:
                raise ValueError("Another volume is currently mounted at that folder.")
        applied = []
        try:
            for change in plan.changes:
                self._change(change)
                applied.append(change)
        except Exception as error:
            rollback_errors = []
            for change in reversed(applied):
                try:
                    self._change(change, reverse=True)
                except Exception as rollback_error:
                    rollback_errors.append(str(rollback_error))
            if rollback_errors:
                raise RuntimeError(f"Saving failed: {error}\nSome changes could not be restored: {'; '.join(rollback_errors)}. Check /etc/fstab and /etc/crypttab before restarting.") from error
            raise RuntimeError(f"Saving failed. Earlier completed changes were restored. {error}") from error
        return "Configuration saved. Restart to activate startup or on-access mounting."


class ReadOnlyBackend:
    """Native fallback when UDisks is unavailable. Never used to mutate disks."""
    demo = False
    readonly = True

    def discover(self):
        data = json.loads(subprocess.run(
            ["lsblk", "--json", "--bytes", "--output", "PATH,TYPE,FSTYPE,LABEL,UUID,SIZE,MOUNTPOINTS,MODEL,TRAN,RO"],
            capture_output=True, text=True, check=True, timeout=10).stdout)
        volumes = []

        def walk(node, parent=None, model="", connection=""):
            model = node.get("model") or model
            connection = node.get("tran") or connection
            luks = node.get("fstype") == "crypto_LUKS"
            children = node.get("children", [])
            filesystem = node.get("fstype")
            if filesystem and filesystem != "swap" and not (luks and children):
                encrypted = node if luks else parent if parent and parent.get("fstype") == "crypto_LUKS" else None
                volumes.append(Volume(node["path"], node["path"], node.get("label") or model or node["path"],
                    uuid=node.get("uuid") or "", filesystem="" if luks else filesystem,
                    size=int(node.get("size") or 0), model=model, connection=connection,
                    mounts=[m for m in node.get("mountpoints", []) if m],
                    encrypted_path=encrypted["path"] if encrypted else "", encrypted_uuid=(encrypted.get("uuid") or "") if encrypted else "",
                    locked=luks, read_only=bool(node.get("ro"))))
            for child in children:
                walk(child, node, model, connection)
        for node in data["blockdevices"]:
            walk(node)
        return volumes


class DemoBackend:
    demo = True
    readonly = False

    def __init__(self):
        self.volumes = [
            Volume("/demo/atlas", "/dev/mapper/atlas", "Atlas", "a891c604-86de-4d53-b42d-ff23e00574a3", "btrfs", 2000398934016,
                   "Samsung SSD 990 PRO", "nvme", ["/mnt/atlas"], encrypted_path="/demo/crypt", encrypted_uuid="b1226d38-cafe-4660-beef-003cc1169890", luks_version="2"),
            Volume("/demo/studio", "/dev/nvme1n1p1", "Studio", "e43220f1-9421-4520-a574-b1d31a12eaab", "ext4", 1000204886016, "WD_BLACK SN850X", "nvme"),
            Volume("/demo/archive", "/dev/sda1", "Field notes", "BD26-AD18", "exfat", 500107862016, "Portable SSD T7", "usb"),
            Volume("/demo/system", "/dev/nvme0n1p2", "Linux", "ea32efb7-5732-4ccd-aeba-d98d306b0327", "btrfs", 512110190592, "System SSD", "nvme", ["/"]),
        ]

    def discover(self):
        return deepcopy(self.volumes)

    def mount(self, volume):
        target = next(v for v in self.volumes if v.object_path == volume.object_path)
        target.mounts = ["/mnt/" + target.name.lower().replace(" ", "-")]
        return target.mounts[0]

    def unmount(self, volume):
        next(v for v in self.volumes if v.object_path == volume.object_path).mounts = []

    def helper_status(self):
        return {"available": True, "tpm2": True}

    def enroll_tpm(self, volume, password):
        return "Demo enrollment complete. No hardware was changed."

    def apply(self, volume, settings, reviewed_preview):
        plan = build_plan(volume, settings)
        target = next(v for v in self.volumes if v.object_path == volume.object_path)
        for change in plan.changes:
            config = target.configuration if change.kind == "fstab" else target.encrypted_configuration
            config[:] = [(kind, item) for kind, item in config if kind != change.kind]
            config.append((change.kind, change.new))
        return "Demo configuration saved in memory. No disks were changed."
