# SPDX-FileCopyrightText: 2026 ModernMount contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYRIGHT and LICENSE for the notice and full terms.

from copy import deepcopy
import unittest
from unittest.mock import patch

from gi.repository import GLib
from modernmount.backend import BLOCK, FS, ENC, UDisksBackend, DemoBackend, variant_item, volumes_from_objects
from modernmount.model import MountSettings, build_plan


class DiscoveryTests(unittest.TestCase):
    def test_pairs_luks_and_cleartext_without_duplicate(self):
        objects = {
            "/crypt": {BLOCK: {"IdType": "crypto_LUKS", "IdVersion": "2", "IdUUID": "luks-id", "Device": b"/dev/sda1\0"}, ENC: {}},
            "/clear": {BLOCK: {"IdType": "btrfs", "IdUUID": "fs-id", "CryptoBackingDevice": "/crypt", "Device": b"/dev/mapper/data\0"}, FS: {"MountPoints": [b"/mnt/data\0"]}},
        }
        volumes = volumes_from_objects(objects)
        self.assertEqual(len(volumes), 1)
        self.assertEqual(volumes[0].encrypted_uuid, "luks-id")
        self.assertEqual(volumes[0].filesystem, "btrfs")
        self.assertFalse(volumes[0].locked)

    def test_locked_device_does_not_invent_inner_filesystem(self):
        volumes = volumes_from_objects({"/crypt": {BLOCK: {"IdType": "crypto_LUKS", "IdVersion": "2", "IdUUID": "abc"}, ENC: {}}})
        self.assertTrue(volumes[0].locked)
        self.assertEqual(volumes[0].filesystem, "")
        self.assertFalse(volumes[0].editable)

    def test_dbus_encoding_for_configuration(self):
        item = variant_item("fstab", {"dir": "/mnt/data", "passno": 2, "opts": b"defaults\0"})
        packed = GLib.Variant("((sa{sv})a{sv})", (item, {})).unpack()
        self.assertEqual(bytes(packed[0][1]["dir"]), b"/mnt/data\0")
        self.assertEqual(packed[0][1]["passno"], 2)

    def test_crypttab_includes_required_empty_secret_field(self):
        item = variant_item("crypttab", {"name": "luks-test", "device": "UUID=test", "passphrase-path": "", "options": "luks"})
        packed = GLib.Variant("((sa{sv})a{sv})", (item, {})).unpack()
        self.assertEqual(bytes(packed[0][1]["passphrase-contents"]), b"\0")


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.volume = DemoBackend().discover()[0]
        self.settings = MountSettings("/mnt/atlas")
        self.plan = build_plan(self.volume, self.settings)
        self.backend = UDisksBackend()

    def test_rolls_back_first_write_if_second_fails(self):
        calls = []
        def change(item, reverse=False):
            calls.append((item.kind, reverse))
            if item.kind == "fstab" and not reverse:
                raise RuntimeError("disk gone")
        with patch.object(self.backend, "discover", return_value=[self.volume]), patch.object(self.backend, "_change", side_effect=change):
            with self.assertRaisesRegex(RuntimeError, "restored"):
                self.backend.apply(self.volume, self.settings, self.plan.preview)
        self.assertEqual(calls, [("crypttab", False), ("fstab", False), ("crypttab", True)])

    def test_reports_failed_rollback(self):
        def change(item, reverse=False):
            if item.kind == "fstab" or reverse:
                raise RuntimeError("permission denied")
        with patch.object(self.backend, "discover", return_value=[self.volume]), patch.object(self.backend, "_change", side_effect=change):
            with self.assertRaisesRegex(RuntimeError, "could not be restored"):
                self.backend.apply(self.volume, self.settings, self.plan.preview)

    def test_rejects_configuration_changed_after_review(self):
        fresh = deepcopy(self.volume)
        fresh.configuration = [("fstab", {"dir": "/mnt/new", "opts": "noexec"})]
        with patch.object(self.backend, "discover", return_value=[fresh]), patch.object(self.backend, "_change") as change:
            with self.assertRaisesRegex(ValueError, "changed since review"):
                self.backend.apply(self.volume, self.settings, self.plan.preview)
            change.assert_not_called()

    def test_tpm_must_be_enrolled_before_configuration(self):
        self.settings.unlock = "tpm2"
        plan = build_plan(self.volume, self.settings)
        with patch.object(self.backend, "discover", return_value=[self.volume]), patch.object(self.backend, "helper_status", return_value={"tpm2": True}), patch.object(self.backend, "_call", return_value=(False,)), patch.object(self.backend, "_change") as change:
            with self.assertRaisesRegex(ValueError, "Enroll"):
                self.backend.apply(self.volume, self.settings, plan.preview)
            change.assert_not_called()

    def test_demo_saves_only_in_memory(self):
        backend = DemoBackend()
        volume = backend.discover()[0]
        settings = MountSettings("/mnt/demo")
        with patch.object(UDisksBackend, "_call", side_effect=AssertionError("No real D-Bus allowed")):
            backend.apply(volume, settings, build_plan(volume, settings).preview)
        self.assertEqual(MountSettings.from_volume(backend.discover()[0]).location, "/mnt/demo")


if __name__ == "__main__":
    unittest.main()
