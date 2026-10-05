"""Tests for ssh-wiz. Run:  python3 test_ssh_wiz.py  (or: python3 -m unittest)"""

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
        self.favorites = root / "favorites"
        self.usage = root / "usage.json"
        self._saved = {a: getattr(shw, a) for a in _PATH_ATTRS}
        vars(shw).update(
            {
                "SSH_CONFIG": self.ssh_config,
                "EXTRA_HOSTS_FILE": self.extra_hosts,
                "CATEGORIES_FILE": self.categories,
                "FAVORITES_FILE": self.favorites,
                "USAGE_FILE": self.usage,
                "CONFIG_DIR": root,
                "STATE_DIR": root,
            }
        )

    def tearDown(self):
        for a, v in self._saved.items():
            setattr(shw, a, v)


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
        self.assertEqual(hosts["foo"]["user"], "alice")
        self.assertEqual(hosts["foo"]["port"], "2222")
        self.assertEqual(hosts["bar"]["user"], "carol")

    def test_tabs_and_match_blocks(self):
        self.write_config(
            "Match host *.example\n  Port 9999\n"
            "Host\tbaz\n\tUser tabby\n"
            "Host qux\n  User dave\n"
        )
        hosts, order = shw.parse_ssh_config(self.ssh_config)
        self.assertEqual(order, ["baz", "qux"])
        self.assertEqual(hosts["baz"]["user"], "tabby")
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
        self.assertEqual(hosts["foo"]["user"], "alice")
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
            self.assertEqual(lines[0], "─" * 80)
            self.assertTrue(lines[1].startswith("★ alpha"))
            self.assertEqual(lines[2], "─" * 80)
            self.assertTrue(lines[3].startswith(" LAN"))
            self.assertTrue(lines[4].startswith("  gamma"))
            self.assertTrue(lines[5].startswith(" WORK"))
            self.assertTrue(lines[6].startswith("  beta"))

    async def test_no_separator_without_favorites(self):
        self.write_hosts()
        app = shw.SSHWiz()
        async with app.run_test(size=(80, 24)):
            wl = app.query_one(shw.HostList)
            lines = str(wl.render()).splitlines()
            self.assertEqual(lines[0], "─" * 80)
            self.assertTrue(lines[1].startswith(" LAN"))
            self.assertTrue(lines[2].startswith("  alpha"))
            self.assertTrue(lines[3].startswith("  gamma"))
            self.assertTrue(lines[4].startswith(" WORK"))
            self.assertTrue(lines[5].startswith("  beta"))
            # only the top border, no separator below favorites
            self.assertEqual(sum(1 for l in lines if l == "─" * 80), 1)

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


class BuildSshCmdTest(unittest.TestCase):
    def make_host(self, **kw):
        base = {"name": "foo", "display": "alice@foo", "target": "foo"}
        base.update(kw)
        return shw.Host(**base)

    def test_full(self):
        h = self.make_host(cmd_port="8022", extra="-o Foo=bar")
        cmd = shw.build_ssh_cmd(h, password_mode=True, tmux_mode=True)
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


class MiscTest(TempPathsTestCase):
    def test_fuzzy_match(self):
        self.assertTrue(shw.fuzzy_match("rzn9", "ryzen9"))
        self.assertTrue(shw.fuzzy_match("ryzen9", "ryzen9"))
        self.assertFalse(shw.fuzzy_match("9znr", "ryzen9"))
        self.assertTrue(shw.fuzzy_match("", "anything"))

    def test_categorize_case_sensitive(self):
        rules = [("LAN", "10.0.1.*", None)]
        self.assertEqual(shw.categorize("10.0.1.5", rules), "LAN")
        self.assertEqual(shw.categorize("zzz", rules), shw.OTHER_CATEGORY)
        self.assertEqual(
            shw.categorize("lan", [("Lan", "LAN*", None)]), shw.OTHER_CATEGORY
        )

    def test_user_color_stable(self):
        self.assertEqual(shw.user_color("root"), shw.user_color("root"))
        self.assertIn(shw.user_color("root"), shw.USER_COLORS)

    def test_usage_score_decays(self):
        now = time.time()
        usage = {"a": [now - 10, now - shw.DECAY_SECONDS]}
        score = shw.usage_score(usage, "a", now)
        self.assertGreater(score, 1.0)
        self.assertLess(score, 2.0)

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


class SshConfigPathTest(unittest.TestCase):
    def test_env_var_wins(self):
        with mock.patch.dict(os.environ, {"SSH_CONFIG": "/tmp/xyz"}):
            self.assertEqual(shw._ssh_config_path(), Path("/tmp/xyz"))

    def test_default_when_unset(self):
        env = dict(os.environ)
        env.pop("SSH_CONFIG", None)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(shw._ssh_config_path(), Path.home() / ".ssh" / "config")


class CliTest(TempPathsTestCase):
    def run_main(self, *argv):
        old = sys.argv
        sys.argv = ["ssh-wiz", *argv]
        try:
            shw.main()
        finally:
            sys.argv = old

    def test_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_main("-h")
        self.assertIn("ssh-wiz", out.getvalue())

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


class CompletionTest(TempPathsTestCase):
    def setUp(self):
        super().setUp()
        self.ssh_config.write_text("Host ryzen9\n  User alice\nHost web1\n  User bob\n")
        self.extra_hosts.write_text("ex  carol@example.org\n")

    def test_all_hosts_in_config_order(self):
        self.assertEqual(shw.complete_candidates(""), ["ryzen9", "web1", "ex"])

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


class CompletionCliTest(TempPathsTestCase):
    def run_main(self, *argv):
        old = sys.argv
        sys.argv = ["ssh-wiz", *argv]
        try:
            shw.main()
        finally:
            sys.argv = old

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


if __name__ == "__main__":
    unittest.main()
