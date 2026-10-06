# Changelog

All notable changes to this project are documented in this file.

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
