"""Tests for ssh-wiz. Run:  python3 test_ssh_wiz.py  (or: python3 -m unittest)"""
import contextlib
import importlib.machinery
import importlib.util
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_loader = importlib.machinery.SourceFileLoader("sshwiz", str(HERE / "ssh-wiz"))
_spec = importlib.util.spec_from_loader("sshwiz", _loader)
assert _spec is not None
shw = importlib.util.module_from_spec(_spec)
sys.modules["sshwiz"] = shw
_loader.exec_module(shw)

_PATH_ATTRS = ("SSH_CONFIG", "EXTRA_HOSTS_FILE", "CATEGORIES_FILE",
               "FAVORITES_FILE", "USAGE_FILE", "CONFIG_DIR", "STATE_DIR")


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
        vars(shw).update({
            "SSH_CONFIG": self.ssh_config,
            "EXTRA_HOSTS_FILE": self.extra_hosts,
            "CATEGORIES_FILE": self.categories,
            "FAVORITES_FILE": self.favorites,
            "USAGE_FILE": self.usage,
            "CONFIG_DIR": root,
            "STATE_DIR": root,
        })

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
            "Host bar\n  User carol\n")
        hosts, order = shw.parse_ssh_config(self.ssh_config)
        self.assertEqual(order, ["foo", "bar"])
        self.assertEqual(hosts["foo"]["user"], "alice")
        self.assertEqual(hosts["foo"]["port"], "2222")
        self.assertEqual(hosts["bar"]["user"], "carol")

    def test_tabs_and_match_blocks(self):
        self.write_config(
            "Match host *.example\n  Port 9999\n"
            "Host\tbaz\n\tUser tabby\n"
            "Host qux\n  User dave\n")
        hosts, order = shw.parse_ssh_config(self.ssh_config)
        self.assertEqual(order, ["baz", "qux"])
        self.assertEqual(hosts["baz"]["user"], "tabby")
        self.assertIsNone(hosts["baz"]["port"])  # Match block skipped

    def test_host_patterns_skipped(self):
        self.write_config("Host *.wild foo\n  User w\n")
        _hosts, order = shw.parse_ssh_config(self.ssh_config)
        self.assertEqual(order, ["foo"])


class ParseExtraHostsTest(TempPathsTestCase):
    def test_entries(self):
        self.extra_hosts.write_text(
            "# comment\n"
            "myhost  alice@example.com:8022  -o Foo=bar\n"
            "plain  bob@example.com\n"
            "nouser  example.org\n"
            "broken  justonefield\n")
        entries = shw.parse_extra_hosts(self.extra_hosts)
        self.assertEqual([e["name"] for e in entries],
                         ["myhost", "plain", "nouser", "broken"])
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
            "Host foo\n  User alice\n  Port 2222\n  IdentityFile ~/.ssh/id_foo\n")
        self.extra_hosts.write_text(
            "ex  bob@example.com:8022\nnouser  example.org\n")
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
        self.assertEqual(hosts["nouser"].display,
                         shw._default_user() + "@example.org")


class BuildSshCmdTest(unittest.TestCase):
    def make_host(self, **kw):
        base = {"name": "foo", "display": "alice@foo", "target": "foo"}
        base.update(kw)
        return shw.Host(**base)

    def test_full(self):
        h = self.make_host(cmd_port="8022", extra="-o Foo=bar")
        cmd = shw.build_ssh_cmd(h, password_mode=True, tmux_mode=True)
        self.assertEqual(cmd, ["ssh", "-t", "-o", "PubkeyAuthentication=no",
                               "-p", "8022", "-o", "Foo=bar", "foo",
                               "tmux", "new", "-A", "-s", "wiz-foo"])

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
        self.assertEqual(shw.categorize("lan", [("Lan", "LAN*", None)]),
                         shw.OTHER_CATEGORY)

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

    def test_toggle_favorite_file(self):
        self.assertTrue(shw.toggle_favorite_file("a"))
        self.assertIn("a", shw.load_favorites())
        self.assertFalse(shw.toggle_favorite_file("a"))
        self.assertNotIn("a", shw.load_favorites())

    def test_category_order_and_colors(self):
        self.categories.write_text("LAN  10.*  magenta\nLAN  100.*\nCLOUD  v*\n")
        rules = shw.load_category_rules(self.categories)
        hosts = [shw.Host(name="a", display="a", target="a", category="LAN"),
                 shw.Host(name="b", display="b", target="b", category="CLOUD"),
                 shw.Host(name="c", display="c", target="c", category="OTHER")]
        self.assertEqual(shw.category_order(hosts, rules),
                         ["ALL", "LAN", "CLOUD", "OTHER"])
        colors = shw.category_colors(hosts, rules)
        self.assertEqual(colors["LAN"], "magenta")
        self.assertEqual(colors["OTHER"], "grey62")
        self.assertIn(colors["CLOUD"], shw.CATEGORY_COLORS)

    def test_category_rules_invalid_color(self):
        self.categories.write_text("A  *  notacolor\nB  *\n")
        self.assertEqual(shw.load_category_rules(self.categories),
                         [("A", "*", None), ("B", "*", None)])

    def test_find_host_fallback(self):
        h = shw.find_host("nope.example")
        self.assertEqual(h.name, "nope.example")
        self.assertEqual(h.target, "nope.example")
        self.assertIsNone(h.user)


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


if __name__ == "__main__":
    unittest.main()
