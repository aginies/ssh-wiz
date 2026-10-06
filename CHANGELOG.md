# Changelog

All notable changes to this project are documented in this file.

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
