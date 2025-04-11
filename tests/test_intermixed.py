import sys
import argparse
import configargparse
from unittest import mock
from io import StringIO

from tests.test_base import TestCase, captured_output

# Intermixed parsing was added in Python 3.7
# There are some tests for it that get run within test_argparse_tests.py but we can also
# add some of our own. Since ConfigArgParse only allows the setting of named arguments,
# and intermixed parsing only impacts positional arguments, we shouldn't have problems.


class TestBasicUseCases(TestCase):

    def testIntermixed1(self):
        # This is the example given in the argparse docs:
        # 'doit 1 --foo bar 2 3'
        self.add_arg("--foo")
        self.add_arg("cmd")
        self.add_arg("rest", nargs="*", type=int)

        # Regular behaviour
        ns, args = self.parse_known("doit 1 --foo bar 2 3".split())
        self.assertEqual(ns.rest, [1])
        self.assertEqual(args, ["2", "3"])

        # Interleaved behaviour
        ns, args = self.parser.parse_known_intermixed_args(
            "doit 1 --foo bar 2 3".split()
        )
        self.assertEqual(ns.rest, [1, 2, 3])
        self.assertEqual(args, [])

        # Note that "2 3" are listed as unrecognised arguments by the regular ArgumentParser
        self.assertParseArgsRaises(
            "unrecognized arguments: --xxx xxxval 2 3",
            args="doit 1 --xxx xxxval --foo bar 2 3",
            intermixed=True,
        )

        # Add in some config
        self.add_arg("--baz")
        ns, args = self.parser.parse_known_intermixed_args(
            "doit 1 --foo bar 2 3".split(), config_file_contents="baz: 'bazval'"
        )
        self.assertEqual(ns.baz, "bazval")
        self.assertEqual(ns.rest, [1, 2, 3])
        self.assertEqual(args, [])

        # As above, but now "--xxx xxxval" is in the config.
        self.assertParseArgsRaises(
            "unrecognized arguments: --xxx=xxxval 2 3",
            args="doit 1 --foo bar 2 3",
            config_file_contents="xxx: 'xxxval'",
            intermixed=True,
        )
