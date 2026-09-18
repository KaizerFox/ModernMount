# SPDX-FileCopyrightText: 2026 ModernMount contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYRIGHT and LICENSE for the notice and full terms.

"""Pure configuration planning. No disk operations happen in this module."""
from dataclasses import dataclass, field
import posixpath
import re


SUPPORTED = {"ext4", "ext3", "ext2", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3"}
PROTECTED = ("/boot", "/efi", "/usr", "/etc", "/var", "/home", "/root", "/dev", "/proc", "/sys", "/bin", "/sbin", "/lib", "/lib64")


def decode(value):
    if isinstance(value, (bytes, bytearray, list, tuple)):
        return bytes(value).rstrip(b"\0").decode("utf-8", errors="replace")
    return str(value or "")


def protected(path):
    path = posixpath.normpath(path)
    return path == "/" or any(path == p or path.startswith(p + "/") for p in PROTECTED)


def escape_field(value):
    return value.replace("\\", "\\134").replace(" ", "\\040").replace("\t", "\\011")


def unescape_field(value):
    return re.sub(r"\\(040|011|134)", lambda m: chr(int(m[1], 8)), value)


@dataclass
class Volume:
    object_path: str
    device: str
    name: str
    uuid: str = ""
    filesystem: str = ""
    size: int = 0
    model: str = ""
    connection: str = ""
    mounts: list = field(default_factory=list)
    configuration: list = field(default_factory=list)
    encrypted_path: str = ""
    encrypted_uuid: str = ""
    luks_version: str = ""
    encrypted_configuration: list = field(default_factory=list)
    locked: bool = False
    read_only: bool = False

    @property
    def encrypted(self):
        return bool(self.encrypted_path)

    @property
    def system(self):
        return any(protected(m) for m in self.mounts) or any(
            kind == "fstab" and protected(decode(item.get("dir")))
            for kind, item in self.configuration if item.get("dir")
        )

    @property
    def editable(self):
        return not self.locked and not self.system and not self.read_only and self.filesystem in SUPPORTED

    @property
    def capacity(self):
        size = float(self.size)
        for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
            if size < 1000 or unit == "PB":
                return f"{size:.0f} {unit}" if size >= 10 else f"{size:.1f} {unit}"
            size /= 1000


@dataclass
class MountSettings:
    location: str = ""
    when: str = "startup"
    read_only: bool = False
    subvolume: str = ""
    compression: str = ""
    unlock: str = "password"

    @classmethod
    def from_volume(cls, volume):
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", volume.name).strip("-").lower() or "drive"
        settings = cls(location=f"/mnt/{slug}")
        entries = [item for kind, item in volume.configuration if kind == "fstab"]
        if len(entries) == 1:
            entry = entries[0]
            settings.location = decode(entry.get("dir"))
            opts = decode(entry.get("opts")).split(",")
            settings.when = "access" if "x-systemd.automount" in opts else "manual" if "noauto" in opts else "startup"
            settings.read_only = "ro" in opts
            settings.subvolume = next((x[7:] for x in opts if x.startswith("subvol=")), "")
            settings.compression = next((x[9:] for x in opts if x.startswith("compress=")), "")
        crypt = [item for kind, item in volume.encrypted_configuration if kind == "crypttab"]
        if crypt:
            opts = decode(crypt[0].get("options"))
            settings.unlock = "tpm2" if "tpm2-device=" in opts else "password"
        return settings


@dataclass
class Change:
    object_path: str
    kind: str
    old: dict | None
    new: dict


@dataclass
class Plan:
    changes: list
    preview: str


def build_plan(volume, settings):
    if not volume.editable:
        raise ValueError("Unlock a supported, writable data volume to configure mounting. System volumes are read-only in ModernMount.")
    path = settings.location
    if not path.startswith("/") or path.startswith("//") or posixpath.normpath(path) != path:
        raise ValueError("Use an absolute folder path without trailing slashes or '..', such as /mnt/games.")
    if any(ord(c) < 32 for c in path) or protected(path) or path in ("/mnt", "/media", "/run"):
        raise ValueError("Choose a dedicated data folder, such as /mnt/games or /media/archive.")
    if path.startswith("/run/") and not path.startswith("/run/media/"):
        raise ValueError("Under /run, use a dedicated folder inside /run/media.")
    if not volume.uuid or not re.fullmatch(r"[a-zA-Z0-9_-]+", volume.uuid):
        raise ValueError("This filesystem does not have a usable UUID.")
    if settings.when not in ("startup", "access", "manual"):
        raise ValueError("Unknown mount schedule.")
    if settings.unlock not in ("password", "tpm2"):
        raise ValueError("Unknown unlock method.")
    if settings.subvolume and (volume.filesystem != "btrfs" or any(c in settings.subvolume for c in ",\n\r\t") or ".." in settings.subvolume.split("/")):
        raise ValueError("Use a Btrfs subvolume path without commas, control characters or '..'.")
    if settings.compression not in ("", "zstd", "zstd:1", "zstd:3", "lzo", "zlib"):
        raise ValueError("Choose a supported compression setting.")
    existing = [item for kind, item in volume.configuration if kind == "fstab"]
    if len(existing) > 1:
        raise ValueError("This volume has multiple mount entries. Editing that layout is not supported yet.")
    old = existing[0] if existing else None
    if old and volume.filesystem == "btrfs":
        current_opts = decode(old.get("opts")).split(",")
        if any(o.startswith("compress-force=") for o in current_opts):
            raise ValueError("This volume uses compress-force. Editing that advanced compression policy is not supported yet.")
        current_compression = next((o[9:] for o in current_opts if o.startswith("compress=")), "")
        if current_compression not in ("", "zstd", "zstd:1", "zstd:3", "lzo", "zlib"):
            raise ValueError("This volume uses an advanced compression level that ModernMount does not edit yet.")
    # Preserve unrelated administrator options, including ownership and security flags.
    managed = {"auto", "noauto", "ro", "rw", "nofail", "x-systemd.automount"}
    opts = [o for o in decode((old or {}).get("opts")).split(",")
            if o and o not in managed and not o.startswith(("subvol=", "subvolid=", "compress=", "compress-force="))]
    if not old:
        opts += ["nosuid", "nodev", "x-gvfs-show", "x-systemd.device-timeout=10s"]
    opts += ["nofail", "ro" if settings.read_only else "rw"]
    opts += {"startup": [], "access": ["x-systemd.automount"], "manual": ["noauto"]}[settings.when]
    if volume.filesystem == "btrfs":
        # Preserve ID-based selection when the UI hasn't supplied a named subvolume.
        if settings.subvolume:
            opts.append("subvol=" + settings.subvolume)
        elif old:
            opts += [o for o in decode(old.get("opts")).split(",") if o.startswith("subvolid=")]
        if settings.compression:
            opts.append("compress=" + settings.compression)
    fstab = dict(old or {})
    fstab.update(fsname=f"UUID={volume.uuid}", dir=path, type=volume.filesystem,
                 opts=",".join(dict.fromkeys(opts)), freq=int((old or {}).get("freq", 0)),
                 passno=int((old or {}).get("passno", 2 if volume.filesystem.startswith("ext") else 0)))
    changes = []
    lines = []
    if volume.encrypted:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", volume.encrypted_uuid):
            raise ValueError("The encrypted container does not have a usable UUID.")
        entries = [item for kind, item in volume.encrypted_configuration if kind == "crypttab"]
        if len(entries) > 1:
            raise ValueError("This volume has multiple encryption entries. Editing that layout is not supported yet.")
        previous = entries[0] if entries else None
        if previous and decode(previous.get("passphrase-path")) not in ("", "none", "-"):
            raise ValueError("This volume uses an existing keyfile. ModernMount preserves it; editing keyfile configurations is not supported yet.")
        crypt_opts = [x for x in decode((previous or {}).get("options")).split(",")
                      if x and x not in ("noauto", "nofail") and not x.startswith("tpm2-device=")]
        crypt_opts += ["luks", "nofail"]
        if settings.when == "manual":
            crypt_opts.append("noauto")
        if settings.unlock == "tpm2":
            if volume.luks_version != "2":
                raise ValueError("TPM2 automatic unlocking requires a detected LUKS2 container.")
            crypt_opts.append("tpm2-device=auto")
        crypt = dict(previous or {})
        crypt.update(name=decode((previous or {}).get("name")) or f"luks-{volume.encrypted_uuid}",
                     device=f"UUID={volume.encrypted_uuid}", **{"passphrase-path": ""},
                     options=",".join(dict.fromkeys(crypt_opts)))
        changes.append(Change(volume.encrypted_path, "crypttab", previous, crypt))
        lines.extend(["# /etc/crypttab", f"{crypt['name']}  {crypt['device']}  none  {crypt['options']}", ""])
    changes.append(Change(volume.object_path, "fstab", old, fstab))
    # UDisks accepts raw fields and escapes them on disk. Escape only the text preview.
    lines += ["# /etc/fstab", f"{escape_field(fstab['fsname'])}  {escape_field(fstab['dir'])}  {fstab['type']}  {escape_field(fstab['opts'])}  {fstab['freq']}  {fstab['passno']}"]
    return Plan(changes, "\n".join(lines))
