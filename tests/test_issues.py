"""Tests in this file all relate to specific issues listed at
https://github.com/bw2/ConfigArgParse/issues/275

No fixes without a test case!
"""

import os, sys, re
from textwrap import dedent
import unittest
from unittest.mock import patch

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
