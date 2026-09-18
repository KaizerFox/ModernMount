# SPDX-FileCopyrightText: 2026 ModernMount contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYRIGHT and LICENSE for the notice and full terms.

import unittest
from modernmount.model import Volume, MountSettings, build_plan, escape_field, unescape_field


def volume(**kwargs):
    return Volume("/data", "/dev/sdb1", "Games", uuid="1234-5678", filesystem="ext4", **kwargs)


class PlannerTests(unittest.TestCase):
    def test_startup_ext4(self):
        plan = build_plan(volume(), MountSettings("/mnt/games"))
        self.assertIn("UUID=1234-5678  /mnt/games  ext4", plan.preview)
        self.assertEqual(plan.changes[-1].new["passno"], 2)
        self.assertIn("nofail", plan.preview)

    def test_on_access_and_manual(self):
        self.assertIn("x-systemd.automount", build_plan(volume(), MountSettings("/mnt/games", "access")).preview)
        self.assertIn("noauto", build_plan(volume(), MountSettings("/mnt/games", "manual")).preview)

    def test_protected_and_invalid_paths(self):
        for path in ("/", "/boot", "/boot/efi", "/home/user/data", "/usr/local", "/etc", "/mnt", "relative", "//mnt/data", "/mnt/../etc", "/mnt/data\n", "/run/credentials/data"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build_plan(volume(), MountSettings(path))

    def test_system_volume_cannot_be_edited(self):
        for mounts in (["/"], ["/home"], ["/boot"]):
            with self.assertRaises(ValueError):
                build_plan(volume(mounts=mounts), MountSettings("/mnt/elsewhere"))

    def test_configured_system_volume_is_protected_even_unmounted(self):
        v = volume(configuration=[("fstab", {"dir": b"/home\0"})])
        self.assertTrue(v.system)

    def test_escaping_is_reversible(self):
        path = "/mnt/my drive\\backup"
        self.assertEqual(unescape_field(escape_field(path)), path)
        plan = build_plan(volume(), MountSettings(path))
        self.assertIn("/mnt/my\\040drive\\134backup", plan.preview)
        self.assertEqual(plan.changes[-1].new["dir"], path)

    def test_btrfs_settings(self):
        v = volume()
        v.filesystem = "btrfs"
        plan = build_plan(v, MountSettings("/mnt/games", subvolume="@games", compression="zstd:3"))
        self.assertIn("subvol=@games,compress=zstd:3", plan.preview)
        self.assertEqual(plan.changes[-1].new["passno"], 0)

    def test_preserves_admin_options(self):
        v = volume(configuration=[("fstab", {"dir": "/mnt/old", "opts": "noexec,uid=1000,noauto,ro,x-custom=value", "freq": 1, "passno": 2})])
        opts = build_plan(v, MountSettings("/mnt/new")).changes[-1].new["opts"]
        for option in ("noexec", "uid=1000", "x-custom=value", "rw"):
            self.assertIn(option, opts)
        self.assertNotIn("noauto", opts)
        self.assertNotIn("ro", opts.split(","))

    def test_does_not_silently_drop_advanced_compression(self):
        for option in ("compress-force=zstd", "compress=zstd:9"):
            v = volume(configuration=[("fstab", {"dir": "/mnt/data", "opts": option})])
            v.filesystem = "btrfs"
            with self.assertRaisesRegex(ValueError, "compression"):
                build_plan(v, MountSettings("/mnt/data"))

    def test_rejects_multiple_fstab_entries(self):
        with self.assertRaises(ValueError):
            build_plan(volume(configuration=[("fstab", {}), ("fstab", {})]), MountSettings("/mnt/test"))

    def test_luks2_generates_ordered_crypttab_then_fstab(self):
        v = volume(encrypted_path="/crypt", encrypted_uuid="abcd-1234", luks_version="2")
        plan = build_plan(v, MountSettings("/mnt/games", unlock="tpm2"))
        self.assertEqual([c.kind for c in plan.changes], ["crypttab", "fstab"])
        self.assertIn("tpm2-device=auto", plan.preview)
        self.assertEqual(plan.changes[0].new["passphrase-path"], "")

    def test_luks1_cannot_use_tpm(self):
        v = volume(encrypted_path="/crypt", encrypted_uuid="abcd-1234", luks_version="1")
        with self.assertRaises(ValueError):
            build_plan(v, MountSettings("/mnt/games", unlock="tpm2"))

    def test_preserves_existing_mapping_name(self):
        v = volume(encrypted_path="/crypt", encrypted_uuid="abcd", encrypted_configuration=[("crypttab", {"name": "my-existing-name", "options": "luks,discard"})])
        plan = build_plan(v, MountSettings("/mnt/test"))
        self.assertEqual(plan.changes[0].new["name"], "my-existing-name")
        self.assertIn("discard", plan.changes[0].new["options"])

    def test_never_replaces_existing_keyfile(self):
        v = volume(encrypted_path="/crypt", encrypted_uuid="abcd", encrypted_configuration=[("crypttab", {"passphrase-path": "/etc/keys/data"})])
        with self.assertRaisesRegex(ValueError, "keyfile"):
            build_plan(v, MountSettings("/mnt/test"))

    def test_load_existing_settings(self):
        v = volume(configuration=[("fstab", {"dir": b"/mnt/my disk\0", "opts": b"ro,x-systemd.automount\0"})])
        settings = MountSettings.from_volume(v)
        self.assertEqual(settings.location, "/mnt/my disk")
        self.assertEqual(settings.when, "access")
        self.assertTrue(settings.read_only)


if __name__ == "__main__":
    unittest.main()
