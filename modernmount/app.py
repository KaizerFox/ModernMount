# SPDX-FileCopyrightText: 2026 ModernMount contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See COPYRIGHT and LICENSE for the notice and full terms.

"""Native GTK4 / libadwaita application."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, VERSION
from .backend import DemoBackend, ReadOnlyBackend, UDisksBackend
from .model import MountSettings, build_plan

DATA = Path(__file__).resolve().parent.parent / "data"


def label(text, css=None, xalign=0, wrap=False):
    widget = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    return widget


def margins(widget, amount):
    for side in ("top", "bottom", "start", "end"):
        getattr(widget, "set_margin_" + side)(amount)
    return widget


def button(text, callback, css=None, icon=None):
    widget = Gtk.Button(label=text)
    if icon:
        content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        content.append(Gtk.Image.new_from_icon_name(icon))
        content.append(Gtk.Label(label=text))
        widget.set_child(content)
    if css:
        for name in css.split():
            widget.add_css_class(name)
    widget.connect("clicked", callback)
    return widget


class Window(Adw.ApplicationWindow):
    def __init__(self, application, demo=False):
        super().__init__(application=application, title="ModernMount", default_width=1120, default_height=820)
        self.set_size_request(820, 640)
        self.backend = DemoBackend() if demo else UDisksBackend()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="modernmount")
        self.volumes = []
        self.selected = None
        self.busy = False
        self.refreshing = False
        self.drafts = {}
        self.connect("close-request", self.on_close)

        self.toast = Adw.ToastOverlay()
        self.set_content(self.toast)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast.set_child(root)
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        brand = Gtk.Box(spacing=10)
        brand.append(Gtk.Image.new_from_icon_name("modernmount-drive-symbolic"))
        brand.append(label("ModernMount", "brand"))
        header.pack_start(brand)
        header.set_title_widget(label(""))
        if demo:
            header.pack_end(label("DEMO", "demo-pill"))
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)
        menu = Gio.Menu()
        menu.append("Refresh drives", "win.refresh")
        menu.append("About ModernMount", "win.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        header.pack_end(menu_button)
        for name, callback in (("refresh", lambda *_: self.refresh()), ("about", self.about)):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        application.set_accels_for_action("win.refresh", ["<Control>r"])
        application.set_accels_for_action("win.close", ["<Control>w"])
        close_action = Gio.SimpleAction.new("close", None)
        close_action.connect("activate", lambda *_: self.close())
        self.add_action(close_action)
        root.append(header)

        self.banner = Adw.Banner(title="Demo mode · Sample drives. Changes stay in memory.", revealed=demo)
        root.append(self.banner)
        body = Gtk.Box(hexpand=True, vexpand=True)
        root.append(body)

        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, width_request=246)
        sidebar.set_hexpand(False)
        sidebar.add_css_class("sidebar-panel")
        sidebar.set_margin_top(0)
        body.append(sidebar)
        sidebar_heading = Gtk.Box(spacing=8, margin_top=27, margin_start=22, margin_end=18)
        sidebar_heading.append(label("YOUR DRIVES", "eyebrow",))
        spacer = Gtk.Box(hexpand=True)
        sidebar_heading.append(spacer)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh drives (Ctrl+R)")
        refresh.add_css_class("flat")
        refresh.connect("clicked", lambda *_: self.refresh())
        sidebar_heading.append(refresh)
        sidebar.append(sidebar_heading)
        self.search = Gtk.SearchEntry(placeholder_text="Find a drive", margin_start=14, margin_end=14)
        self.search.set_property("width-chars", 15)
        self.search.set_property("max-width-chars", 18)
        self.search.connect("search-changed", self.filter_drives)
        sidebar.append(self.search)
        drive_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.drive_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, margin_start=10, margin_end=10)
        self.drive_list.add_css_class("navigation-sidebar")
        self.drive_list.connect("row-selected", self.select_drive)
        drive_scroll.set_child(self.drive_list)
        sidebar.append(drive_scroll)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=22, margin_end=20, margin_bottom=24)
        footer.append(label("A place for every drive.", "sidebar-caption"))
        self.count_label = label("Discovering volumes…", "dim-label caption")
        footer.append(self.count_label)
        sidebar.append(footer)

        self.content_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        body.append(self.content_scroll)
        self.refresh()

    def on_close(self, *_):
        if self.busy:
            self.toast.add_toast(Adw.Toast(title="Wait for the current operation to finish."))
            return True
        self.executor.shutdown(wait=False, cancel_futures=True)
        return False

    def task(self, function, done):
        if self.busy:
            return
        self.busy = True
        self.spinner.start()
        self.drive_list.set_sensitive(False)
        self.content_scroll.set_sensitive(False)
        future = self.executor.submit(function)

        def finish():
            self.busy = False
            self.spinner.stop()
            self.drive_list.set_sensitive(True)
            self.content_scroll.set_sensitive(True)
            try:
                value = future.result()
            except Exception as error:
                self.error(str(error))
            else:
                done(value)
            return GLib.SOURCE_REMOVE
        future.add_done_callback(lambda _: GLib.idle_add(finish))

    def error(self, message):
        dialog = Adw.AlertDialog(heading="That didn’t work", body=message)
        dialog.add_response("close", "Close")
        dialog.present(self)

    def refresh(self, preferred_id=None):
        if self.busy:
            return
        if self.backend.readonly:
            self.backend = UDisksBackend()
        selected_id = preferred_id or (self.selected.object_path if self.selected else None)

        def discover():
            try:
                return self.backend.discover(), None
            except Exception as error:
                if not self.backend.demo and not self.backend.readonly:
                    fallback = ReadOnlyBackend()
                    try:
                        volumes = fallback.discover()
                    except Exception:
                        raise RuntimeError("Could not connect to UDisks. Ensure udisks2 is running and the app has system D-Bus access.\n\n" + str(error)) from error
                    return volumes, fallback
                raise

        def loaded(result):
            self.volumes, fallback = result
            if fallback:
                self.backend = fallback
                self.banner.set_title("Read-only discovery · UDisks is unavailable. Run outside the development sandbox for disk controls.")
                self.banner.set_revealed(True)
            elif not self.backend.demo:
                self.banner.set_revealed(False)
            self.refreshing = True
            child = self.drive_list.get_first_child()
            while child:
                following = child.get_next_sibling()
                self.drive_list.remove(child)
                child = following
            chosen = None
            for volume in self.volumes:
                row = Gtk.ListBoxRow()
                row.volume = volume
                row.add_css_class("drive-row")
                box = Gtk.Box(spacing=12)
                icon = Gtk.Image.new_from_icon_name("drive-removable-media-symbolic" if volume.connection == "usb" else "modernmount-drive-symbolic")
                icon.set_pixel_size(24)
                box.append(icon)
                info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
                name = label(volume.name, "heading")
                name.set_ellipsize(3)
                name.set_max_width_chars(18)
                info.append(name)
                info.append(label(f"{volume.capacity} · {'LUKS' if volume.locked else volume.filesystem.upper()}", "caption dim-label"))
                box.append(info)
                if volume.encrypted:
                    box.append(Gtk.Image.new_from_icon_name("changes-prevent-symbolic"))
                row.set_child(box)
                self.drive_list.append(row)
                if selected_id == volume.object_path:
                    chosen = row
            self.refreshing = False
            self.count_label.set_text(f"{len(self.volumes)} volumes discovered")
            self.drive_list.select_row(chosen or self.drive_list.get_row_at_index(0))
            self.filter_drives()
            if not self.volumes:
                self.content_scroll.set_child(Adw.StatusPage(title="No volumes found", description="Connect a drive, then refresh to get started.", icon_name="drive-harddisk-symbolic"))
        self.task(discover, loaded)

    def filter_drives(self, *_):
        query = self.search.get_text().casefold()
        self.drive_list.set_filter_func(lambda row: query in (row.volume.name + " " + row.volume.device + " " + row.volume.filesystem).casefold())

    def select_drive(self, _list, row):
        if self.refreshing or not row:
            return
        if self.selected and hasattr(self, "location"):
            self.drafts[self.selected.object_path] = self.settings()
        self.selected = row.volume
        self.render_drive()

    def render_drive(self):
        v = self.selected
        self.content_scroll.get_vadjustment().set_value(0)
        self.location = None
        settings = self.drafts.get(v.object_path) or MountSettings.from_volume(v)
        clamp = Adw.Clamp(maximum_size=850, tightening_threshold=650)
        self.content_scroll.set_child(clamp)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=30, margin_bottom=32, margin_start=32, margin_end=32)
        clamp.set_child(content)
        top = Gtk.Box(spacing=12)
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7, hexpand=True)
        heading.append(label("DRIVE DETAILS", "eyebrow"))
        title = label(v.name, "drive-title")
        title.set_ellipsize(3)
        heading.append(title)
        heading.append(label(v.model or v.device, "dim-label"))
        top.append(heading)
        drive_icon = Gtk.Image.new_from_icon_name("modernmount-drive-symbolic")
        drive_icon.set_pixel_size(36)
        drive_icon.add_css_class("hero-icon")
        top.append(drive_icon)
        content.append(top)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        hero.add_css_class("volume-card")
        status_row = Gtk.Box(spacing=10)
        state = "System volume" if v.system else "Locked" if v.locked else "Mounted" if v.mounts else "Ready to mount"
        status_row.append(label("●  " + state, "status-pill"))
        status_row.append(Gtk.Box(hexpand=True))
        status_row.append(label("LUKS" + (" " + v.luks_version if v.luks_version else "") if v.encrypted else "Unencrypted", "dim-label caption"))
        hero.append(status_row)
        metrics = Gtk.Box(spacing=26, homogeneous=True)
        for caption, value in (("CAPACITY", v.capacity), ("FILESYSTEM", "Encrypted" if v.locked else v.filesystem.upper()), ("CONNECTION", (v.connection or "local").upper())):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            cell.append(label(caption, "eyebrow"))
            cell.append(label(value, "metric"))
            metrics.append(cell)
        hero.append(metrics)
        hero.append(Gtk.Separator())
        bottom = Gtk.Box(spacing=12)
        mount_label = label(v.mounts[0] if v.mounts else "Unlock to see the filesystem" if v.locked else "No active mount", "monospace caption")
        mount_label.set_ellipsize(3)
        mount_label.set_hexpand(True)
        bottom.append(mount_label)
        if not v.system and not self.backend.readonly:
            action = "Unlock" if v.locked else "Unmount" if v.mounts else "Mount"
            mount_button = button(action, self.mount_action, "flat", "changes-allow-symbolic" if v.locked else "media-eject-symbolic" if v.mounts else "media-playback-start-symbolic")
            mount_button.set_sensitive(v.locked or v.editable)
            bottom.append(mount_button)
        hero.append(bottom)
        content.append(hero)

        if v.system:
            content.append(Adw.StatusPage(title="Your system lives here", description="ModernMount manages data drives. Boot, system and home volumes are shown for reference.", icon_name="computer-symbolic"))
            return
        if v.locked:
            content.append(Adw.StatusPage(title="A little privacy, built in", description="Unlock this LUKS volume to discover its filesystem and configure automatic mounting.", icon_name="changes-prevent-symbolic"))
            return
        if not v.editable:
            content.append(Adw.StatusPage(title="View-only volume", description="This filesystem or device is not supported for configuration yet.", icon_name="dialog-information-symbolic"))
            return

        group = Adw.PreferencesGroup(title="Make it feel at home", description="Choose where this drive belongs and when it’s available.")
        self.location = Adw.EntryRow(title="Mount folder", text=settings.location)
        group.add(self.location)
        self.schedule = Adw.ComboRow(title="Mount when", subtitle="Startup, first access, or only when you choose",
            model=Gtk.StringList.new(["At startup", "When accessed", "Manually"]))
        self.schedule.set_selected(["startup", "access", "manual"].index(settings.when))
        group.add(self.schedule)
        self.readonly = Adw.SwitchRow(title="Read-only access", subtitle="Allow reading files without writing to the drive", active=settings.read_only)
        group.add(self.readonly)
        content.append(group)

        self.subvolume = None
        self.compression = None
        if v.filesystem == "btrfs":
            btrfs = Adw.PreferencesGroup(title="Btrfs options")
            self.subvolume = Adw.EntryRow(title="Subvolume · leave empty for the default", text=settings.subvolume)
            btrfs.add(self.subvolume)
            choices = ["", "zstd", "zstd:1", "zstd:3", "lzo", "zlib"]
            self.compression = Adw.ComboRow(title="Compression", model=Gtk.StringList.new(["Filesystem default", "Zstandard", "Zstandard · fast", "Zstandard · level 3", "LZO", "Zlib"]))
            self.compression.set_selected(choices.index(settings.compression) if settings.compression in choices else 0)
            btrfs.add(self.compression)
            content.append(btrfs)

        self.unlock_method = None
        if v.encrypted:
            encryption = Adw.PreferencesGroup(title="Unlock &amp; go", description="Unlocking the container comes before mounting its filesystem.")
            self.unlock_method = Adw.ComboRow(title="Unlock using", subtitle="TPM2 requires enrollment on this computer",
                model=Gtk.StringList.new(["LUKS password", "This computer’s TPM2"]))
            self.unlock_method.set_selected(1 if settings.unlock == "tpm2" else 0)
            encryption.add(self.unlock_method)
            tpm_row = Adw.ActionRow(title="Set up TPM2", subtitle="Bind to Secure Boot state (PCR 7); keep your password")
            enroll = button("Set up…", self.tpm_dialog, "flat")
            enroll.set_valign(Gtk.Align.CENTER)
            enroll.set_sensitive(v.luks_version == "2" and not self.backend.readonly)
            tpm_row.add_suffix(enroll)
            encryption.add(tpm_row)
            content.append(encryption)
            content.append(label("With “When accessed”, the filesystem mounts on demand; the encrypted container is still unlocked at startup.", "dim-label caption", wrap=True))

        actions = Gtk.Box(spacing=12, margin_top=2)
        note = label("Changes take effect after review.", "dim-label caption", wrap=True)
        note.set_hexpand(True)
        actions.append(note)
        actions.append(button("Review changes", self.review, "suggested-action pill", "document-edit-symbolic"))
        content.append(actions)
        details = Adw.PreferencesGroup()
        expander = Adw.ExpanderRow(title="Technical details", subtitle=v.device)
        for title, value in (("Filesystem UUID", v.uuid), ("Encrypted UUID", v.encrypted_uuid)):
            if value:
                row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(value))
                expander.add_row(row)
        details.add(expander)
        content.append(details)

    def settings(self):
        if not self.location:
            return MountSettings.from_volume(self.selected)
        return MountSettings(
            location=self.location.get_text().strip(),
            when=["startup", "access", "manual"][self.schedule.get_selected()],
            read_only=self.readonly.get_active(),
            subvolume=self.subvolume.get_text().strip() if self.subvolume else "",
            compression=["", "zstd", "zstd:1", "zstd:3", "lzo", "zlib"][self.compression.get_selected()] if self.compression else "",
            unlock="tpm2" if self.unlock_method and self.unlock_method.get_selected() == 1 else "password")

    def review(self, *_):
        volume, settings = self.selected, self.settings()
        try:
            plan = build_plan(volume, settings)
        except ValueError as error:
            self.error(str(error))
            return
        dialog = Adw.AlertDialog(heading="Everything in its place", body="Review the configuration below. Saving updates startup settings; it does not remount the drive. Restart afterward to activate the new settings.")
        dialog.set_content_width(660)
        dialog.set_prefer_wide_layout(True)
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        if settings.unlock == "tpm2":
            extra.append(label("TPM2 must already be enrolled. Your existing LUKS password remains the recovery method.", "dim-label", wrap=True))
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=16, bottom_margin=16, left_margin=16, right_margin=16)
        view.get_buffer().set_text(plan.preview)
        view.add_css_class("code-preview")
        scroll = Gtk.ScrolledWindow(min_content_height=160, max_content_height=280, propagate_natural_height=True)
        scroll.set_child(view)
        extra.append(scroll)
        copy = button("Copy configuration", lambda *_: self.copy(plan.preview), "flat", "edit-copy-symbolic")
        copy.set_halign(Gtk.Align.START)
        extra.append(copy)
        dialog.set_extra_child(extra)
        dialog.add_response("cancel", "Back")
        if not self.backend.readonly:
            dialog.add_response("apply", "Save in demo" if self.backend.demo else "Save configuration")
            dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def responded(_dialog, response):
            if response == "apply":
                def saved(message):
                    self.drafts.pop(volume.object_path, None)
                    self.selected = None
                    self.toast.add_toast(Adw.Toast(title=message, timeout=8))
                    self.refresh(volume.object_path)
                self.task(lambda: self.backend.apply(volume, settings, plan.preview), saved)
        dialog.connect("response", responded)
        dialog.present(self)

    def copy(self, text):
        self.get_clipboard().set(text)
        self.toast.add_toast(Adw.Toast(title="Configuration copied"))

    def password_dialog(self, heading, body, callback, tpm=False):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        group = Adw.PreferencesGroup()
        password = Adw.PasswordEntryRow(title="Existing LUKS password")
        group.add(password)
        box.append(group)
        recovery = None
        if tpm:
            recovery = Gtk.CheckButton(label="I have a working recovery password stored safely.")
            box.append(recovery)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("continue", "Enroll TPM2" if tpm else "Unlock")
        dialog.set_response_appearance("continue", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_response_enabled("continue", False)
        dialog.set_close_response("cancel")
        password.connect("changed", lambda *_: dialog.set_response_enabled("continue", bool(password.get_text()) and (not recovery or recovery.get_active())))
        if recovery:
            recovery.connect("toggled", lambda *_: dialog.set_response_enabled("continue", bool(password.get_text()) and recovery.get_active()))

        def response(_dialog, choice):
            secret = password.get_text()
            password.set_text("")
            if choice == "continue":
                callback(secret)
        dialog.connect("response", response)
        dialog.present(self)

    def mount_action(self, *_):
        volume = self.selected
        if volume.locked:
            self.password_dialog("Unlock this drive", f"Enter the LUKS password for {volume.name}.",
                lambda password: self.task(lambda: self.backend.unlock(volume, password), lambda _: self.refresh()))
        else:
            operation = self.backend.unmount if volume.mounts else self.backend.mount
            self.task(lambda: operation(volume), lambda _: self.refresh())

    def tpm_dialog(self, *_):
        volume = self.selected

        def ready(status):
            if not status.get("available"):
                self.error("The ModernMount host helper is not installed. Install the native helper package described in the project README, then try again. The Flatpak frontend cannot install system services.")
                return
            if not status.get("tpm2"):
                self.error("No TPM2 device or systemd-cryptenroll was detected. Enable TPM2 in firmware and install the host dependencies.")
                return

            def enroll(password):
                def enrolled(message):
                    self.unlock_method.set_selected(1)
                    self.toast.add_toast(Adw.Toast(title=message, timeout=8))
                self.task(lambda: self.backend.enroll_tpm(volume, password), enrolled)
            self.password_dialog("Make this computer the key", "Add a TPM2 unlock slot bound to PCR 7. Firmware or Secure Boot changes may require your recovery password. Enrollment changes the LUKS header immediately; review and save the mount configuration separately.", enroll, tpm=True)
        self.task(self.backend.helper_status, ready)

    def about(self, *_):
        dialog = Adw.AboutDialog(application_name="ModernMount", application_icon=APP_ID, version=VERSION,
            developer_name="ModernMount contributors", license_type=Gtk.License.GPL_3_0,
            copyright="Copyright © 2026 ModernMount contributors",
            comments="A place for every drive.\nAutomatic mounts, LUKS and TPM2 in one thoughtful interface.")
        dialog.present(self)


class Application(Adw.Application):
    def __init__(self, demo=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.demo = demo

    def do_activate(self):
        self.get_style_manager().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        display = Gdk.Display.get_default()
        css = Gtk.CssProvider()
        css.load_from_path(str(DATA / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(display).add_search_path(str(DATA / "icons"))
        window = self.get_active_window() or Window(self, self.demo)
        window.present()


def main():
    args = sys.argv[1:]
    if "--version" in args:
        print("ModernMount", VERSION)
        return 0
    if "--help" in args:
        print("Usage: modernmount [--demo] [--version]\n\n--demo   Explore with sample drives; never accesses real disks.")
        return 0
    unknown = set(args) - {"--demo"}
    if unknown:
        print("Unknown arguments: " + ", ".join(sorted(unknown)), file=sys.stderr)
        return 2
    return Application(demo="--demo" in args).run([sys.argv[0]])
