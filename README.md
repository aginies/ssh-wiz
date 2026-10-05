# ssh-wiz

Fast interactive SSH host picker — a console TUI (built on [Textual]) that
reads your `~/.ssh/config` and gets you connected.

- Fuzzy filter (`rzn9` → `ryzen9`), category tabs, per-user colors
- Ranking: favorites first, then most-recently-used (usage decays
  exponentially, 7-day time constant)
- Per-host tmux sessions, password mode, ad-hoc `user@host` connects
- Single file, no build step

## Install

Python 3.10+ with:

```sh
pip install textual rich
```

Put `ssh-wiz` on your `PATH` (it is a single executable script).

## Usage

```
ssh-wiz              launch the interactive picker
ssh-wiz -l           list hosts (ranked), no TUI
ssh-wiz <host>       connect directly, no TUI
ssh-wiz <host> cmd   connect and run cmd (and args) on the host
ssh-wiz -p <host>    connect with -o PubkeyAuthentication=no
ssh-wiz -t <host>    connect inside a per-host tmux session (wiz-<host>)
ssh-wiz -f <host>    toggle favorite
ssh-wiz -h           this help
```

### TUI keys

| key | action |
| --- | --- |
| type | fuzzy-filter hosts (letters in order, e.g. `rzn9` → `ryzen9`) |
| ←/→ (tab/shift-tab) | previous/next category |
| ↑/↓ · PgUp/PgDn · Home/End | move cursor |
| enter | connect to selected host |
| ^f | toggle favorite for selected host |
| ^o | toggle password mode (`-o PubkeyAuthentication=no`) |
| ^t | toggle tmux mode (`tmux new -A -s wiz-<host>`) |
| ^y | copy the ssh command for the selected host |
| ^e | open `~/.ssh/config` at the selected host (`$EDITOR`) |
| ^a | ad-hoc connect (type `user@host`) |
| ^r | refresh host list |
| esc | clear filter / cancel ad-hoc |
| ^q / ^c | quit |

## Configuration

All files are optional; create them as needed.

### `~/.config/ssh-wiz/hosts` — extra hosts

Hosts that are not in `~/.ssh/config`. One per line:

```
name  user@host[:port]  [extra ssh args]
```

- `name` — the alias shown in the picker
- `user@host[:port]` — `user` may be omitted (your default user is used);
  the port is passed to ssh as `-p`
- `extra ssh args` — appended to the ssh command as-is

### `~/.config/ssh-wiz/categories` — categories

One rule per line: `<category>  <host pattern>  [color]`. The first matching
rule wins; patterns are shell globs matched case-sensitively against the host
name. Hosts matching no rule go to `OTHER`. Tab order follows first
appearance; without an explicit color, categories get a color from a fixed
palette.

### `~/.config/ssh-wiz/favorites` — favorites

One host name per line; `#` starts a comment.

### `~/.local/state/ssh-wiz/usage.json` — usage stats

Written by ssh-wiz (last 50 connect timestamps per host). Don't edit.

## Tests

```sh
python3 test_ssh_wiz.py
```
