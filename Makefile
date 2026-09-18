PREFIX ?= /usr/local
DESTDIR ?=
APP_ID = io.github.modernmount.ModernMount
APP_DIR = $(DESTDIR)$(PREFIX)/share/modernmount

.PHONY: run demo test test-ui install install-helper

run:
	python3 -m modernmount

demo:
	python3 -m modernmount --demo

test:
	python3 -m unittest discover -s tests -v

# Requires a graphical session; uses simulated drives only.
test-ui:
	python3 -m tests.ui_demo_switch

install:
	install -d $(APP_DIR)/modernmount $(APP_DIR)/data $(DESTDIR)$(PREFIX)/bin
	install -m644 modernmount/*.py $(APP_DIR)/modernmount/
	install -m644 data/style.css $(APP_DIR)/data/
	install -m644 LICENSE $(APP_DIR)/LICENSE
	install -m644 COPYRIGHT $(APP_DIR)/COPYRIGHT
	cp -R data/icons $(APP_DIR)/data/
	install -m755 packaging/modernmount $(DESTDIR)$(PREFIX)/bin/modernmount
	install -Dm644 data/$(APP_ID).desktop $(DESTDIR)$(PREFIX)/share/applications/$(APP_ID).desktop
	install -Dm644 data/$(APP_ID).metainfo.xml $(DESTDIR)$(PREFIX)/share/metainfo/$(APP_ID).metainfo.xml
	install -Dm644 data/icons/hicolor/scalable/apps/$(APP_ID).svg $(DESTDIR)$(PREFIX)/share/icons/hicolor/scalable/apps/$(APP_ID).svg

# The system service lives on the host, never in the Flatpak sandbox.
# Stage with DESTDIR for native packaging. Live installation requires root.
install-helper:
	install -Dm644 LICENSE $(DESTDIR)/usr/share/licenses/modernmount-helper/LICENSE
	install -Dm644 COPYRIGHT $(DESTDIR)/usr/share/licenses/modernmount-helper/COPYRIGHT
	install -Dm755 helper/modernmount_helper.py $(DESTDIR)/usr/libexec/modernmount-helper
	install -Dm644 helper/io.github.modernmount.Helper.conf $(DESTDIR)/usr/share/dbus-1/system.d/io.github.modernmount.Helper.conf
	install -Dm644 helper/io.github.modernmount.Helper.service $(DESTDIR)/usr/share/dbus-1/system-services/io.github.modernmount.Helper.service
	install -Dm644 helper/io.github.modernmount.Helper.policy $(DESTDIR)/usr/share/polkit-1/actions/io.github.modernmount.Helper.policy
	install -Dm644 helper/modernmount-helper.service $(DESTDIR)/usr/lib/systemd/system/modernmount-helper.service
