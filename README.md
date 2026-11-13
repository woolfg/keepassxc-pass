# keepassxc-pass

Prints a password from the running KeePassXC – no master password prompt while
the database is unlocked. It talks to KeePassXC through the browser
integration (via [keepassxc-proxy-client](https://pypi.org/project/keepassxc-proxy-client/)),
like the browser extension does.

Typical use: Ansible `vault_password_file`, shell scripts, or ad hoc on the
command line.

## Setup (once per database on each machine)

Requires [uv](https://docs.astral.sh/uv/); the script declares its dependency
inline and `uv run --script` installs it on first use.

1. KeePassXC: enable *Settings → Browser Integration* (no browser needs to be
   ticked).
2. `make install` links `keepassxc-pass` into `~/.local/bin`.
3. Select the database's tab in KeePassXC, unlock it, then run `make pair`
   (or `keepassxc-pass --pair`) and confirm the dialog in KeePassXC.
   Associations are stored by database hash in
   `~/.config/keepassxc-pass/association.json` (mode 0600). They grant access
   to entries with a URL – treat this file like a key file.

### Multiple databases

Repeat `keepassxc-pass --pair` once with each database active in KeePassXC.
Adding a database keeps all previous associations. Running `--pair` for an
already paired database reuses its authorization; if that authorization was
removed in KeePassXC, it lets you pair again.

Password lookups send the keys of **all** paired databases, so the entry can
live in any of them, independent of the selected tab:

- Enable *Settings → Browser Integration → Advanced → Search in all opened
  databases for matching credentials* in KeePassXC. Then every unlocked,
  paired database is searched and the UUID picks the entry.
- Without that option, KeePassXC only searches the active database: switch to
  the tab of the database that holds the entry before running the command.
- Either way, the active tab must be a paired, unlocked database: KeePassXC
  only answers after the tool proves its association with the active one.
  Locked databases are never searched.

You do not need to delete `association.json` or re-authorize when switching
between paired databases.

Existing single-database association files are migrated automatically when
their database is used. You can pair a new database first: the old key is
preserved until its original database is active again.

## Entries

The browser integration can only search by URL. Give every entry you want to
use with this tool the URL `https://cli.keepassxc`; the entry's UUID (KeePassXC:
entry → *Advanced* / *Properties*) then picks the right one.

## Usage

```sh
keepassxc-pass                                   # uses the nearest .keepassxc.toml
keepassxc-pass --uuid 8976ad09822b45f491a14cfaa385c7a3
keepassxc-pass --config path/to/.keepassxc.toml
keepassxc-pass --no-prompt                       # fail instead of asking in the terminal
```

Only the password goes to stdout. If KeePassXC is not running, locked or does
not return the entry, the tool asks in the terminal (unless `--no-prompt`).

## Configuration

Settings are merged, later wins:

1. built-in defaults (`url = "https://cli.keepassxc"`, `prompt = "Password: "`)
2. `~/.config/keepassxc-pass/config.toml` (optional, machine-wide)
3. the nearest `.keepassxc.toml` from the current directory upward, or `--config`
4. command-line flags

A repository commits a `.keepassxc.toml` (the UUID is not secret):

```toml
# KeePassXC entry (URL https://cli.keepassxc) with the Ansible vault password
uuid = "8976ad09822b45f491a14cfaa385c7a3"
prompt = "Vault password: "
```

## Ansible

```ini
# ansible.cfg
[defaults]
vault_password_file = ~/.local/bin/keepassxc-pass
```

Ansible runs the script in the directory it was started from, so put
`.keepassxc.toml` next to `ansible.cfg`.
