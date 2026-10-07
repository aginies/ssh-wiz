# Changelog

All notable changes to this project are documented in this file.

## [1.3.0] - 2026-10-07

Feature release: an add-host wizard (`^a`) that onboards a new host end to
end — pick or generate a key, install it, verify key auth, and append a
backed-up `~/.ssh/config` block — plus a batch of performance and correctness
fixes to the picker and the file-sync screen.

### Added

- Add-host wizard (`^a`): a form (host, port, user, optional alias, key) that
  walks through onboarding a host in staged steps. Pick an existing keypair or
  generate a per-host ed25519 key, install it with `ssh-copy-id` (the password
  is entered on the handed-over terminal), verify key auth with a `BatchMode`
  probe (no config write on failure unless confirmed), then back up
  `~/.ssh/config` to `.bak` and append the host block. A live preview shows the
  exact block that will be written and warns about earlier `Host` patterns that
  would shadow it. Any failure keeps the form open so you can retry in place;
  `esc`/`q` cancels without touching anything.
- Host/port validation in the wizard: the port field is digits-only (letters
  can't be typed or pasted) and the host is validated as an IPv4/IPv6 address
  or a DNS name, with a subtle red tint on a field while it holds an unusable
  value and a clear error on submit.
- Pure, unit-tested onboarding helpers: `NewHost`, `valid_host`/`valid_port`,
  `build_copyid_cmd`, `build_probe_cmd`, `classify_probe`, `find_ssh_keys`,
  `generate_key`, `backup_ssh_config`, `render_config_block`,
  `config_has_host`, `wildcard_shadows` and `append_host_to_config`.

### Fixed

- Remote file listing broken when the ssh config sets `ControlPath=%t` (a
  regression in 1.2.0).
- Keys leaking to the background host list while an overlay screen (help /
  confirm / add-host / file-sync) is active: the app-level key handler now
  ignores non-quit keys while such a screen is on the stack, so the wizard and
  other overlays own the keyboard.

### Changed

- Performance: config file reads are cached by mtime; the local directory
  listing runs off the UI thread; `FileList.filtered` is cached; the command
  preview re-lays out only when the command changes; the transfer status is
  throttled and capped; the sync screen reuses one SSH connection; and textual
  is imported only when the TUI actually starts (faster `--version`/CLI).

## [1.2.0] - 2026-10-06

Feature release: twin-panel file sync (`^s`) pushes chosen files from the
local filesystem to the selected host over rsync, with in-panel delete and
hidden-file toggling.

### Added

- Twin-panel file sync (`^s`) — a Midnight-Commander-style local/remote file
  picker. Browse the local tree on the left and the selected host's filesystem
  on the right (via `rsync --list-only`), toggle entries with space, navigate
  directories with Enter/left, select a batch with `*`/`/`, then press `F5`
  (or `c`) to push the chosen local files into the remote panel's current
  directory. Transfer uses `rsync -a --itemize-changes` (incremental, preserves
  metadata) with the host's key auth, and streams a per-file result list with a
  text progress indicator. Requires `rsync` on the remote host; if it is not
  present a clear message is shown instead of running the command.
- `F8` deletes the file/dir under the cursor in the active panel (locally, or via
  `ssh … rm -rf` when the remote panel is active), with a per-result status line.
  The remote path is passed to the remote shell as `$1` (not interpolated into the
  command), so names with spaces/quotes/globs need no quoting and cannot inject a
  command; a guard also refuses to build a command unless the path is a real file
  (it can never be empty or `/`), so a missing name can never become `rm -rf -- /`.
- `h` shows or hides hidden ('dot') files and directories in both panels at once
  (hidden entries are hidden by default). The local path bar shows a `(hidden)`
  marker while they are visible.
- Vertical separator between the two panels, the remote path bar now shows the
  host as `user@host`, and a shortcut legend runs along the bottom of the screen.
- Selected entries use a low-contrast indicator: the check turns green while the
  name stays normally coloured, so only the icon marks a selection. The cursor
  row gets a *subtle* blue background (not the harsh white of `reverse`) so you
  always know where you are without a glaring highlight bar.
- `build_transfer_cmd` core helper (unit-tested) that assembles the rsync
  command from a host and pick-as-is sources with a trailing-slash destination
  so a file drops into `dest/file` and a directory into `dest/dir/`

### Notes

- Sync is local → remote push only for now; remote → local pull is a possible
  follow-up. Key auth only — no password prompt.

## [1.0.0] - 2026-10-05

First release.

### Added

- Textual TUI host picker: fuzzy filter, favorites, usage-based ranking,
  per-category tabs, ALL view triaged into per-category sections
- Connect modes: password (`-o PubkeyAuthentication=no`), per-host tmux
  session, ad-hoc `user@host`, remote command passthrough
- CLI: direct connect, `-l` ranked list, `-f` toggle favorite, `-p`/`-t`
  modes, `--version`
- Shell completion for bash, zsh and fish (`--completion <shell>`) with
  fuzzy host matching
- Config files: `~/.config/ssh-wiz/hosts` (extra hosts with per-host ssh
  args), `~/.config/ssh-wiz/categories` (glob rules with colors),
  `~/.local/state/ssh-wiz/usage.json` (connect stats)
- Robustness: corrupted usage state is dropped instead of crashing, IPv6
  and `[v6]:port` targets, ssh-compatible comment parsing, `$SSH_CONFIG`
  support
- Test suite (unit + Textual pilot tests) and ruff lint config

## [1.1.0] - 2026-10-06

Feature release since the first tag. Each `~/.ssh/config` host now offers
one picker entry per `User`, a demo mode for screenshots, and smarter
shell completion.

### Added

- One picker entry per user: a host listed under several `User` blocks gets
  one row each — the first (what plain `ssh <host>` uses) keeps the host
  name, later users connect as `user@host` while the config still applies
  (port, identityfile, …)
- `--demo` mode: run the picker against built-in fake sample data (RFC 2606
  `example.*` domains, TEST-NET addresses) with no real config or
  connections; Enter previews the ssh command instead of running it
- Tailscale auto-categorization: bare IPs in `100.64.0.0/10` land in a
  `TAILSCALE` tab unless a user rule matches first
- `^g` opens `~/.config/ssh-wiz/categories` in `$EDITOR` and reloads
- Single-stage `user@host` shell completion (`--complete-hosts`): the host
  argument completes as one connectable token offering only real
  `user@host` couples (same fuzzy filter as the TUI), instead of a user
  followed by a host

### Changed

- Hosts are now ordered alphabetically within each category (the ALL view
  groups them A→Z) and the identityfile column no longer gets a separator
- `^o` options panel, a live ssh-command preview below the tabs, a
  full-screen key help overlay (`?`/`F1`), and tmux-aware hint lines
- `find_host` resolves a completed `user@host` back to its entry, including
  the first/default user whose target is the bare host name
