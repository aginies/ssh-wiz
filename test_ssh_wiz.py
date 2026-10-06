"""Tests for ssh-wiz. Run:  python3 test_ssh_wiz.py  (or: python3 -m unittest)"""

import asyncio
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_loader = importlib.machinery.SourceFileLoader("sshwiz", str(HERE / "ssh-wiz"))
_spec = importlib.util.spec_from_loader("sshwiz", _loader)
assert _spec is not None
shw = importlib.util.module_from_spec(_spec)
sys.modules["sshwiz"] = shw
_loader.exec_module(shw)
shw._tui()  # define the TUI classes; defers the textual import out of module load

_PATH_ATTRS = (
    "SSH_CONFIG",
    "EXTRA_HOSTS_FILE",
    "CATEGORIES_FILE",
    "FAVORITES_FILE",
    "USAGE_FILE",
    "CONFIG_DIR",
    "STATE_DIR",
)


class TempPathsTestCase(unittest.TestCase):
    """Point the module's file paths at a temp dir (never touch real state)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.ssh_config = root / "ssh_config"
        self.extra_hosts = root / "hosts"
        self.categories = root / "categories"
        self.options_file = root / "options"
        self.favorites = root / "favorites"
        self.usage = root / "usage.json"
        self.option_state = root / "options.json"
        self._saved = {a: getattr(shw, a) for a in _PATH_ATTRS}
        vars(shw).update(
            {
                "SSH_CONFIG": self.ssh_config,
                "EXTRA_HOSTS_FILE": self.extra_hosts,
                "CATEGORIES_FILE": self.categories,
                "OPTIONS_FILE": self.options_file,
                "FAVORITES_FILE": self.favorites,
                "USAGE_FILE": self.usage,
                "OPTION_STATE_FILE": self.option_state,
                "CONFIG_DIR": root,
                "STATE_DIR": root,
            }
        )

    def tearDown(self):
        for a, v in self._saved.items():
            setattr(shw, a, v)

    def run_main(self, *argv):
        old = sys.argv
        sys.argv = ["ssh-wiz", *argv]
        try:
            shw.main()
        finally:
            sys.argv = old


class ParseSshConfigTest(TempPathsTestCase):
    def write_config(self, text: str) -> None:
        self.ssh_config.write_text(text)

    def test_first_value_wins(self):
        self.write_config(
            "Host foo\n  User alice\n  Port 2222\n"
            "Host foo\n  User bob\n  Port 3333\n"
            "Host bar\n  User carol\n"
        )
        hosts, order = shw.parse_ssh_config(self.ssh_config)
        self.assertEqual(order, ["foo", "bar"])
        # every distinct user is kept, in order of appearance
        self.assertEqual(hosts["foo"]["users"], ["alice", "bob"])
        # port/identityfile: first value still wins
        self.assertEqual(hosts["foo"]["port"], "2222")
        self.assertEqual(hosts["bar"]["users"], ["carol"])

    def test_duplicate_users_deduped(self):
        self.write_config(
            "Host foo\n  User alice\nHost foo\n  User alice\n  User bob\n"
        )
        hosts, _order = shw.parse_ssh_config(self.ssh_config)
        self.assertEqual(hosts["foo"]["users"], ["alice", "bob"])

    def test_tabs_and_match_blocks(self):
        self.write_config(
            "Match host *.example\n  Port 9999\n"
            "Host\tbaz\n\tUser tabby\n"
            "Host qux\n  User dave\n"
        )
        hosts, order = shw.parse_ssh_config(self.ssh_config)
        self.assertEqual(order, ["baz", "qux"])
        self.assertEqual(hosts["baz"]["users"], ["tabby"])
        self.assertIsNone(hosts["baz"]["port"])  # Match block skipped

    def test_host_patterns_skipped(self):
        self.write_config("Host *.wild foo\n  User w\n")
        _hosts, order = shw.parse_ssh_config(self.ssh_config)
        self.assertEqual(order, ["foo"])

    def test_comments(self):
        self.write_config(
            "# top comment\n"
            "Host foo\n"
            "  # indented comment\n"
            "  #User evil\n"
            "  User alice # inline comment\n"
            "  Port 2222  # another\n"
            "  IdentityFile id_x#y\n"
        )
        hosts, order = shw.parse_ssh_config(self.ssh_config)
        self.assertEqual(order, ["foo"])
        self.assertEqual(hosts["foo"]["users"], ["alice"])
        self.assertEqual(hosts["foo"]["port"], "2222")
        # no whitespace before '#': part of the value, not a comment
        self.assertEqual(hosts["foo"]["identityfile"], "id_x#y")


class ParseExtraHostsTest(TempPathsTestCase):
    def test_entries(self):
        self.extra_hosts.write_text(
            "# comment\n"
            "myhost  alice@example.com:8022  -o Foo=bar\n"
            "plain  bob@example.com\n"
            "nouser  example.org\n"
            "broken  justonefield\n"
        )
        entries = shw.parse_extra_hosts(self.extra_hosts)
        self.assertEqual(
            [e["name"] for e in entries], ["myhost", "plain", "nouser", "broken"]
        )
        self.assertEqual(entries[0]["target"], "alice@example.com:8022")
        self.assertEqual(entries[0]["extra"], "-o Foo=bar")
        self.assertEqual(entries[2]["extra"], "")

    def test_malformed_extra_args_warned_and_dropped(self):
        self.extra_hosts.write_text("badq  x@y  -o 'unterminated\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            entries = shw.parse_extra_hosts(self.extra_hosts)
        self.assertEqual(entries[0]["extra"], "")
        self.assertIn("malformed extra args", err.getvalue())


class BuildHostsTest(TempPathsTestCase):
    def test_fields(self):
        self.ssh_config.write_text(
            "Host foo\n  User alice\n  Port 2222\n  IdentityFile ~/.ssh/id_foo\n"
        )
        self.extra_hosts.write_text("ex  bob@example.com:8022\nnouser  example.org\n")
        self.categories.write_text("LAN  ex*\n")
        hosts = {h.name: h for h in shw.build_hosts()}
        self.assertEqual(hosts["foo"].user, "alice")
        self.assertEqual(hosts["foo"].port, "2222")
        self.assertIsNone(hosts["foo"].cmd_port)  # ssh reads Port from config
        self.assertEqual(hosts["foo"].identityfile, "~/.ssh/id_foo")
        self.assertEqual(hosts["ex"].user, "bob")
        self.assertEqual(hosts["ex"].cmd_port, "8022")
        self.assertEqual(hosts["ex"].category, "LAN")
        self.assertEqual(hosts["foo"].category, shw.OTHER_CATEGORY)
        self.assertEqual(hosts["nouser"].user, shw._default_user())
        self.assertEqual(hosts["nouser"].display, shw._default_user() + "@example.org")

    def test_multiple_users_per_host(self):
        self.ssh_config.write_text(
            "Host ryzen9\n  Port 22\n  IdentityFile ~/.ssh/id_rsa\n"
            "Host ryzen9\n  User llm\n"
            "Host ryzen9\n  User root\n"
        )
        hosts = shw.build_hosts()
        self.assertEqual([h.name for h in hosts], ["ryzen9", "ryzen9 (root)"])
        # first user: plain config name, ssh resolves it (first value wins)
        self.assertEqual(hosts[0].user, "llm")
        self.assertEqual(hosts[0].target, "ryzen9")
        self.assertEqual(hosts[0].identityfile, "~/.ssh/id_rsa")
        # later user: separate entry with explicit user@host target
        self.assertEqual(hosts[1].user, "root")
        self.assertEqual(hosts[1].target, "root@ryzen9")
        self.assertEqual(hosts[1].host, "ryzen9")
        self.assertEqual(hosts[1].display, "root@ryzen9")
        self.assertEqual(hosts[1].identityfile, "~/.ssh/id_rsa")
        # name column shows the bare host for every user entry
        self.assertEqual(hosts[0].label, "ryzen9")
        self.assertEqual(hosts[1].label, "ryzen9")

    def test_find_host_multi_user_entry(self):
        self.ssh_config.write_text(
            "Host ryzen9\n  User llm\nHost ryzen9\n  User root\n"
        )
        self.assertEqual(shw.find_host("ryzen9").target, "ryzen9")
        self.assertEqual(shw.find_host("ryzen9 (root)").target, "root@ryzen9")

    def test_find_host_user_at_host_completion(self):
        # completion inserts user@host; the first/default user's target is
        # the bare host, so find_host must resolve it back
        self.ssh_config.write_text(
            "Host ryzen9\n  User llm\nHost ryzen9\n  User root\n"
        )
        self.assertEqual(shw.find_host("root@ryzen9").target, "root@ryzen9")
        self.assertEqual(shw.find_host("llm@ryzen9").target, "ryzen9")

    def test_ipv6_and_bracketed_ports(self):
        self.extra_hosts.write_text("v6  ::1\nv6p  [::1]:8022\nv4  10.0.1.5:99\n")
        hosts = {h.name: h for h in shw.build_hosts()}
        self.assertEqual(hosts["v6"].host, "::1")
        self.assertIsNone(hosts["v6"].port)
        self.assertIsNone(hosts["v6"].cmd_port)
        self.assertEqual(hosts["v6"].display, shw._default_user() + "@::1")
        self.assertEqual(hosts["v6p"].host, "::1")
        self.assertEqual(hosts["v6p"].port, "8022")
        self.assertEqual(hosts["v6p"].cmd_port, "8022")
        self.assertEqual(hosts["v4"].host, "10.0.1.5")
        self.assertEqual(hosts["v4"].port, "99")

    def test_tailscale_auto_category(self):
        self.ssh_config.write_text("Host 100.64.9.9\nHost 10.0.1.5\n")
        self.extra_hosts.write_text("tsbox  carol@100.90.1.2\n")
        hosts = {h.name: h for h in shw.build_hosts()}
        self.assertEqual(hosts["100.64.9.9"].category, "TAILSCALE")
        self.assertEqual(hosts["tsbox"].category, "TAILSCALE")
        self.assertEqual(hosts["10.0.1.5"].category, shw.OTHER_CATEGORY)

    def test_tailscale_rule_overrides_builtin(self):
        self.ssh_config.write_text("Host 100.87.36.78\n")
        self.categories.write_text("LAN  100.87.*\n")
        hosts = {h.name: h for h in shw.build_hosts()}
        self.assertEqual(hosts["100.87.36.78"].category, "LAN")


class FavoriteSplitTest(unittest.TestCase):
    def make(self, names):
        return [shw.Host(name=n, display=n, target=n) for n in names]

    def test_no_favorites(self):
        self.assertEqual(shw.favorite_split(self.make(["a", "b"]), set()), 0)

    def test_all_favorites(self):
        self.assertEqual(shw.favorite_split(self.make(["a", "b"]), {"a", "b"}), 2)

    def test_empty(self):
        self.assertEqual(shw.favorite_split([], {"a"}), 0)

    def test_favorites_prefix(self):
        shown = self.make(["a", "b", "c", "d"])
        self.assertEqual(shw.favorite_split(shown, {"a", "b"}), 2)
        self.assertEqual(shw.favorite_split(shown, {"a"}), 1)


class TriageHostsTest(unittest.TestCase):
    def make(self, name, cat=shw.OTHER_CATEGORY):
        return shw.Host(name=name, display=name, target=name, category=cat)

    def test_favorites_first_then_groups(self):
        hosts = [
            self.make("a", "LAN"),
            self.make("b", "WORK"),
            self.make("c", "LAN"),
            self.make("d", "WORK"),
            self.make("e"),
        ]
        out = shw.triage_hosts(
            hosts, {"c"}, [shw.ALL_CATEGORY, "LAN", "WORK", shw.OTHER_CATEGORY]
        )
        self.assertEqual([h.name for h in out], ["c", "a", "b", "d", "e"])

    def test_no_favorites(self):
        hosts = [
            self.make("a", "LAN"),
            self.make("b", "WORK"),
            self.make("c", "LAN"),
        ]
        out = shw.triage_hosts(
            hosts, set(), [shw.ALL_CATEGORY, "LAN", "WORK", shw.OTHER_CATEGORY]
        )
        self.assertEqual([h.name for h in out], ["a", "c", "b"])

    def test_all_favorites(self):
        hosts = [self.make("a", "LAN"), self.make("b", "WORK")]
        out = shw.triage_hosts(hosts, {"a", "b"}, [shw.ALL_CATEGORY, "LAN", "WORK"])
        self.assertEqual([h.name for h in out], ["a", "b"])

    def test_empty(self):
        self.assertEqual(shw.triage_hosts([], set(), [shw.ALL_CATEGORY]), [])


class RankHostsTest(TempPathsTestCase):
    def make(self, name, cat=shw.OTHER_CATEGORY, label=""):
        return shw.Host(name=name, display=name, target=name, category=cat, label=label)

    def test_alphabetic_by_first_column(self):
        # non-alphabetic input order is sorted by the first column
        hosts = [
            self.make("zeta", "LAN"),
            self.make("alpha", "LAN"),
            self.make("mid", "WORK"),
            self.make("beta", "WORK"),
        ]
        out, _favs = shw.rank_hosts(hosts)
        self.assertEqual([h.name for h in out], ["alpha", "beta", "mid", "zeta"])

    def test_multi_user_grouped_by_label(self):
        # every user of a host stays adjacent (same label); the plain
        # entry comes before the "(user)" variants
        hosts = [
            self.make("zeta (bob)", "LAN", label="zeta"),
            self.make("alpha", "LAN"),
            self.make("zeta", "LAN", label="zeta"),
            self.make("beta", "WORK"),
        ]
        out, _favs = shw.rank_hosts(hosts)
        self.assertEqual([h.name for h in out], ["alpha", "beta", "zeta", "zeta (bob)"])

    def test_favorites_pinned_first(self):
        self.favorites.write_text("mid\n")
        hosts = [
            self.make("zeta", "LAN"),
            self.make("alpha", "LAN"),
            self.make("mid", "WORK"),
        ]
        out, favs = shw.rank_hosts(hosts)
        self.assertEqual(favs, {"mid"})
        self.assertEqual([h.name for h in out], ["mid", "alpha", "zeta"])


class TuiRenderTest(TempPathsTestCase, unittest.IsolatedAsyncioTestCase):
    def write_hosts(self):
        self.ssh_config.write_text(
            "Host alpha\n  User alice\n"
            "Host beta\n  User bob\n"
            "Host gamma\n  User carol\n"
        )
        self.categories.write_text("LAN  alpha\nLAN  gamma\nWORK  beta\n")

    async def test_separator_below_favorites(self):
        self.write_hosts()
        self.favorites.write_text("alpha\n")
        app = shw.SSHWiz()
        async with app.run_test(size=(80, 24)):
            wl = app.query_one(shw.HostList)
            self.assertEqual([h.name for h in wl.shown], ["alpha", "gamma", "beta"])
            lines = str(wl.render()).splitlines()
            self.assertTrue(lines[0].startswith("★ alpha"))
            self.assertEqual(lines[1], "─" * 80)
            self.assertTrue(lines[2].startswith(" LAN"))
            self.assertTrue(lines[3].startswith("  gamma"))
            self.assertTrue(lines[4].startswith(" WORK"))
            self.assertTrue(lines[5].startswith("  beta"))

    async def test_no_separator_without_favorites(self):
        self.write_hosts()
        app = shw.SSHWiz()
        async with app.run_test(size=(80, 24)):
            wl = app.query_one(shw.HostList)
            lines = str(wl.render()).splitlines()
            self.assertTrue(lines[0].startswith(" LAN"))
            self.assertTrue(lines[1].startswith("  alpha"))
            self.assertTrue(lines[2].startswith("  gamma"))
            self.assertTrue(lines[3].startswith(" WORK"))
            self.assertTrue(lines[4].startswith("  beta"))
            # no separators in the list itself (the top line is CmdLine's)
            self.assertEqual(sum(1 for l in lines if l == "─" * 80), 0)

    async def test_multi_user_rows_share_bare_name(self):
        self.ssh_config.write_text(
            "Host ryzen9\n  User root\nHost ryzen9\n  User aginies\n"
        )
        app = shw.SSHWiz()
        async with app.run_test(size=(80, 24)):
            wl = app.query_one(shw.HostList)
            lines = str(wl.render()).splitlines()
            # OTHER header, then both rows: bare host name in
            # column 1, the user only in column 2
            self.assertTrue(lines[0].startswith(" OTHER"))
            self.assertTrue(lines[1].startswith("  ryzen9"))
            self.assertIn("root@ryzen9", lines[1])
            self.assertTrue(lines[2].startswith("  ryzen9"))
            self.assertIn("aginies@ryzen9", lines[2])
            self.assertNotIn("(aginies)", lines[1] + lines[2])

    async def test_scroll_keeps_cursor_visible_with_separator(self):
        self.ssh_config.write_text(
            "\n".join(f"Host h{i:02d}\n  User u{i:02d}\n" for i in range(12)) + "\n"
        )
        self.favorites.write_text("h00\nh01\n")
        app = shw.SSHWiz()
        async with app.run_test(size=(80, 8)):
            wl = app.query_one(shw.HostList)
            wl.cursor = 11  # last host
            lines = str(wl.render()).splitlines()
            self.assertTrue(lines[-1].startswith("  h11"))

    async def test_edit_categories_opens_editor_and_creates_file(self):
        # no categories file yet: ^g must create it with the template header
        self.ssh_config.write_text("Host alpha\n  User alice\nHost beta\n  User bob\n")
        app = shw.SSHWiz()
        with (
            mock.patch.object(
                shw.SSHWiz, "suspend", lambda self: contextlib.nullcontext()
            ),
            mock.patch.dict(os.environ, {"EDITOR": "vi"}),
            mock.patch.object(shw.subprocess, "run") as run,
        ):
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.press("ctrl+g")
        run.assert_called_once_with(["vi", str(self.categories)], check=False)
        self.assertIn("ssh-wiz categories", self.categories.read_text())

    async def test_edit_categories_reloads_tabs(self):
        self.write_hosts()  # LAN alpha/gamma, WORK beta

        def fake_editor(args, **_kw):
            self.categories.write_text("LAN  alpha\nDB  beta\n")

        app = shw.SSHWiz()
        with (
            mock.patch.object(
                shw.SSHWiz, "suspend", lambda self: contextlib.nullcontext()
            ),
            mock.patch.dict(os.environ, {"EDITOR": "vi"}),
            mock.patch.object(shw.subprocess, "run", side_effect=fake_editor),
        ):
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.press("ctrl+g")
                tabs = str(app.query_one("#tabs", shw.Static).render())
        self.assertIn("DB", tabs)
        self.assertNotIn("WORK", tabs)

    async def test_options_panel_toggle_cycle_and_persist(self):
        self.ssh_config.write_text("Host alpha\n  User alice\n")
        app = shw.SSHWiz()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("ctrl+o")
            panel = app.query_one(shw.OptionsPanel)
            self.assertTrue(panel.display)
            self.assertIn("value", str(app.query_one("#hints", shw.Static).render()))
            await pilot.press("right")  # no-pubkey: off → no
            self.assertEqual(app.option_state["no-pubkey"], "PubkeyAuthentication=no")
            await pilot.press("down", "right")  # x11: off → yes
            self.assertEqual(app.option_state["x11"], "ForwardX11=yes")
            await pilot.press("escape")
            self.assertFalse(panel.display)
            # values are kept for the next open
            await pilot.press("ctrl+o")
            self.assertEqual(app.option_state["no-pubkey"], "PubkeyAuthentication=no")
            self.assertEqual(app.option_state["x11"], "ForwardX11=yes")
            await pilot.press("escape")
        # and persisted across runs
        self.assertEqual(
            shw.load_option_state(),
            {
                "no-pubkey": "PubkeyAuthentication=no",
                "x11": "ForwardX11=yes",
                "comp": "",
            },
        )

    async def test_options_appear_in_copied_command(self):
        self.ssh_config.write_text("Host alpha\n  User alice\n")
        app = shw.SSHWiz()
        with mock.patch.object(shw, "copy_to_clipboard", return_value=True) as cp:
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.press("ctrl+o")
                await pilot.press("right")  # no-pubkey on
                await pilot.press("escape")
                await pilot.press("ctrl+y")
        self.assertIn("-o PubkeyAuthentication=no", str(cp.call_args))

    async def test_cmdline_shows_final_command(self):
        self.ssh_config.write_text("Host alpha\n  User alice\nHost beta\n  User bob\n")
        with mock.patch.object(shw, "tmux_available", return_value=True):
            app = shw.SSHWiz()
            async with app.run_test(size=(80, 24)) as pilot:
                cl = app.query_one(shw.CmdLine)
                lines = str(cl.render()).splitlines()
                self.assertIn("ssh alpha", lines[0])
                self.assertEqual(lines[1], "─" * 80)
                await pilot.press("down")
                lines = str(cl.render()).splitlines()
                self.assertIn("ssh beta", lines[0])
                await pilot.press("ctrl+o")
                await pilot.press("right")  # no-pubkey on
                await pilot.press("escape")
                await pilot.press("ctrl+t")  # tmux mode
                lines = str(cl.render()).splitlines()
                self.assertIn("-o PubkeyAuthentication=no", lines[0])
                self.assertIn("-t", lines[0])
                self.assertIn("tmux new -A -s wiz-beta", lines[0])

    async def test_cmdline_wraps_long_command(self):
        self.ssh_config.write_text("")
        self.extra_hosts.write_text("box  root@10.0.1.253\n")
        with mock.patch.object(shw, "tmux_available", return_value=True):
            app = shw.SSHWiz()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.press("ctrl+o")
                await pilot.press("right")  # no-pubkey
                await pilot.press("down", "right")  # x11
                await pilot.press("down", "right")  # comp
                await pilot.press("escape")
                await pilot.press("ctrl+t")
                cl = app.query_one(shw.CmdLine)
                text = str(cl.render())
                # 109-char command wraps: nothing clipped, tmux suffix visible
                self.assertIn("ssh -t -o PubkeyAuthentication=no", text)
                self.assertIn("tmux new -A -s wiz-box", text)
                # the widget grew from 2 rows to 3 (wrapped command)
                self.assertEqual(cl.region.height, 3)

    async def test_lines_below_tabs_and_options(self):
        self.ssh_config.write_text("Host alpha\n  User alice\n")
        app = shw.SSHWiz()
        async with app.run_test(size=(80, 24)):
            tabs = app.query_one("#tabs", shw.Static).styles.border_bottom
            panel = app.query_one(shw.OptionsPanel).styles.border_bottom
            self.assertEqual(tabs[0], "heavy")
            self.assertEqual(panel[0], "heavy")

    async def test_help_overlay(self):
        self.ssh_config.write_text("Host alpha\n  User alice\n")
        with mock.patch.object(shw, "tmux_available", return_value=True):
            app = shw.SSHWiz()
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.press("question_mark")
                self.assertIsInstance(app.screen, shw.HelpScreen)
                text = str(app.screen.query_one(shw.Static).render())
                self.assertIn("^o", text)
                self.assertIn("tmux", text)
                # modal: typing does not filter the list underneath
                await pilot.press("a")
                self.assertEqual(app.query_one(shw.HostList).filter, "")
                await pilot.press("escape")
                self.assertNotIsInstance(app.screen, shw.HelpScreen)
                # F1 opens again, q closes
                await pilot.press("f1")
                self.assertIsInstance(app.screen, shw.HelpScreen)
                await pilot.press("q")
                self.assertNotIsInstance(app.screen, shw.HelpScreen)

    async def test_hints_hide_tmux_when_unavailable(self):
        self.ssh_config.write_text("Host alpha\n  User alice\n")
        with mock.patch.object(shw, "tmux_available", return_value=False):
            app = shw.SSHWiz()
            async with app.run_test(size=(80, 24)) as pilot:
                hints = str(app.query_one("#hints", shw.Static).render())
                self.assertNotIn("^t", hints)
                await pilot.press("ctrl+t")
                self.assertFalse(app.tmux_mode)
                await pilot.press("question_mark")
                text = str(app.screen.query_one(shw.Static).render())
                self.assertNotIn("tmux", text)
                await pilot.press("escape")


class BuildSshCmdTest(unittest.TestCase):
    def make_host(self, **kw):
        base = {"name": "foo", "display": "alice@foo", "target": "foo"}
        base.update(kw)
        return shw.Host(**base)

    def test_options_emitted_in_order(self):
        h = self.make_host()
        cmd = shw.build_ssh_cmd(h, ["ForwardX11=yes", "Compression=auto"])
        self.assertEqual(
            cmd, ["ssh", "-o", "ForwardX11=yes", "-o", "Compression=auto", "foo"]
        )

    def test_options_with_port_extra_and_tmux(self):
        h = self.make_host(cmd_port="8022", extra="-o Foo=bar")
        cmd = shw.build_ssh_cmd(h, ["PubkeyAuthentication=no"], tmux_mode=True)
        self.assertEqual(cmd[:4], ["ssh", "-t", "-o", "PubkeyAuthentication=no"])
        self.assertIn("-p", cmd)
        self.assertIn("foo", cmd)

    def test_full(self):
        h = self.make_host(cmd_port="8022", extra="-o Foo=bar")
        cmd = shw.build_ssh_cmd(h, ["PubkeyAuthentication=no"], tmux_mode=True)
        self.assertEqual(
            cmd,
            [
                "ssh",
                "-t",
                "-o",
                "PubkeyAuthentication=no",
                "-p",
                "8022",
                "-o",
                "Foo=bar",
                "foo",
                "tmux",
                "new",
                "-A",
                "-s",
                "wiz-foo",
            ],
        )

    def test_remote_cmd(self):
        h = self.make_host()
        cmd = shw.build_ssh_cmd(h, False, False, ["ls", "-la"])
        self.assertEqual(cmd, ["ssh", "foo", "ls", "-la"])

    def test_tmux_session_name_sanitized(self):
        h = shw.Host(name="login.suse.de", display="", target="")
        self.assertEqual(shw.tmux_session_name(h), "wiz-login-suse-de")
        h2 = shw.Host(name="user@10.0.1.5:99", display="", target="")
        self.assertEqual(shw.tmux_session_name(h2), "wiz-user-10-0-1-5-99")
        h3 = shw.Host(name="ryzen9.guibland.com (root)", display="", target="")
        self.assertEqual(shw.tmux_session_name(h3), "wiz-ryzen9-guibland-com-root")


class MiscTest(TempPathsTestCase):
    def test_fuzzy_match(self):
        self.assertTrue(shw.fuzzy_match("rzn9", "ryzen9"))
        self.assertTrue(shw.fuzzy_match("ryzen9", "ryzen9"))
        self.assertFalse(shw.fuzzy_match("9znr", "ryzen9"))
        self.assertTrue(shw.fuzzy_match("", "anything"))

    def test_host_category_case_sensitive(self):
        rules = [("LAN", "10.0.1.*", None)]
        self.assertEqual(shw.host_category("10.0.1.5", rules), "LAN")
        self.assertEqual(shw.host_category("zzz", rules), shw.OTHER_CATEGORY)
        self.assertEqual(
            shw.host_category("lan", [("Lan", "LAN*", None)]), shw.OTHER_CATEGORY
        )

    def test_range_category_boundaries(self):
        self.assertEqual(shw.range_category("100.64.0.0"), "TAILSCALE")
        self.assertEqual(shw.range_category("100.127.255.255"), "TAILSCALE")
        self.assertIsNone(shw.range_category("100.63.255.255"))
        self.assertIsNone(shw.range_category("100.128.0.0"))
        self.assertIsNone(shw.range_category("not-an-ip"))
        self.assertIsNone(shw.range_category("100.64.1.2:2222"))  # not bare IP

    def test_host_category_builtin_ranges(self):
        # no rule matches: name or target_host in the range -> TAILSCALE
        self.assertEqual(shw.host_category("100.87.36.78", []), "TAILSCALE")
        self.assertEqual(shw.host_category("buildbox", [], "100.64.5.5"), "TAILSCALE")
        # user rules win over built-in ranges
        rules = [("LAN", "100.87.*", None)]
        self.assertEqual(shw.host_category("100.87.36.78", rules), "LAN")
        # nothing matches -> OTHER
        self.assertEqual(
            shw.host_category("buildbox", [], "192.0.2.5"), shw.OTHER_CATEGORY
        )

    def test_category_order_includes_builtin(self):
        hosts = [
            shw.Host(name="a", display="a", target="a", category="TAILSCALE"),
            shw.Host(name="b", display="b", target="b", category=shw.OTHER_CATEGORY),
        ]
        self.assertEqual(
            shw.category_order(hosts, []),
            ["ALL", "TAILSCALE", shw.OTHER_CATEGORY],
        )
        colors = shw.category_colors(hosts, [])
        self.assertEqual(colors["TAILSCALE"], "#32bea6")

    def test_user_color_stable(self):
        self.assertEqual(shw.user_color("root"), shw.user_color("root"))
        self.assertIn(shw.user_color("root"), shw.USER_COLORS)

    def test_record_use_caps_and_atomic(self):
        for _ in range(60):
            shw.record_use("h")
        self.assertEqual(len(shw.load_usage()["h"]), 50)
        self.assertFalse(self.usage.with_suffix(".tmp").exists())

    def test_load_usage_drops_corrupt_entries(self):
        self.usage.write_text(
            '{"a": [1.0, "x", 2.5], "b": "garbage", "c": 7, "d": [true]}'
        )
        self.assertEqual(shw.load_usage(), {"a": [1.0, 2.5]})

    def test_load_usage_non_dict(self):
        self.usage.write_text("[1, 2]")
        self.assertEqual(shw.load_usage(), {})

    def test_record_use_survives_corrupt_entry(self):
        self.usage.write_text('{"a": "garbage"}')
        shw.record_use("a")  # must not raise
        self.assertEqual(len(shw.load_usage()["a"]), 1)

    def test_toggle_favorite_file(self):
        self.assertTrue(shw.toggle_favorite_file("a"))
        self.assertIn("a", shw.load_favorites())
        self.assertFalse(shw.toggle_favorite_file("a"))
        self.assertNotIn("a", shw.load_favorites())

    def test_category_order_and_colors(self):
        self.categories.write_text("LAN  10.*  magenta\nLAN  100.*\nCLOUD  v*\n")
        rules = shw.load_category_rules(self.categories)
        hosts = [
            shw.Host(name="a", display="a", target="a", category="LAN"),
            shw.Host(name="b", display="b", target="b", category="CLOUD"),
            shw.Host(name="c", display="c", target="c", category="OTHER"),
        ]
        self.assertEqual(
            shw.category_order(hosts, rules), ["ALL", "LAN", "CLOUD", "OTHER"]
        )
        colors = shw.category_colors(hosts, rules)
        self.assertEqual(colors["LAN"], "magenta")
        self.assertEqual(colors["OTHER"], "grey62")
        self.assertIn(colors["CLOUD"], shw.CATEGORY_COLORS)

    def test_category_rules_invalid_color(self):
        self.categories.write_text("A  *  notacolor\nB  *\n")
        self.assertEqual(
            shw.load_category_rules(self.categories),
            [("A", "*", None), ("B", "*", None)],
        )

    def test_find_host_fallback(self):
        h = shw.find_host("nope.example")
        self.assertEqual(h.name, "nope.example")
        self.assertEqual(h.target, "nope.example")
        self.assertIsNone(h.user)


class OptionsTest(TempPathsTestCase):
    def test_defaults_without_file(self):
        opts = dict(shw.load_options())
        self.assertEqual(opts["no-pubkey"], ("PubkeyAuthentication=no",))
        self.assertEqual(opts["comp"], ("Compression=yes", "Compression=auto"))

    def test_file_replaces_defaults(self):
        self.options_file.write_text(
            "# comment\n"
            "x11  ForwardX11=yes\n"
            "alive  ServerAliveInterval=60|120\n"
            "badline\n"
            "x11  Duplicate=no\n"
        )
        self.assertEqual(
            dict(shw.load_options()),
            {
                "x11": ("ForwardX11=yes",),
                "alive": ("ServerAliveInterval=60", "ServerAliveInterval=120"),
            },
        )

    def test_empty_file_means_no_options(self):
        self.options_file.write_text("# only a comment\n")
        self.assertEqual(shw.load_options(), [])

    def test_state_roundtrip_and_corrupt(self):
        shw.save_option_state({"x11": "ForwardX11=yes"})
        self.assertEqual(shw.load_option_state(), {"x11": "ForwardX11=yes"})
        self.option_state.write_text("garbage")
        self.assertEqual(shw.load_option_state(), {})


class SshConfigPathTest(unittest.TestCase):
    def test_env_var_wins(self):
        with mock.patch.dict(os.environ, {"SSH_CONFIG": "/tmp/xyz"}):
            self.assertEqual(shw._ssh_config_path(), Path("/tmp/xyz"))

    def test_default_when_unset(self):
        env = dict(os.environ)
        env.pop("SSH_CONFIG", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(shw._ssh_config_path(), Path.home() / ".ssh" / "config")


class DemoTest(TempPathsTestCase):
    def test_start_and_stop_roundtrip(self):
        root = shw.start_demo()
        self.addCleanup(shw.stop_demo)
        self.assertTrue(shw.DEMO)
        self.assertTrue(shw.SSH_CONFIG.is_file())
        self.assertIn("web1.example.com", shw.SSH_CONFIG.read_text())
        for f in (
            shw.CATEGORIES_FILE,
            shw.FAVORITES_FILE,
            shw.USAGE_FILE,
            shw.EXTRA_HOSTS_FILE,
        ):
            self.assertTrue(f.is_file())
        shw.stop_demo()
        self.assertFalse(shw.DEMO)
        self.assertFalse(root.exists())
        self.assertEqual(shw.SSH_CONFIG, self.ssh_config)

    def test_demo_list_uses_fake_data(self):
        self.ssh_config.write_text("Host realmarker\n  User zed\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main("--demo", "-l")
        text = out.getvalue()
        self.assertIn("web1.example.com", text)
        self.assertIn("alice@web1.example.com", text)
        self.assertIn("bob@web1.example.com", text)  # multi-user entry
        self.assertIn("buildbox", text)  # extra host
        self.assertIn("TAILSCALE", text)  # 100.64.9.9 auto-categorized
        self.assertNotIn("realmarker", text)  # real config untouched


class DemoTuiTest(TempPathsTestCase, unittest.IsolatedAsyncioTestCase):
    async def test_demo_connect_shows_command_not_ssh(self):
        shw.start_demo()
        self.addCleanup(shw.stop_demo)
        app = shw.SSHWiz()
        with (
            mock.patch.object(shw.subprocess, "run") as run,
            mock.patch.object(shw.SSHWiz, "notify") as notify,
        ):
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.press("enter")
            run.assert_not_called()  # no real ssh in demo mode
        self.assertTrue(any("would run: ssh" in str(c) for c in notify.call_args))


class CliTest(TempPathsTestCase):
    def test_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main("-h")
        self.assertIn("ssh-wiz", out.getvalue())

    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main("--version")
        self.assertEqual(out.getvalue().strip(), f"ssh-wiz {shw.__version__}")

    def test_list(self):
        self.ssh_config.write_text("Host foo\n  User alice\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main("-l")
        self.assertIn("foo", out.getvalue())
        self.assertIn("alice@foo", out.getvalue())

    def test_f_requires_name(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main("-f")
        self.assertIn("requires a host name", str(cm.exception.code))

    def test_unknown_option(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main("--nope", "host")
        self.assertIn("unknown option", str(cm.exception.code))

    def test_tmux_with_remote_command_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main("-t", "host", "ls")
        self.assertIn("cannot combine", str(cm.exception.code))

    def test_cli_tmux_flag_requires_tmux(self):
        self.ssh_config.write_text("Host foo\n  User alice\n")
        with (
            mock.patch.object(shw, "tmux_available", return_value=False),
            self.assertRaises(SystemExit) as cm,
        ):
            self.run_main("-t", "foo")
        self.assertIn("tmux is not installed", str(cm.exception.code))

    def test_password_flag_enables_option(self):
        self.ssh_config.write_text("Host foo\n  User alice\n")
        with mock.patch.object(shw.os, "execvp") as execvp:
            self.run_main("-p", "foo")
        args = execvp.call_args[0][1]
        self.assertEqual(args[args.index("-o") + 1], "PubkeyAuthentication=no")

    def test_cli_uses_saved_option_state(self):
        self.ssh_config.write_text("Host foo\n  User alice\n")
        shw.save_option_state({"x11": "ForwardX11=yes"})
        with mock.patch.object(shw.os, "execvp") as execvp:
            self.run_main("foo")
        args = execvp.call_args[0][1]
        self.assertEqual(args[args.index("-o") + 1], "ForwardX11=yes")


class LazyTuiTest(unittest.TestCase):
    def test_cli_path_does_not_import_textual(self):
        # textual must only be imported when the TUI actually starts: a
        # CLI invocation (e.g. shell completion) must not pay for it
        code = (
            "import sys, runpy\n"
            "sys.argv = ['ssh-wiz', '--version']\n"
            f"runpy.run_path({str(HERE / 'ssh-wiz')!r}, run_name='__main__')\n"
            "assert 'textual' not in sys.modules\n"
        )
        # check=False on purpose: the test asserts on r.returncode/r.stderr
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(r.returncode, 0, r.stderr)


class CompletionTest(TempPathsTestCase):
    def setUp(self):
        super().setUp()
        self.ssh_config.write_text("Host ryzen9\n  User alice\nHost web1\n  User bob\n")
        self.extra_hosts.write_text("ex  carol@example.org\n")

    def test_all_hosts_in_config_order(self):
        self.assertEqual(shw.complete_candidates(""), ["ryzen9", "web1", "ex"])

    def test_multi_user_entries(self):
        self.ssh_config.write_text(
            "Host ryzen9\n  User llm\nHost ryzen9\n  User root\n"
        )
        self.assertEqual(
            shw.complete_candidates(""),
            ["ryzen9", "ryzen9 (root)", "ex"],
        )
        self.assertEqual(shw.complete_candidates("root"), ["ryzen9 (root)"])

    def test_fuzzy_match(self):
        self.assertEqual(shw.complete_candidates("rzn9"), ["ryzen9"])
        self.assertEqual(shw.complete_candidates("WEB"), ["web1"])
        self.assertEqual(shw.complete_candidates("zzz"), [])

    def test_flags_prefix_match(self):
        self.assertEqual(shw.complete_candidates("-l"), ["-l"])
        self.assertEqual(shw.complete_candidates("--l"), ["--list"])
        self.assertEqual(shw.complete_candidates("--c"), ["--complete", "--completion"])
        self.assertEqual(shw.complete_candidates("-z"), [])
        self.assertEqual(shw.complete_candidates("-"), list(shw.CLI_FLAGS))

    def test_complete_pairs(self):
        # only real user@host couples, in config order
        self.assertEqual(shw.complete_pairs("alice", ""), ["alice@ryzen9"])
        self.assertEqual(shw.complete_pairs("bob", ""), ["bob@web1"])
        self.assertEqual(shw.complete_pairs("carol", ""), ["carol@example.org"])

    def test_complete_pairs_prefix(self):
        self.assertEqual(shw.complete_pairs("alice", "ryz"), ["alice@ryzen9"])
        self.assertEqual(shw.complete_pairs("alice", "web"), [])

    def test_complete_user_hosts_all(self):
        self.assertEqual(
            shw.complete_user_hosts(""),
            ["alice@ryzen9", "bob@web1", "carol@example.org"],
        )

    def test_complete_user_hosts_prefix(self):
        self.assertEqual(shw.complete_user_hosts("ryz"), ["alice@ryzen9"])


class CompletionCliTest(TempPathsTestCase):
    def test_complete(self):
        self.ssh_config.write_text("Host ryzen9\n  User alice\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main("--complete", "rzn9")
        self.assertEqual(out.getvalue().splitlines(), ["ryzen9"])

    def test_complete_no_prefix(self):
        self.ssh_config.write_text("Host a\n  User x\nHost b\n  User y\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main("--complete")
        self.assertEqual(out.getvalue().splitlines(), ["a", "b"])

    def test_complete_users(self):
        self.ssh_config.write_text("Host ryzen9\n  User alice\nHost web1\n  User bob\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main("--complete-hosts")
        self.assertEqual(out.getvalue().splitlines(), ["alice@ryzen9", "bob@web1"])

    def test_complete_hosts_prefix(self):
        self.ssh_config.write_text("Host ryzen9\n  User alice\nHost web1\n  User bob\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main("--complete-hosts", "ryz")
        self.assertEqual(out.getvalue().splitlines(), ["alice@ryzen9"])

    def test_completion_scripts(self):
        markers = {
            "bash": "complete -o default -F __ssh_wiz ssh-wiz",
            "zsh": "compdef __ssh_wiz ssh-wiz",
            "fish": "complete -c ssh-wiz",
        }
        for shell, marker in markers.items():
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.run_main("--completion", shell)
            self.assertIn(marker, out.getvalue(), shell)

    def test_completion_unknown_shell(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_main("--completion", "tcsh")
        self.assertIn("unknown completion shell", str(cm.exception.code))

    def test_scripts_parse(self):
        """Generated scripts must be syntactically valid (skip missing shells)."""
        for shell, cmd in (
            ("bash", ["bash", "-n"]),
            ("zsh", ["zsh", "-n"]),
            ("fish", ["fish", "-n"]),
        ):
            if not shutil.which(cmd[0]):
                continue
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.run_main("--completion", shell)
            subprocess.run(
                cmd, input=out.getvalue(), text=True, check=True, capture_output=True
            )


class BuildTransferCmdTest(unittest.TestCase):
    def make_host(self, **kw):
        base = {
            "name": "foo",
            "display": "alice@foo",
            "target": "alice@foo",
            "user": "alice",
            "host": "foo",
        }
        base.update(kw)
        return shw.Host(**base)

    def test_upload_default(self):
        h = self.make_host()
        cmd = shw.build_transfer_cmd(h, [], "up", ["/tmp/a.txt"], "~/")
        self.assertEqual(cmd[0], "rsync")
        for flag in ("-a", "-z", "-h", "-i"):
            self.assertIn(flag, cmd)
        # -e shell is ssh (no port/identity here; ControlMaster opts aside)
        e_i = cmd.index("-e")
        self.assertTrue(cmd[e_i + 1].startswith("ssh "))
        self.assertIn("/tmp/a.txt", cmd)
        self.assertIn("alice@foo:~/", cmd[-1])

    def test_upload_embeds_port_identity_and_options(self):
        h = self.make_host(
            cmd_port="8022", identityfile="~/.ssh/id_x", extra="-o Foo=bar"
        )
        cmd = shw.build_transfer_cmd(
            h, ["PubkeyAuthentication=no"], "up", ["/tmp/a"], "~/"
        )
        shell = cmd[cmd.index("-e") + 1]
        self.assertIn("-p 8022", shell)  # ssh uses lowercase -p
        self.assertIn("-i", shell)
        self.assertIn("id_x", shell)
        self.assertIn("Foo=bar", shell)
        self.assertIn("PubkeyAuthentication=no", shell)

    def test_dest_trailing_slash_drops_into_folder(self):
        h = self.make_host()
        # file or dir: dest always gets a trailing slash (drop into the folder)
        for src in ("/tmp/file.txt", "/tmp/dir"):
            up = shw.build_transfer_cmd(h, [], "up", [src], "/remote")
            self.assertTrue(up[-1].endswith("/remote/"))
            self.assertIn(src, up)  # source keeps no trailing slash

    def test_shell_reuses_one_ssh_connection(self):
        # ControlMaster multiplexes listing/probe/delete/transfer over one
        # socket; ControlPersist keeps it warm between operations. The
        # ControlPath dir is resolved literally (some ssh builds reject %t)
        # and must point at a real, writable directory.
        shell = shw._rsync_ssh_shell(self.make_host())
        self.assertIn("-o ControlMaster=auto", shell)
        self.assertIn("ControlPath=", shell)
        self.assertIn("ControlPersist=60", shell)
        cp = shell.split("ControlPath=")[1].split()[0]
        self.assertTrue(cp.endswith("/ssh-wiz-%C"))
        self.assertTrue(os.path.isdir(os.path.dirname(cp)))

    def test_shell_without_tempdir_omits_controlmaster(self):
        # No usable temp dir: fall back to one connection per operation
        # instead of failing with a broken ControlPath
        with mock.patch.object(shw, "_control_path", return_value=None):
            shell = shw._rsync_ssh_shell(self.make_host())
        self.assertNotIn("ControlMaster", shell)
        self.assertNotIn("ControlPath", shell)

    def test_download_direction_swaps_operands(self):
        h = self.make_host()
        cmd = shw.build_transfer_cmd(h, [], "down", ["data/file.txt"], "/tmp/out")
        self.assertIn("alice@foo:data/file.txt", cmd)
        self.assertTrue(cmd[-1].endswith("/tmp/out/"))
        # remote source comes before local dest
        self.assertLess(cmd.index("alice@foo:data/file.txt"), cmd.index("/tmp/out/"))


class FileSyncParserTest(unittest.TestCase):
    def test_parse_rsync_listing(self):
        out = (
            "drwxr-xr-x            120 2026/10/06 17:26:43 .\n"
            "-rw-r--r--              0 2026/10/06 17:26:41 a.txt\n"
            "-rw-r--r--              7 2026/10/06 17:26:41 b c.txt\n"
            "drwxr-xr-x             40 2026/10/06 17:26:41 sub\n"
        )
        entries = shw.parse_rsync_listing(out)
        self.assertEqual([e.name for e in entries], ["sub", "a.txt", "b c.txt"])
        # dirs first, then A->Z; sizes parsed; spaces in names preserved
        self.assertTrue(entries[0].is_dir)
        self.assertEqual(entries[1].size, 0)
        self.assertEqual(entries[2].size, 7)

    def test_parse_rsync_listing_ignores_dot_entries(self):
        entries = shw.parse_rsync_listing("drwxr-xr-x 1 .\n-rw-r--r-- 2 ../\n")
        self.assertEqual([e.name for e in entries], [])

    def test_parse_itemize(self):
        # real rsync --itemize-changes lines carry a leading status char
        # (``+`` for a plain add, ``c`` for a directory processed by a child)
        # and the file *type* is the second char (f/d/=).
        self.assertEqual(shw.parse_itemize("+f+++++++++ a.txt"), ("f", "a.txt"))
        self.assertEqual(shw.parse_itemize("cd+++++++++ sub/"), ("d", "sub"))
        self.assertEqual(shw.parse_itemize("+f+++++++++ ./x"), ("f", "x"))
        self.assertEqual(shw.parse_itemize("cd+++++++++ ./"), None)  # root
        self.assertIsNone(shw.parse_itemize("not a valid line"))

    def test_list_local_dir_dirs_first_then_alpha(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "b.txt").write_text("x")
            Path(d, "a.txt").write_text("y")
            Path(d, "sub").mkdir()
            entries = shw.list_local_dir(Path(d))
            self.assertTrue(entries[0].is_dir)  # sub first
            self.assertEqual([e.name for e in entries[1:]], ["a.txt", "b.txt"])

    def test_fmt_size(self):
        self.assertEqual(shw.fmt_size(512), "512B")
        self.assertEqual(shw.fmt_size(1024), "1K")
        self.assertEqual(shw.fmt_size(1536), "2K")

    def test_parse_progress(self):
        # (cumulative bytes, pct, speed B/s, xfr-done); speed is rsync's own
        # rate; xfr is None when the sample has no (xfr#N, ...) suffix
        b, pct, speed, xfr = shw.parse_progress(
            "     41,943,040  99%    1.26GB/s    0:00:00 (xfr#1, to-chk=1/3)"
        )
        self.assertEqual((b, pct, xfr), (41943040, 99, 1))
        self.assertAlmostEqual(speed, 1.26e9, delta=1)
        # de_DE-style grouping and decimal separators
        b, pct, speed, xfr = shw.parse_progress(
            "     42.621.440  53%    1,26GB/s    0:00:04 (xfr#1, to-chk=1/2)"
        )
        self.assertEqual((b, pct, xfr), (42621440, 53, 1))
        self.assertAlmostEqual(speed, 1.26e9, delta=1)
        # -h human-readable size (SI units)
        b, pct, speed, xfr = shw.parse_progress(
            "     41.94M  99%    1.15GB/s    0:00:00 (xfr#1, to-chk=1/3)"
        )
        self.assertEqual((b, pct, xfr), (41940000, 99, 1))
        # no xfr# suffix (older rsync), zero speed
        b, pct, speed, xfr = shw.parse_progress(
            "     32,768   0%    0.00kB/s    0:00:00"
        )
        self.assertEqual((b, pct, speed, xfr), (32768, 0, 0.0, None))
        self.assertIsNone(shw.parse_progress(">f+++++++++ a.bin"))
        self.assertIsNone(shw.parse_progress(""))

    def test_fmt_speed(self):
        self.assertEqual(shw.fmt_speed(120), "120B/s")
        self.assertEqual(shw.fmt_speed(850000), "850.0kB/s")
        self.assertEqual(shw.fmt_speed(1.26e9), "1.3GB/s")


class _FakeRsyncProc:
    """Stand-in for an asyncio subprocess: yields itemize lines on stdout.
    `raw_stdout` feeds the whole byte string in a single read() (for
    --info=progress2 streams with \r updates)."""

    def __init__(self, itemize_lines=(), returncode=0, stderr="", raw_stdout=None):
        if raw_stdout is not None:
            self._chunks = [raw_stdout]
        else:
            self._chunks = [l.encode() + b"\n" for l in itemize_lines]
        self._it = iter(self._chunks)
        self.returncode = returncode
        self._stderr = stderr.encode()
        self.stderr = _FakeStream(self._stderr)

    async def read(self, n=-1):
        try:
            return next(self._it)
        except StopIteration:
            return b""

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    def __aiter__(self):
        return self

    @property
    def stdout(self):
        return self

    async def wait(self):
        return self.returncode

    async def communicate(self):
        return b"", self._stderr

    def kill(self):
        pass


class _StallRsyncProc:
    """rsync stand-in that stalls on stdout until killed (quit-confirm test)."""

    def __init__(self):
        self.returncode = None
        self._killed = asyncio.Event()
        self.stderr = _FakeStream(b"")

    @property
    def stdout(self):
        return self

    async def read(self, n=-1):
        await self._killed.wait()
        return b""

    async def wait(self):
        return -9

    def kill(self):
        self._killed.set()


class _FakeStream:
    """Stand-in for a subprocess pipe: returns buffered bytes on read()."""

    def __init__(self, data: bytes = b""):
        self._data = data

    async def read(self):
        return self._data


class FileSyncTuiTest(TempPathsTestCase, unittest.IsolatedAsyncioTestCase):
    def write_hosts(self):
        self.ssh_config.write_text("Host foo\n  User alice\n")

    async def test_open_and_switch_panels(self):
        self.write_hosts()
        app = shw.SSHWiz()
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                self.assertIsInstance(app.screen, shw.FileSyncScreen)
                local = app.screen.query_one("#local-list", shw.FileList)
                remote = app.screen.query_one("#remote-list", shw.FileList)
                self.assertTrue(local.active)
                await pilot.press("tab")
                self.assertTrue(remote.active)
                await pilot.press("tab")
                self.assertTrue(local.active)

    async def test_space_toggles_selection_and_counts(self):
        self.write_hosts()
        app = shw.SSHWiz()
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = [shw.FileEntry(name="r.txt", path="/tmp/r.txt")]
                await pilot.press("space")
                self.assertTrue(local.entries[0].selected)
                bar = str(app.screen.query_one("#local-path", shw.Static).render())
                self.assertIn("1 selected", bar)
                await pilot.press("space")
                self.assertFalse(local.entries[0].selected)

    async def test_space_toggles_visible_row_not_hidden_entry(self):
        # The cursor indexes the *visible* rows: with hidden entries present
        # (the default in any real directory) space must toggle the row shown
        # on screen, not the invisible entries[cursor].
        self.write_hosts()
        app = shw.SSHWiz()
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = [
                    shw.FileEntry(name=".hidden", path="/tmp/.hidden"),
                    shw.FileEntry(name="visible.txt", path="/tmp/visible.txt"),
                ]
                self.assertEqual([e.name for e in local.filtered], ["visible.txt"])
                await pilot.press("space")
                self.assertTrue(local.entries[1].selected)  # visible.txt
                self.assertFalse(local.entries[0].selected)  # .hidden untouched
                bar = str(app.screen.query_one("#local-path", shw.Static).render())
                self.assertIn("1 selected", bar)
                # the selection must be *visible*: ☑ on the shown row
                self.assertIn("☑", str(local.render()))

    async def test_f8_deletes_visible_row_not_hidden_entry(self):
        # F8 must delete the file under the cursor, never the hidden entry
        # that shares its index in entries.
        self.write_hosts()
        app = shw.SSHWiz()
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir)
        victim = Path(workdir) / "trash.txt"
        victim.write_text("bye")
        protected = Path(workdir) / ".keep"
        protected.write_text("keep me")
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = shw.list_local_dir(workdir)
                self.assertEqual(local.filtered[0].name, "trash.txt")
                await pilot.press("f8")
                await pilot.pause()
                self.assertIsInstance(app.screen, shw.ConfirmScreen)
                await pilot.press("y")
                await pilot.pause()
                status = str(app.screen.query_one("#sync-status", shw.Static).render())
        self.assertFalse(victim.exists())
        self.assertTrue(protected.exists())
        self.assertIn("deleted trash.txt", status)

    async def test_cursor_clamps_to_visible_rows(self):
        # With only one visible row, down/end must not run past it into the
        # hidden entries.
        self.write_hosts()
        app = shw.SSHWiz()
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = [
                    shw.FileEntry(name=".a", path="/x/.a"),
                    shw.FileEntry(name=".b", path="/x/.b"),
                    shw.FileEntry(name="c.txt", path="/x/c.txt"),
                ]
                await pilot.press("down")
                await pilot.press("down")
                await pilot.press("end")
                self.assertEqual(local.cursor, 0)
                self.assertEqual(local.entry_at_cursor().name, "c.txt")

    async def test_copy_runs_rsync_with_correct_command(self):
        self.write_hosts()
        app = shw.SSHWiz()
        calls = []

        async def fake_exec(*cmd, **kw):
            calls.append(cmd)
            return _FakeRsyncProc(["f+++++++++ r.txt", "cd+++++++++ sub/"])

        with (
            mock.patch.object(
                shw.asyncio,
                "create_subprocess_exec",
                new=mock.AsyncMock(side_effect=fake_exec),
            ),
            mock.patch.object(shw, "parse_rsync_listing", return_value=[]),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = [
                    shw.FileEntry(name="r.txt", path="/tmp/r.txt", selected=True)
                ]
                await pilot.press("f5")
                await pilot.pause()
                await pilot.pause()
        transfer = [
            c for c in calls if c and c[0] == "rsync" and "--list-only" not in c
        ]
        self.assertTrue(transfer, "expected an rsync transfer command")
        cmd = transfer[0]
        self.assertIn("/tmp/r.txt", cmd)
        self.assertIn("foo:~/", cmd[-1])

    async def test_demo_copy_previews_not_runs(self):
        shw.start_demo()
        self.addCleanup(shw.stop_demo)
        app = shw.SSHWiz()
        with (
            mock.patch.object(shw.subprocess, "run") as run,
            mock.patch.object(
                shw.asyncio,
                "create_subprocess_exec",
                new=mock.AsyncMock(return_value=_FakeRsyncProc(stderr="no rsync")),
            ),
            mock.patch.object(shw.SSHWiz, "notify") as notify,
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = [
                    shw.FileEntry(name="r.txt", path="/tmp/r.txt", selected=True)
                ]
                await pilot.press("f5")
                await pilot.pause()
        run.assert_not_called()  # demo never runs a real command
        self.assertTrue(any("would run" in str(c) for c in notify.call_args))

    async def test_f8_deletes_local_file(self):
        self.write_hosts()
        app = shw.SSHWiz()
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir)
        victim = Path(workdir) / "trash.txt"
        victim.write_text("bye")
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = shw.list_local_dir(workdir)
                await pilot.press("f8")
                await pilot.pause()
                self.assertIsInstance(app.screen, shw.ConfirmScreen)
                await pilot.press("y")
                await pilot.pause()
                status = str(app.screen.query_one("#sync-status", shw.Static).render())
        self.assertFalse(victim.exists())
        self.assertIn("deleted trash.txt", status)

    async def test_f8_asks_confirmation_before_deleting(self):
        # F8 must never delete without a yes/no prompt: 'n' (or esc) leaves
        # the file in place and returns to the sync screen.
        self.write_hosts()
        app = shw.SSHWiz()
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir)
        victim = Path(workdir) / "trash.txt"
        victim.write_text("bye")
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = shw.list_local_dir(workdir)
                # first press: the prompt appears and 'n' cancels
                await pilot.press("f8")
                await pilot.pause()
                self.assertIsInstance(app.screen, shw.ConfirmScreen)
                msg = (
                    app.screen.query_one("#confirm-box", shw.Container)
                    .query_one("Static", shw.Static)
                    .render()
                )
                self.assertIn("trash.txt", str(msg))
                await pilot.press("n")
                await pilot.pause()
                self.assertIsInstance(app.screen, shw.FileSyncScreen)
                self.assertTrue(victim.exists(), "'n' must not delete")
                # second press: 'y' confirms and deletes
                await pilot.press("f8")
                await pilot.pause()
                self.assertIsInstance(app.screen, shw.ConfirmScreen)
                await pilot.press("y")
                await pilot.pause()
        self.assertFalse(victim.exists())

    def _remote_roundtrip(self, cmd: list, home: str, host: str = "foo"):
        """Simulate the ssh round-trip for a delete command: the client joins
        the remote words with plain spaces, the remote login shell parses the
        result with -c. Runs it for real against a temp dir as $HOME.
        `host` locates the remote command (ssh options precede it)."""
        remote_cmd = " ".join(
            cmd[cmd.index(host) + 1 :]
        )  # ssh [opts] <host> <remote words...>
        # check=False on purpose: the tests assert on r.returncode/r.stderr
        return subprocess.run(
            ["sh", "-c", remote_cmd],
            env={**os.environ, "HOME": home},
            capture_output=True,
            text=True,
            check=False,
        )

    async def test_f8_deletes_remote_file_via_ssh(self):
        # The delete must survive the real ssh round-trip: the client joins
        # the remote words with plain spaces and the remote login shell
        # re-parses them, so the path must be quoted *inside* the command
        # string. (The old `sh -c '...' rm $path` argv form lost its quoting
        # in the join and ran a bare `rm` remotely: "missing operand".)
        self.write_hosts()
        app = shw.SSHWiz()
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir)
        victim = Path(workdir) / "old.txt"
        victim.write_text("bye")
        calls = []

        async def fake_exec(*cmd, **kw):
            calls.append(cmd)
            return _FakeRsyncProc()

        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(side_effect=fake_exec),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                await pilot.press("tab")
                await pilot.pause()  # let the remote-listing worker settle
                remote = app.screen.query_one("#remote-list", shw.FileList)
                remote.entries = [shw.FileEntry(name="old.txt", path="old.txt")]
                await pilot.press("f8")
                await pilot.pause()
                self.assertIsInstance(app.screen, shw.ConfirmScreen)
                await pilot.press("y")
                await pilot.pause()
        ssh_calls = [c for c in calls if c and c[0] == "ssh" and "--version" not in c]
        self.assertTrue(ssh_calls, "expected an ssh delete command")
        cmd = ssh_calls[0]
        # a leading ~ must be sent as $HOME so the remote shell expands it
        remote_cmd = " ".join(cmd[cmd.index("foo") + 1 :])
        self.assertIn("$HOME", remote_cmd)
        r = self._remote_roundtrip(cmd, workdir)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(victim.exists())

    async def test_f8_remote_delete_quotes_tricky_names(self):
        # Spaces, single quotes and glob chars in the name must arrive
        # literally on the remote (no word-splitting, no globbing, no
        # injection) after the ssh space-join round-trip.
        self.write_hosts()
        app = shw.SSHWiz()
        workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, workdir)
        victims = [Path(workdir) / n for n in ("my file.txt", "we'ird*.txt", "a b")]
        for v in victims:
            v.write_text("bye")
        decoy = Path(workdir) / "keep.txt"  # must not match the glob
        decoy.write_text("keep")
        calls = []

        async def fake_exec(*cmd, **kw):
            calls.append(cmd)
            return _FakeRsyncProc()

        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(side_effect=fake_exec),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                await pilot.press("tab")
                await pilot.pause()
                remote = app.screen.query_one("#remote-list", shw.FileList)
                # each successful delete re-lists the dir (empty here), so
                # re-seed the entry before every press
                for name in ("my file.txt", "we'ird*.txt", "a b"):
                    remote.entries = [shw.FileEntry(name=name, path=name)]
                    await pilot.press("f8")
                    await pilot.pause()
                    self.assertIsInstance(app.screen, shw.ConfirmScreen)
                    await pilot.press("y")
                    await pilot.pause()

        ssh_calls = [c for c in calls if c and c[0] == "ssh" and "--version" not in c]
        self.assertEqual(len(ssh_calls), 3)
        for cmd in ssh_calls:
            r = self._remote_roundtrip(cmd, workdir)
            self.assertEqual(r.returncode, 0, r.stderr)
        for v in victims:
            self.assertFalse(v.exists(), f"{v.name} should be deleted")
        self.assertTrue(decoy.exists(), "glob must stay literal")

    async def test_f8_refuses_root_or_empty_remote_path(self):
        # The target must never be empty or `/`: a missing/empty name must
        # not become `rm -rf -- /` (or `~/`) on the remote host.
        self.write_hosts()
        app = shw.SSHWiz()
        calls = []

        async def fake_exec(*cmd, **kw):
            calls.append(cmd)
            return _FakeRsyncProc()

        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(side_effect=fake_exec),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                await pilot.press("tab")  # active panel = remote
                await pilot.pause()
                remote = app.screen.query_one("#remote-list", shw.FileList)
                for remote_path in ("/", "~"):
                    remote.entries = [shw.FileEntry(name="", path=remote_path)]
                    app.screen.remote_path = remote_path
                    await pilot.press("f8")
                    await pilot.pause()
                ssh_calls = [
                    c for c in calls if c and c[0] == "ssh" and "--version" not in c
                ]
                self.assertEqual(
                    ssh_calls, [], "no ssh command may be sent for root/empty paths"
                )
                status = str(app.screen.query_one("#sync-status", shw.Static).render())
                self.assertIn("cannot delete", status)

    async def test_help_bar_lists_f8_delete(self):
        self.write_hosts()
        app = shw.SSHWiz()
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                helpbar = str(app.screen.query_one("#sync-help", shw.Static).render())
                self.assertIn("F8", helpbar)
                self.assertIn("delete", helpbar)
                # a vertical separator sits between the two columns
                order = [
                    w.id for w in app.screen.query_one("#sync-root").walk_children()
                ]
                self.assertLess(order.index("sync-sep"), order.index("right-col"))
                self.assertGreater(order.index("sync-sep"), order.index("local-list"))
                # the remote path bar shows the host as user@host
                remote_bar = str(
                    app.screen.query_one("#remote-path", shw.Static).render()
                )
                self.assertIn("alice@foo", remote_bar)

    async def test_selection_has_a_visible_highlight(self):
        self.write_hosts()
        app = shw.SSHWiz()
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = [
                    shw.FileEntry(name="keep.txt", path="/tmp/keep.txt", selected=True),
                    shw.FileEntry(name="drop.txt", path="/tmp/drop.txt"),
                ]
                local.cursor = 0  # cursor on the *selected* row
                local.refresh()
                rendered = local.render()

                def name_style_for(label):
                    idx = rendered.plain.index(label)
                    for span in rendered.spans:
                        if span.start <= idx < span.end:
                            return str(span.style)
                    return ""

                # the cursor row gets a *subtle* blue background so you always
                # know where you are (not the harsh white of reverse)
                self.assertIn("26405b", name_style_for("keep.txt"))
                # selected files are indicated only by the icon: the
                # non-cursor row (drop.txt) is not row-highlighted at all
                self.assertNotIn("26405b", name_style_for("drop.txt"))
                self.assertNotIn("reverse", name_style_for("drop.txt"))

    async def test_hidden_files_hidden_by_default_and_toggled(self):
        self.write_hosts()
        app = shw.SSHWiz()
        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=_FakeRsyncProc()),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                remote = app.screen.query_one("#remote-list", shw.FileList)
                local.entries = [
                    shw.FileEntry(name="visible.txt", path="/tmp/visible.txt"),
                    shw.FileEntry(name=".secret", path="/tmp/.secret"),
                    shw.FileEntry(name=".git", path="/tmp/.git", is_dir=True),
                ]
                remote.entries = list(local.entries)
                # hidden entries are excluded from the visible rows by default
                self.assertEqual({e.name for e in local.filtered}, {"visible.txt"})
                rendered = str(local.render())
                self.assertIn("visible.txt", rendered)
                self.assertNotIn(".secret", rendered)
                # pressing h reveals them in BOTH panels at once
                await pilot.press("h")
                self.assertTrue(local.show_hidden)
                self.assertTrue(remote.show_hidden)
                self.assertEqual(
                    {e.name for e in local.filtered}, {".git", ".secret", "visible.txt"}
                )
                rendered = str(local.render())
                self.assertIn(".secret", rendered)
                self.assertIn(".git", rendered)
                # the status line and the local path bar reflect the state
                status = str(app.screen.query_one("#sync-status", shw.Static).render())
                self.assertIn("hidden files shown", status)
                local_bar = str(
                    app.screen.query_one("#local-path", shw.Static).render()
                )
                self.assertIn("(hidden)", local_bar)
                # pressing h again hides them once more
                await pilot.press("h")
                self.assertFalse(local.show_hidden)
                self.assertEqual({e.name for e in local.filtered}, {"visible.txt"})
                status = str(app.screen.query_one("#sync-status", shw.Static).render())
                self.assertIn("hidden files hidden", status)
                local_bar = str(
                    app.screen.query_one("#local-path", shw.Static).render()
                )
                self.assertNotIn("(hidden)", local_bar)

    async def test_progress_flag_gated_on_rsync_version(self):
        # --info=progress2 needs rsync >= 3.1 on BOTH ends; an older (or
        # unknown) remote would reject the flag, so the command must omit it
        self.write_hosts()
        for remote_ver, expect in (
            ((3, 2, 0), True),
            ((3, 0, 9), False),
            (None, False),
        ):
            app = shw.SSHWiz()
            calls = []

            async def fake_exec(*cmd, calls=calls, **kw):
                calls.append(cmd)
                return _FakeRsyncProc()

            async def no_probe(self):
                pass

            with (
                mock.patch.object(
                    shw.asyncio,
                    "create_subprocess_exec",
                    new=mock.AsyncMock(side_effect=fake_exec),
                ),
                mock.patch.object(
                    shw,
                    "local_rsync_version",
                    return_value=(3, 2, 0),
                ),
                mock.patch.object(shw.FileSyncScreen, "_probe_remote_rsync", no_probe),
            ):
                async with app.run_test(size=(120, 30)) as pilot:
                    await pilot.press("ctrl+s")
                    app.screen.remote_rsync = remote_ver
                    local = app.screen.query_one("#local-list", shw.FileList)
                    local.entries = [
                        shw.FileEntry(name="r.txt", path="/tmp/r.txt", selected=True)
                    ]
                    await pilot.press("f5")
                    await pilot.pause()
            transfer = [
                c for c in calls if c and c[0] == "rsync" and "--list-only" not in c
            ]
            self.assertTrue(transfer)
            self.assertEqual("--info=progress2" in transfer[0], expect)

    async def test_transfer_parses_progress2_stream(self):
        # progress2 output is \r-separated and interleaved with -i lines;
        # the transfer must parse cumulative bytes/percent from it
        self.write_hosts()
        app = shw.SSHWiz()
        progress_out = (
            ">f+++++++++ a.bin\n"
            "     10.00M  50%    1.00MB/s    0:00:01 (xfr#0, to-chk=1/2)\r"
            "     20.00M 100%    1.00MB/s    0:00:02 (xfr#2, to-chk=0/2)\n"
            ">f+++++++++ b.bin\n"
        )

        async def no_probe(self):
            pass

        with (
            mock.patch.object(
                shw.asyncio,
                "create_subprocess_exec",
                new=mock.AsyncMock(
                    return_value=_FakeRsyncProc(raw_stdout=progress_out.encode())
                ),
            ),
            mock.patch.object(
                shw,
                "local_rsync_version",
                return_value=(3, 2, 0),
            ),
            mock.patch.object(shw.FileSyncScreen, "_probe_remote_rsync", no_probe),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                app.screen.remote_rsync = (3, 2, 0)
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = [
                    shw.FileEntry(name="a.bin", path="/tmp/a.bin", selected=True),
                    shw.FileEntry(name="b.bin", path="/tmp/b.bin", selected=True),
                ]
                await pilot.press("f5")
                await pilot.pause()
                await pilot.pause()
                status = str(app.screen.query_one("#sync-status", shw.Static).render())
                self.assertIn("done", status)
                self.assertEqual(app.screen._xfer_bytes, 20000000)
                self.assertEqual(app.screen._pct, 100)
                self.assertAlmostEqual(app.screen._speed, 1.0e6, delta=1)

    async def test_status_capped_but_counts_complete(self):
        # Thousands of files: the status box keeps only the last
        # _MAX_STATUS_ITEMS lines (it is max-height 14), but the final
        # counts must cover every item and the oldest lines are dropped
        self.write_hosts()
        app = shw.SSHWiz()
        n = 120
        itemize = "".join(f">f+++++++++ f{i:03d}.bin\n" for i in range(n))

        async def no_probe(self):
            pass

        with (
            mock.patch.object(
                shw.asyncio,
                "create_subprocess_exec",
                new=mock.AsyncMock(
                    return_value=_FakeRsyncProc(raw_stdout=itemize.encode())
                ),
            ),
            # rsync < 3.1: no --info=progress2, itemize lines only
            mock.patch.object(shw, "local_rsync_version", return_value=(3, 0, 0)),
            mock.patch.object(shw.FileSyncScreen, "_probe_remote_rsync", no_probe),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                app.screen.remote_rsync = None
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = [
                    shw.FileEntry(
                        name=f"f{i:03d}.bin", path=f"/tmp/f{i:03d}.bin", selected=True
                    )
                    for i in range(n)
                ]
                await pilot.press("f5")
                await pilot.pause()
                await pilot.pause()
                status = str(app.screen.query_one("#sync-status", shw.Static).render())
        self.assertIn(f"done  {n} file(s)", status)
        self.assertIn("earlier item(s) not shown", status)
        self.assertIn("f119.bin", status)  # newest item still shown
        self.assertNotIn("f000.bin", status)  # oldest dropped from display

    async def test_esc_during_transfer_asks_confirmation(self):
        self.write_hosts()
        app = shw.SSHWiz()
        stall = _StallRsyncProc()

        async def fake_exec(*cmd, **kw):
            if cmd and cmd[0] == "rsync" and "--list-only" not in cmd:
                return stall
            return _FakeRsyncProc()

        with mock.patch.object(
            shw.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(side_effect=fake_exec),
        ):
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.press("ctrl+s")
                local = app.screen.query_one("#local-list", shw.FileList)
                local.entries = [
                    shw.FileEntry(name="r.txt", path="/tmp/r.txt", selected=True)
                ]
                await pilot.press("f5")
                await pilot.pause()
                self.assertTrue(app.screen.busy)
                # esc must not kill the transfer immediately: it asks first
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsInstance(app.screen, shw.ConfirmScreen)
                # 'n' cancels: back on the sync screen, transfer still running
                await pilot.press("n")
                await pilot.pause()
                self.assertIsInstance(app.screen, shw.FileSyncScreen)
                self.assertTrue(app.screen.busy)
                # 'y' kills the transfer and closes the screen
                await pilot.press("escape")
                await pilot.pause()
                self.assertIsInstance(app.screen, shw.ConfirmScreen)
                await pilot.press("y")
                await pilot.pause()
                self.assertNotIsInstance(app.screen, shw.FileSyncScreen)
                self.assertTrue(stall._killed.is_set())


if __name__ == "__main__":
    unittest.main()
