# SPDX-FileCopyrightText: 2026 ModernMount contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYRIGHT and LICENSE for the notice and full terms.

"""Run explicitly with a graphical session; all drive access is simulated."""
import time
import unittest
from unittest.mock import patch

from modernmount.app import Application, GLib
from modernmount.backend import DemoBackend


class DemoSwitchTest(unittest.TestCase):
    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        context = GLib.MainContext.default()
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            if condition():
                return
            time.sleep(0.01)
        self.fail('UI operation timed out')

    def test_acknowledgment_and_backend_isolation(self):
        app = Application(demo=True)
        app.register(None)
        real_backend = DemoBackend()
        real_backend.demo = False
        real_backend.volumes = real_backend.volumes[1:2]
        real_backend.volumes[0].name = 'Simulated real drive'
        real_backend.volumes[0].object_path = '/simulated/real'
        with patch('modernmount.app.UDisksBackend', return_value=real_backend) as real_factory, \
             patch.object(real_backend, 'apply', side_effect=AssertionError('Unexpected write')) as apply:
            app.activate()
            window = app.get_active_window()
            try:
                self.wait_for(lambda: not window.busy and len(window.volumes) == 4)
                self.assertEqual(window.banner.get_button_label(), 'Use real drives…')
                window.busy = True
                window.leave_demo()
                self.assertIsNone(window.real_mode_dialog)
                window.busy = False

                window.banner.emit('button-clicked')
                dialog = window.real_mode_dialog
                self.assertFalse(dialog.get_response_enabled('continue'))
                self.assertEqual(dialog.get_default_response(), 'cancel')
                self.assertEqual(dialog.get_close_response(), 'cancel')
                window.leave_demo()
                self.assertIs(window.real_mode_dialog, dialog)
                dialog.close()
                self.wait_for(lambda: window.real_mode_dialog is None)
                self.wait_for(lambda: window.get_visible_dialog() is None)
                real_factory.assert_not_called()
                self.assertTrue(window.backend.demo)

                window.leave_demo()
                dialog = window.real_mode_dialog
                # Even a synthetic response cannot bypass the unchecked box.
                dialog.emit('response', 'continue')
                dialog.close()
                real_factory.assert_not_called()
                self.wait_for(lambda: window.get_visible_dialog() is None)

                window.drafts['/demo/studio'] = object()
                window.search.set_text('Atlas')
                window.leave_demo()
                dialog = window.real_mode_dialog
                check = dialog.get_extra_child()
                check.set_active(True)
                self.assertTrue(dialog.get_response_enabled('continue'))
                check.set_active(False)
                self.assertFalse(dialog.get_response_enabled('continue'))
                check.set_active(True)
                dialog.emit('response', 'continue')
                self.wait_for(lambda: len(app.get_windows()) == 1 and app.get_windows()[0] is not window)
                live = app.get_windows()[0]
                self.wait_for(lambda: not live.busy and bool(live.volumes))
                real_factory.assert_called_once_with()
                self.assertFalse(app.demo)
                self.assertIs(live.backend, real_backend)
                self.assertEqual(live.drafts, {})
                self.assertEqual(live.search.get_text(), '')
                self.assertEqual(live.selected.name, 'Simulated real drive')
                self.assertFalse(live.banner.get_revealed())
                apply.assert_not_called()
            finally:
                for open_window in app.get_windows():
                    self.wait_for(lambda: not open_window.busy)
                    open_window.close()
                app.quit()


if __name__ == '__main__':
    unittest.main()
