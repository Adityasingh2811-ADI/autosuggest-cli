PREFIX ?= /usr/local
BINDIR ?= $(PREFIX)/bin
MANDIR ?= $(PREFIX)/share/man/man1
SYSTEMD_USER_DIR ?= $(HOME)/.config/systemd/user

.PHONY: install install-user install-systemd install-man uninstall help

help:
	@echo "autosuggest-cli installation targets"
	@echo ""
	@echo "  make install          pip install + man page (may need sudo for /usr/local)"
	@echo "  make install-user     pip install --user + man in ~/.local/share/man"
	@echo "  make install-systemd  install systemd user service unit"
	@echo "  make install-man      install man page only"
	@echo "  make uninstall        remove man page and pip package"
	@echo ""
	@echo "Variables:"
	@echo "  PREFIX=$(PREFIX)  BINDIR=$(BINDIR)  MANDIR=$(MANDIR)"

install:
	pip install .
	install -Dm644 contrib/autosuggest.1 $(MANDIR)/autosuggest.1

install-user:
	pip install --user .
	install -Dm644 contrib/autosuggest.1 $(HOME)/.local/share/man/man1/autosuggest.1
	@echo ""
	@echo "Ensure ~/.local/bin is in your PATH."

install-systemd:
	install -Dm644 contrib/autosuggest-daemon.service $(SYSTEMD_USER_DIR)/autosuggest-daemon.service
	systemctl --user daemon-reload
	@echo ""
	@echo "To enable: systemctl --user enable --now autosuggest-daemon"

install-man:
	install -Dm644 contrib/autosuggest.1 $(MANDIR)/autosuggest.1

uninstall:
	rm -f $(MANDIR)/autosuggest.1
	rm -f $(HOME)/.local/share/man/man1/autosuggest.1
	pip uninstall -y cli-autosuggest
