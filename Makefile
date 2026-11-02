PREFIX ?= $(HOME)/.local
BIN := $(PREFIX)/bin/keepassxc-pass

.PHONY: help install uninstall pair test

help:
	@echo "keepassxc-pass"
	@echo ""
	@echo "  install    Link keepassxc-pass into $(PREFIX)/bin"
	@echo "  uninstall  Remove the link"
	@echo "  pair       Pair with the active KeePassXC database (once per database)"
	@echo "  test       Run the regression tests"

install:
	mkdir -p $(dir $(BIN))
	ln -sf $(CURDIR)/keepassxc-pass $(BIN)

uninstall:
	rm -f $(BIN)

pair:
	$(BIN) --pair

test:
	python3 -m unittest discover -s tests -v
