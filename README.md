# ssh-wiz

Fast interactive SSH host picker — a console TUI (built on [Textual]) that
reads your `~/.ssh/config` and gets you connected.

- Fuzzy filter (`rzn9` → `ryzen9`), category tabs, per-user colors
- Ranking: favorites first, then most-recently-used, grouped by category
  in the ALL view (usage decays exponentially, 7-day time constant)
- Per-host tmux sessions, password mode
- Fuzzy shell completion for bash, zsh and fish
- Single file, no build step

## Install

Python 3.9+ with:

```sh
pip install "textual>=8.2.0" "rich>=13.0.0"
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
ssh-wiz --complete <prefix>   print hosts/flags matching prefix (completion)
ssh-wiz --completion <shell>  print completion script (bash, zsh or fish)
ssh-wiz --demo       launch the picker with built-in sample data
ssh-wiz --version    print the version
ssh-wiz -h           this help
```

### Demo mode

`ssh-wiz --demo` runs the picker with built-in sample data — fake hosts on
the RFC 2606 `example.*` domains, sample categories, favorites and usage
stats. Your real `~/.ssh/config` and state are never read or written, and
Enter only shows the ssh command that would be run. Useful for demos and
screenshots without exposing real infrastructure.

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
| ^g | open `~/.config/ssh-wiz/categories` (`$EDITOR`), then refresh |
| ^r | refresh host list |
| esc | clear filter |
| ^q / ^c | quit |

### Shell completion

Host names complete with the same fuzzy matching as the TUI
(`rzn9` → `ryzen9`); flags complete by prefix. Add to your shell rc:

```sh
# bash
eval "$(ssh-wiz --completion bash)"

# zsh
eval "$(ssh-wiz --completion zsh)"

# fish
ssh-wiz --completion fish | source
```

## Configuration

Hosts are read from `~/.ssh/config` (or `$SSH_CONFIG` if set).
If the same host appears in several `Host` blocks with different `User`
values, each user gets its own entry: the first one (what plain `ssh <host>`
uses) keeps the host name, the others appear as `<host> (<user>)` and connect
as `user@host` — ssh still applies the config for the host (port,
identityfile, ...). The files below are optional; create them as needed.

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

Built-in IP ranges are auto-categorized when no user rule matches, so hosts
in the Tailscale CGNAT range `100.64.0.0/10` (100.64.0.0 – 100.127.255.255)
land in a `TAILSCALE` tab automatically — no rule needed. This covers both
`~/.ssh/config` names and extra hosts (via their target IP). A user rule
always wins over the built-in detection, so you can still pin a specific
Tailscale IP to another category.

### `~/.config/ssh-wiz/favorites` — favorites

One host name per line; `#` starts a comment.

### `~/.local/state/ssh-wiz/usage.json` — usage stats

Written by ssh-wiz (last 50 connect timestamps per host). Don't edit.

## Tests

```sh
python3 test_ssh_wiz.py
```
