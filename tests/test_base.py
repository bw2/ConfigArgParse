import argparse
import configargparse
from contextlib import contextmanager
import logging
import os
import sys
import types
import unittest
from io import StringIO

# enable logging to simplify debugging
logger = logging.getLogger()
logger.level = logging.WARNING
stream_handler = logging.StreamHandler(sys.stdout)
logger.addHandler(stream_handler)

def replace_error_method(arg_parser):
    """Swap out arg_parser's error(..) method so that instead of calling
    sys.exit(..) it just raises an error.
    """
    def error_method(self, message):
        raise argparse.ArgumentError(None, message)

    def exit_method(self, status, message=None):
        self._exit_method_called = True

    arg_parser._exit_method_called = False
    arg_parser.error = types.MethodType(error_method, arg_parser)
    arg_parser.exit = types.MethodType(exit_method, arg_parser)

    return arg_parser


@contextmanager
def captured_output():
    """
    swap stdout and stderr for StringIO so we can do asserts on outputs.
    """
    new_out, new_err = StringIO(), StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = new_out, new_err
        yield sys.stdout, sys.stderr
    finally:
        sys.stdout, sys.stderr = old_out, old_err


class TestCase(unittest.TestCase):

    def setUp(self):
        # set COLUMNS to get expected wrapping
        os.environ['COLUMNS'] = '80'

        if sys.version_info >= (3, 10):
            self.OPTIONAL_ARGS_STRING="options"
        else:
            self.OPTIONAL_ARGS_STRING="optional arguments"

        self.initParser(args_for_setting_config_path=[])

    def initParser(self, *args, **kwargs):
        p = configargparse.ArgParser(*args, **kwargs)
        self.parser = replace_error_method(p)
        self.add_arg = self.parser.add_argument
        self.parse = self.parser.parse_args
        self.parse_known = self.parser.parse_known_args
        self.format_values = self.parser.format_values
        self.format_help = self.parser.format_help

        # TODO - do we really need this??
        if not hasattr(self, "assertRegex"):
            self.assertRegex = self.assertRegexpMatches
        if not hasattr(self, "assertRaisesRegex"):
            self.assertRaisesRegex = self.assertRaisesRegexp

        return self.parser

    def assertParseArgsRaises(self, regex, args, **kwargs):
        self.assertRaisesRegex(argparse.ArgumentError, regex, self.parse,
                               args=args, **kwargs)
