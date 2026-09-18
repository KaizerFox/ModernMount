# SPDX-FileCopyrightText: 2026 ModernMount contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYRIGHT and LICENSE for the notice and full terms.

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("modernmount_helper", Path(__file__).parents[1] / "helper" / "modernmount_helper.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class HelperTests(unittest.TestCase):
    def test_rejects_arbitrary_device_input_before_dbus(self):
        connection = Mock()
        for uuid in ("/dev/sda", "--help", "../../etc/passwd", "abc\nxyz"):
            with self.assertRaises(ValueError):
                helper.resolve_data_volume(connection, uuid)
        connection.call_sync.assert_not_called()

    def test_tpm_token_requires_live_keyslot(self):
        token = {"type": "systemd-tpm2", "keyslots": ["1"]}
        self.assertFalse(helper.has_tpm({"tokens": {"0": token}, "keyslots": {}}))
        self.assertTrue(helper.has_tpm({"tokens": {"0": token}, "keyslots": {"1": {}}}))

    def test_authorization_denial_stops_enrollment(self):
        with patch.object(helper, "authorize", side_effect=PermissionError("Denied")), patch.object(helper, "resolve_data_volume") as resolve:
            with self.assertRaises(PermissionError):
                helper.enroll(Mock(), ":1.10", "uuid", "secret")
            resolve.assert_not_called()

    def test_existing_tpm_is_not_replaced(self):
        meta = {"keyslots": {"1": {}}, "tokens": {"0": {"type": "systemd-tpm2", "keyslots": ["1"]}}}
        with patch.object(helper, "authorize"), patch.object(helper, "resolve_data_volume", return_value="/dev/mock"), patch.object(helper, "metadata", return_value=meta), patch.object(helper.subprocess, "run") as run:
            self.assertIn("already exists", helper.enroll(Mock(), ":1.10", "uuid", "secret"))
            run.assert_not_called()

    def test_enrollment_keeps_password_out_of_command_and_never_wipes_slots(self):
        after = {"keyslots": {"1": {}}, "tokens": {"0": {"type": "systemd-tpm2", "keyslots": ["1"]}}}
        with patch.object(helper, "authorize"), patch.object(helper, "resolve_data_volume", return_value="/dev/mock"), patch.object(helper, "metadata", side_effect=[{}, after]), patch.object(helper, "tool", side_effect=lambda name: "/usr/bin/" + name), patch.object(helper.subprocess, "run", return_value=Mock(returncode=0)) as run:
            helper.enroll(Mock(), ":1.10", "uuid", "test-secret")
        args, kwargs = run.call_args
        self.assertNotIn("test-secret", str(args))
        self.assertNotIn("test-secret", str(kwargs))
        self.assertFalse(any(arg.startswith("--wipe-slot") for arg in args[0]))
        self.assertEqual(len(kwargs["pass_fds"]), 1)


if __name__ == "__main__":
    unittest.main()
