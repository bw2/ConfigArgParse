"""Tests in this file all relate to specific issues listed at
https://github.com/bw2/ConfigArgParse/issues/275

No fixes without a test case!
"""

import os, sys, re
from textwrap import dedent
import unittest
from unittest.mock import patch, mock_open
from io import StringIO

import configargparse

from tests.test_base import TestCase


class TestIssues(TestCase):

    def test_issue_275_a(self):
        """With a subparser the env vars are tacked on the end,
        when any config used has nargs > 1.
        """
        p = configargparse.ArgParser(prog="TEST")
        p.add_argument("--foo", "-f", nargs="+", required=False)
        p.add_argument("--moo", "-m", action="store_true")
        sp = p.add_subparsers(dest="sp_name")
        spa = sp.add_parser("a")
        spa.add_argument("abar", type=int)
        spa = sp.add_parser("b")
        spa.add_argument("bbar", type=int)

        # This one always worked
        options = p.parse_args(args=["-f", "x", "y", "-m", "a", "22"])
        self.assertEqual(
            vars(options), dict(foo=["x", "y"], moo=True, sp_name="a", abar=22)
        )

        # But this was broken and now works
        cfile = dedent(
            """\
            foo = ["j","k"]
            """
        )

        options = p.parse_args(args=["-m", "a", "22"], config_file_contents=cfile)
        self.assertEqual(
            vars(options), dict(foo=["j", "k"], moo=True, sp_name="a", abar=22)
        )

        # This one was broken by my fix but I re-fixed it.
        cfile = dedent(
            """\
            moo = true
            """
        )
        options = p.parse_args(args=["a", "22"], config_file_contents=cfile)
        self.assertEqual(vars(options), dict(foo=None, moo=True, sp_name="a", abar=22))

    @unittest.expectedFailure
    def test_issue_275_b(self):
        # This and related cases remain broken, and I don't know how to fix it :-(
        # Basically, don't use variable-length args with subparsers.

        self.add_arg("--foo", "-f", nargs="+", required=False)
        self.add_arg("--moo", "-m", action="store_true")
        sp = self.parser.add_subparsers(dest="sp_name")
        spa = sp.add_parser("a")
        spa.add_argument("abar", type=int)
        spa = sp.add_parser("b")
        spa.add_argument("bbar", type=int)

        # In this case the argument munger incorrectly produces:
        #  -f j k a 22
        # And --foo eats all four of the arguments
        # Or without the exception in insert_args() we could have it be:
        #  a 22 -f j k
        # But I can't see any way to arrange this command line to get the desired
        # result, never mind how to do it programatically.
        cfile = dedent(
            """\
            foo = ["j","k"]
            """
        )

        options = self.parse(args=["a", "22"], config_file_contents=cfile)
        self.assertEqual(
            vars(options), dict(foo=["j", "k"], moo=False, sp_name="a", abar=22)
        )

    def test_issue_275_c(self):
        """Including -- to end processing of named args did not work as
        advertised.
        """
        p = configargparse.ArgParser()
        p.add("--foo", "-f", nargs="?", env_var="FOO", required=False)
        p.add("bar", nargs="*")

        options = p.parse_args(
            args=["--", "arg1", "arg2"], env_vars=dict(FOO="environ_value")
        )

        self.assertEqual(vars(options), dict(foo="environ_value", bar=["arg1", "arg2"]))

    # This currently seems to run into a bug within argparse itself,
    # or else I don't understand the expected behaviour
    @unittest.expectedFailure
    def test_issue_275_c_custom_char(self):
        """Including ++ should also work if prefix_chars is set to '+'"""
        p = configargparse.ArgParser(prefix_chars="+")
        p.add("++foo", "+f", nargs="?", env_var="FOO", required=False)
        p.add("bar", nargs="*")

        options = p.parse_args(
            args=["++", "arg1", "arg2"], env_vars=dict(FOO="environ_value")
        )

        self.assertEqual(vars(options), dict(foo="environ_value", bar=["arg1", "arg2"]))

    def test_issue_pr_293(self):
        """Handle pathlib Path used as default_config_files"""
        from pathlib import Path

        self.initParser(default_config_files=[Path("~/.my_settings.ini")])
        self.add_arg("foo")
        self.add_arg("--flag", help="Flag that can be set in the config file")

        with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
            self.assertRaisesRegex(TypeError, "exit", self.parse_known, "--help")

            self.assertRegex(mock_stdout.getvalue(), r"usage:")

    def test_issue_265(self):

        # This can't be tested by using config_file_contents, so use a tmpFile
        with self.tmpFile() as tf:
            conf_filename = tf.name
            print("option=foo", file=tf)

        p = configargparse.ArgumentParser()
        p.add_argument("first_arg")
        p.add_argument("config", is_config_file=True)
        p.add_argument("--option")

        ns, rest = p.parse_known_args(["first", conf_filename])

        self.assertEqual(rest, [])
        self.assertEqual(
            vars(ns), dict(first_arg="first", config=conf_filename, option="foo")
        )

    def test_issue_292(self):
        # Now we should be able to get at the command line by inspecting
        # parser.last_parsed_args
        with self.tmpFile() as tf:
            conf_filename = tf.name
            print("option=[foo]", file=tf)

        p = configargparse.ArgumentParser()
        p.add_argument("first_arg")
        p.add_argument("--option", nargs="*")
        p.add_argument("--config", is_config_file=True)
        p.add_argument("--another", env_var="ANOTHER")

        ns, rest = p.parse_known_args(
            ["first", "--config", conf_filename], env_vars=dict(ANOTHER="aaa")
        )

        self.assertEqual(
            p.last_parsed_args,
            ["first", "--option", "foo", "--another=aaa", "--config", conf_filename],
        )
