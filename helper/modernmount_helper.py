#!/usr/bin/python3
# SPDX-FileCopyrightText: 2026 ModernMount contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYRIGHT and LICENSE for the notice and full terms.

"""Small, Polkit-gated TPM2 enrollment service. No shell or generic command API."""
from concurrent.futures import ThreadPoolExecutor
import glob
import json
import os
import re
import shutil
import subprocess
import threading

from gi.repository import Gio, GLib

BUS = "io.github.modernmount.Helper"
PATH = "/io/github/modernmount/Helper"
UDISKS = "org.freedesktop.UDisks2"
BLOCK = UDISKS + ".Block"
FS = UDISKS + ".Filesystem"
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
XML = """<node><interface name="io.github.modernmount.Helper">
<method name="GetStatus"><arg type="s" direction="out"/></method>
<method name="CheckTpm"><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
<method name="EnrollTpm"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
</interface></node>"""


def tool(name):
    path = shutil.which(name, path=SAFE_PATH)
    if not path:
        raise ValueError(f"Install {name} on the host first.")
    return path


def decode(value):
    return bytes(value).rstrip(b"\0").decode() if isinstance(value, (list, bytes, tuple)) else str(value or "")


def is_system(path):
    path = os.path.normpath(path)
    return path == "/" or any(path == p or path.startswith(p + "/") for p in
        ("/boot", "/efi", "/usr", "/etc", "/var", "/home", "/root", "/dev", "/proc", "/sys", "/bin", "/sbin", "/lib", "/lib64"))


def resolve_data_volume(connection, uuid):
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", uuid):
        raise ValueError("Invalid LUKS UUID.")
    objects = connection.call_sync(UDISKS, "/org/freedesktop/UDisks2", "org.freedesktop.DBus.ObjectManager",
        "GetManagedObjects", None, None, Gio.DBusCallFlags.NONE, 10000, None).unpack()[0]
    matches = [(path, obj[BLOCK]) for path, obj in objects.items() if BLOCK in obj and obj[BLOCK].get("IdUUID") == uuid]
    if len(matches) != 1:
        raise ValueError("The encrypted volume is missing or its UUID is ambiguous.")
    path, block = matches[0]
    if block.get("IdType") != "crypto_LUKS" or block.get("IdVersion") != "2" or block.get("ReadOnly"):
        raise ValueError("TPM enrollment requires a writable LUKS2 container.")
    children = [obj for obj in objects.values() if obj.get(BLOCK, {}).get("CryptoBackingDevice") == path]
    if len(children) != 1 or FS not in children[0]:
        raise ValueError("Unlock a simple data filesystem before enrollment. Root, LVM and nested layouts are not supported.")
    child = children[0]
    mounts = [decode(p) for p in child[FS].get("MountPoints", [])]
    mounts += [decode(item.get("dir")) for kind, item in child[BLOCK].get("Configuration", []) if kind == "fstab"]
    if any(is_system(m) for m in mounts):
        raise ValueError("TPM enrollment of system volumes is not supported by ModernMount.")
    device = decode(block.get("Device"))
    if not device.startswith("/dev/") or os.stat(device).st_rdev != block.get("DeviceNumber"):
        raise ValueError("The device changed. Refresh and try again.")
    return device


def metadata(device):
    result = subprocess.run([tool("cryptsetup"), "luksDump", "--dump-json-metadata", device],
        check=True, capture_output=True, text=True, timeout=15, env={"PATH": SAFE_PATH, "LANG": "C"})
    return json.loads(result.stdout)


def has_tpm(meta):
    slots = meta.get("keyslots", {})
    return any(token.get("type") == "systemd-tpm2" and any(slot in slots for slot in token.get("keyslots", []))
               for token in meta.get("tokens", {}).values())


def authorize(connection, sender):
    params = GLib.Variant("((sa{sv})sa{ss}us)", (
        ("system-bus-name", {"name": GLib.Variant("s", sender)}), BUS + ".enroll-tpm", {}, 1, ""))
    result = connection.call_sync("org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
        "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization", params, None,
        Gio.DBusCallFlags.NONE, 120000, None).unpack()[0]
    if not result[0]:
        raise PermissionError("Administrator authorization was not granted.")


def enroll(connection, sender, uuid, password):
    if not password or len(password.encode()) > 4096 or "\0" in password:
        raise ValueError("Supply an existing LUKS password (up to 4096 bytes).")
    authorize(connection, sender)
    device = resolve_data_volume(connection, uuid)
    before = metadata(device)
    if has_tpm(before):
        return "A systemd TPM2 slot already exists. You can now review the mount configuration."
    # Password never appears in argv, environment, logs, or a persistent file.
    fd = os.memfd_create("modernmount-unlock", os.MFD_CLOEXEC)
    try:
        os.write(fd, password.encode())
        os.lseek(fd, 0, os.SEEK_SET)
        result = subprocess.run([tool("systemd-cryptenroll"), "--tpm2-device=auto", "--tpm2-pcrs=7",
            f"--unlock-key-file=/proc/self/fd/{fd}", device], pass_fds=(fd,), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=90, env={"PATH": SAFE_PATH, "LANG": "C"})
        if result.returncode:
            raise RuntimeError("TPM2 enrollment failed. " + result.stderr.strip()[-1500:])
    finally:
        os.close(fd)
    if not has_tpm(metadata(device)):
        raise RuntimeError("Enrollment returned without a detectable TPM2 token. Check the LUKS header before continuing.")
    return "TPM2 enrolled. Your password is preserved. Review and save the mount settings next."


class Service:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.inflight = threading.Lock()

    def on_bus(self, connection, _name):
        interface = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self.registration = connection.register_object(PATH, interface, self.call, None, None)

    def call(self, connection, sender, _path, _interface, method, params, invocation):
        if method == "GetStatus":
            status = {"available": True, "tpm2": bool(glob.glob("/dev/tpmrm*")) and bool(shutil.which("systemd-cryptenroll", path=SAFE_PATH))}
            invocation.return_value(GLib.Variant("(s)", (json.dumps(status),)))
            return
        if method not in ("CheckTpm", "EnrollTpm"):
            invocation.return_dbus_error(BUS + ".Error", "Unknown method")
            return
        if not self.inflight.acquire(blocking=False):
            invocation.return_dbus_error(BUS + ".Busy", "Another helper operation is in progress.")
            return
        values = params.unpack()

        def execute():
            if method == "CheckTpm":
                return GLib.Variant("(b)", (has_tpm(metadata(resolve_data_volume(connection, values[0]))),))
            return GLib.Variant("(s)", (enroll(connection, sender, *values),))

        future = self.executor.submit(execute)

        def finish():
            self.inflight.release()
            try:
                invocation.return_value(future.result())
            except Exception as error:
                invocation.return_dbus_error(BUS + ".Error", str(error))
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(finish))


def main():
    service = Service()
    owner = Gio.bus_own_name(Gio.BusType.SYSTEM, BUS, Gio.BusNameOwnerFlags.NONE, service.on_bus,
                            None, lambda *_: os._exit(1))
    GLib.MainLoop().run()
    Gio.bus_unown_name(owner)


if __name__ == "__main__":
    main()
