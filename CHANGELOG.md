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
