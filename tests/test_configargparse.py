import argparse
from collections import OrderedDict
import configargparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import functools
import importlib.util
import inspect
import json
import logging
import os
import pathlib
import sys
import tempfile
import traceback
import types
import unittest
from unittest import mock
import warnings
import textwrap

from io import BytesIO, StringIO

if sys.version_info >= (3, 10):
    OPTIONAL_ARGS_STRING = "options"
else:
    OPTIONAL_ARGS_STRING = "optional arguments"

# In Python 3.13+, short option metavars are no longer repeated
if sys.version_info >= (3, 13):
    SHORT_OPT_METAVAR = ""  # e.g., "-g, --my-cfg-file MY_CFG_FILE"
else:
    SHORT_OPT_METAVAR = "(?: {metavar})?"  # e.g., "-g MY_CFG_FILE, --my-cfg-file MY_CFG_FILE" or "-g, --my-cfg-file MY_CFG_FILE"

# set COLUMNS to get expected wrapping
os.environ["COLUMNS"] = "80"

# YAMLConfigFileParser reads and writes YAML with PyYAML, an optional dependency
skip_unless_pyyaml_is_installed = unittest.skipUnless(
    importlib.util.find_spec("yaml") is not None, "PyYAML not installed"
)


def replace_error_method(arg_parser):
    """Swap out arg_parser's error(..) method so that instead of calling
    sys.exit(..) it just raises an error.
    """

    def error_method(self, message):
        raise argparse.ArgumentError(None, message)

    def exit_method(self, status=0, message=None):
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
    with redirect_stdout(new_out), redirect_stderr(new_err):
        yield new_out, new_err


def write_config_file_and_read_it_back(parser, namespace):
    """Write a config file for a namespace to a temp dir and read it back.

    The "Wrote config file to ..." message that write_config_file() prints is
    captured so it doesn't show up in the test run's output.

    Args:
        parser: The parser whose write_config_file() writes the file.
        namespace: The parsed namespace to write out.

    Returns:
        The contents of the written config file.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "written_config_file.conf")
        with captured_output():
            parser.write_config_file(namespace, [path])
        with open(path) as config_file:
            return config_file.read()


class TrackingStringIO(StringIO):
    """A StringIO that adds itself to a given list when created.

    This lets a test check afterwards, through each stream's .closed
    attribute, that the parser closed every stream it opened.

    Args:
        text: The stream's contents.
        opened_streams: The list this stream appends itself to.
    """

    def __init__(self, text, opened_streams):
        super().__init__(text)
        opened_streams.append(self)


class PathLikeThatFailsIfCalled:
    """An object that is both os.PathLike and callable.

    A default_config_files entry like this must be read as a path, so calling
    it fails the test.

    Args:
        path: The path that __fspath__() returns.
    """

    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return self.path

    def __call__(self):
        raise AssertionError(
            "a PathLike default_config_files entry was called instead of being "
            "read as a path"
        )


class TestCase(unittest.TestCase):
    def initParser(self, *args, **kwargs):
        p = configargparse.ArgParser(*args, **kwargs)
        self.parser = replace_error_method(p)
        self.add_arg = self.parser.add_argument
        self.parse = self.parser.parse_args
        self.parse_known = self.parser.parse_known_args
        self.format_values = self.parser.format_values
        self.format_help = self.parser.format_help

        return self.parser

    def assertParseArgsRaises(self, regex, args, **kwargs):
        self.assertRaisesRegex(
            argparse.ArgumentError, regex, self.parse, args=args, **kwargs
        )

    def make_temp_file_holding_contents(self, contents="", suffix=""):
        """Create a temp file holding the given text.

        The file is removed when the test finishes, even if it fails.

        Args:
            contents: The text to write to the file.
            suffix: The end of the file name, e.g. ".ini".

        Returns:
            The path of the new file.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as f:
            f.write(contents)
        self.addCleanup(os.remove, f.name)
        return f.name

    def make_unused_path_in_a_new_temp_dir(self):
        """Return a path that nothing exists at yet, for the parser to create.

        The path is in a new temp directory, which is removed along with
        anything in it when the test finishes, even if it fails.

        Returns:
            The path, which doesn't exist yet.
        """
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return os.path.join(temp_dir.name, "config_file")

    def write_config_file_with_the_write_out_arg_and_read_it_back(
        self, option, add_arg_kwargs, args, env_vars=None
    ):
        """Write a config file for one option with '-w', then read it back.

        The option is added to a parser with the '-w' write-out arg, which
        parses args plus '-w <path>'. It is then added, without its default, to
        a new parser that reads the written file as a default config file.

        Args:
            option: The option string, e.g. "--size".
            add_arg_kwargs: Keyword args for add_argument().
            args: The command line args, without the write-out arg.
            env_vars: The env vars for the first parse, if any.

        Returns:
            A tuple of the written file's contents, the option's value from the
            first parse, and its value read back from the written file.
        """
        output_path = self.make_unused_path_in_a_new_temp_dir()
        self.initParser(args_for_writing_out_config_file=["-w"])
        dest = self.add_arg(option, **add_arg_kwargs).dest
        with captured_output():
            parsed_namespace = self.parse(
                args=args + ["-w", output_path], env_vars=env_vars or {}
            )
        with open(output_path) as output_file:
            written_contents = output_file.read()

        self.initParser(default_config_files=[output_path])
        self.add_arg(
            option, **{k: v for k, v in add_arg_kwargs.items() if k != "default"}
        )
        return (
            written_contents,
            getattr(parsed_namespace, dest),
            getattr(self.parse(args=[], env_vars={}), dest),
        )

    def make_file_holding_original_contents(self):
        """Create a temp file holding the text "original contents".

        The file is removed when the test finishes, even if it fails.

        Returns:
            The path of the new file.
        """
        return self.make_temp_file_holding_contents("original contents")

    def assert_file_still_holds_original_contents(self, path, msg=None):
        """Assert that a file made by make_file_holding_original_contents()
        was not overwritten.

        Args:
            path: The path of the file to check.
            msg: Optional message to show if the assertion fails.
        """
        with open(path) as f:
            self.assertEqual(f.read(), "original contents", msg=msg)


class TestBasicUseCases(TestCase):
    def setUp(self):
        self.initParser(args_for_setting_config_path=[])

    def testBasicCase1(self):
        ## Test command line and config file values
        self.add_arg("filenames", nargs="+", help="positional arg")
        self.add_arg("-x", "--arg-x", action="store_true")
        self.add_arg("-y", "--arg-y", dest="y1", type=int, required=True)
        self.add_arg("--arg-z", action="append", type=float, required=True)
        if sys.version_info >= (3, 9):
            self.add_arg("--foo", action=argparse.BooleanOptionalAction, default=False)
        else:
            self.add_arg("--foo", action="store_true", default=False)

        # make sure required args are enforced
        self.assertParseArgsRaises("the following arguments are required", args="")
        self.assertParseArgsRaises(
            "the following arguments are required: -y/--arg-y",
            args="-x --arg-z 11 file1.txt",
        )
        self.assertParseArgsRaises(
            "the following arguments are required: --arg-z",
            args="file1.txt file2.txt file3.txt -x -y 1",
        )

        # check values after setting args on command line
        ns = self.parse(
            args="file1.txt --arg-x -y 3 --arg-z 10 --foo", config_file_contents=""
        )
        self.assertListEqual(ns.filenames, ["file1.txt"])
        self.assertEqual(ns.arg_x, True)
        self.assertEqual(ns.y1, 3)
        self.assertEqual(ns.arg_z, [10])
        self.assertEqual(ns.foo, True)

        self.assertRegex(
            self.format_values(),
            "Command Line Args:   file1.txt --arg-x -y 3 --arg-z 10",
        )

        # check values after setting args in config file
        ns = self.parse(
            args="file1.txt file2.txt",
            config_file_contents="""
            # set all required args in config file
            arg-x = True
            arg-y = 10
            arg-z = 30
            arg-z = 40
            foo = True
            """,
        )
        self.assertListEqual(ns.filenames, ["file1.txt", "file2.txt"])
        self.assertEqual(ns.arg_x, True)
        self.assertEqual(ns.y1, 10)
        self.assertEqual(ns.arg_z, [40])
        self.assertEqual(ns.foo, True)

        self.assertRegex(
            self.format_values(),
            "Command Line Args: \\s+ file1.txt file2.txt\n"
            "Config File \\(method arg\\):\n"
            "  arg-x: \\s+ True\n"
            "  arg-y: \\s+ 10\n"
            "  arg-z: \\s+ 40\n"
            "  foo: \\s+ True\n",
        )

        # check values after setting args in both command line and config file
        ns = self.parse(
            args="file1.txt file2.txt --arg-x -y 3 --arg-z 100 ",
            config_file_contents="""arg-y = 31.5
                                    arg-z = 30
                                    foo = True
                                 """,
        )
        self.format_help()
        self.format_values()
        self.assertListEqual(ns.filenames, ["file1.txt", "file2.txt"])
        self.assertEqual(ns.arg_x, True)
        self.assertEqual(ns.y1, 3)
        self.assertEqual(ns.arg_z, [100])
        self.assertEqual(ns.foo, True)

        self.assertRegex(
            self.format_values(),
            "Command Line Args:   file1.txt file2.txt --arg-x -y 3 --arg-z 100",
        )

    def testBasicCase2(self, use_groups=False):

        ## Test command line, config file and env var values
        default_config_file_path = self.make_temp_file_holding_contents()

        p = self.initParser(
            default_config_files=[
                "/etc/settings.ini",
                "/home/jeff/.user_settings",
                default_config_file_path,
            ]
        )
        p.add_arg("vcf", nargs="+", help="Variant file(s)")
        parser_or_argument_group_g1 = (
            p.add_argument_group(title="g1") if use_groups else p
        )
        parser_or_argument_group_g1.add_arg(
            "--genome", help="Path to genome file", required=True
        )
        parser_or_argument_group_g1.add_arg("-v", dest="verbose", action="store_true")
        parser_or_argument_group_g1.add_arg(
            "-g", "--my-cfg-file", required=True, is_config_file=True
        )
        parser_or_argument_group_g2 = (
            p.add_argument_group(title="g2") if use_groups else p
        )
        parser_or_argument_group_g2.add_arg("-d", "--dbsnp", env_var="DBSNP_PATH")
        parser_or_argument_group_g2.add_arg(
            "-f",
            "--format",
            choices=["BED", "MAF", "VCF", "WIG", "R"],
            dest="fmt",
            metavar="FRMT",
            env_var="OUTPUT_FORMAT",
            default="BED",
        )

        # make sure required args are enforced
        self.assertParseArgsRaises(
            "the following arguments are required: vcf, -g/--my-cfg-file",
            args="--genome hg19",
        )
        self.assertParseArgsRaises(
            r"Unable to open config file: file\.txt\. Error: .*No such file or director",
            args="-g file.txt",
        )

        # check values after setting args on command line
        config_file2_path = self.make_temp_file_holding_contents()

        ns = self.parse(args="--genome hg19 -g %s bla.vcf " % config_file2_path)
        self.assertEqual(ns.genome, "hg19")
        self.assertEqual(ns.verbose, False)
        self.assertIsNone(ns.dbsnp)
        self.assertEqual(ns.fmt, "BED")
        self.assertListEqual(ns.vcf, ["bla.vcf"])

        self.assertRegex(
            self.format_values(),
            "Command Line Args:   --genome hg19 -g [^\\s]+ bla.vcf\n"
            "Defaults:\n"
            "  --format: \\s+ BED\n",
        )

        # check precedence: args > env > config > default using the --format arg
        with open(default_config_file_path, "w") as f:
            f.write("--format MAF")
        ns = self.parse(args="--genome hg19 -g %s f.vcf " % config_file2_path)
        self.assertEqual(ns.fmt, "MAF")
        self.assertRegex(
            self.format_values(),
            "Command Line Args:   --genome hg19 -g [^\\s]+ f.vcf\n"
            "Config File \\([^\\s]+\\):\n"
            "  --format: \\s+ MAF\n",
        )

        with open(config_file2_path, "w") as f:
            f.write("--format VCF")
        ns = self.parse(args="--genome hg19 -g %s f.vcf " % config_file2_path)
        self.assertEqual(ns.fmt, "VCF")
        self.assertRegex(
            self.format_values(),
            "Command Line Args:   --genome hg19 -g [^\\s]+ f.vcf\n"
            "Config File \\([^\\s]+\\):\n"
            "  --format: \\s+ VCF\n",
        )

        ns = self.parse(
            env_vars={"OUTPUT_FORMAT": "R", "DBSNP_PATH": "/a/b.vcf"},
            args="--genome hg19 -g %s f.vcf " % config_file2_path,
        )
        self.assertEqual(ns.fmt, "R")
        self.assertEqual(ns.dbsnp, "/a/b.vcf")
        self.assertRegex(
            self.format_values(),
            "Command Line Args:   --genome hg19 -g [^\\s]+ f.vcf\n"
            "Environment Variables:\n"
            "  DBSNP_PATH: \\s+ /a/b.vcf\n"
            "  OUTPUT_FORMAT: \\s+ R\n",
        )

        ns = self.parse(
            env_vars={
                "OUTPUT_FORMAT": "R",
                "DBSNP_PATH": "/a/b.vcf",
                "ANOTHER_VAR": "something",
            },
            args="--genome hg19 -g %s --format WIG f.vcf" % config_file2_path,
        )
        self.assertEqual(ns.fmt, "WIG")
        self.assertEqual(ns.dbsnp, "/a/b.vcf")
        self.assertRegex(
            self.format_values(),
            "Command Line Args:   --genome hg19 -g [^\\s]+ --format WIG f.vcf\n"
            "Environment Variables:\n"
            "  DBSNP_PATH: \\s+ /a/b.vcf\n",
        )

        short_g = SHORT_OPT_METAVAR.format(metavar="MY_CFG_FILE")
        short_d = SHORT_OPT_METAVAR.format(metavar="DBSNP")
        short_f = SHORT_OPT_METAVAR.format(metavar="FRMT")
        help_lines_for_the_options_in_group_g1 = (
            "  --genome GENOME \\s+ Path to genome file\n"
            "  -v\n"
            f"  -g{short_g}, --my-cfg-file MY_CFG_FILE\n"
        )
        help_lines_for_the_options_in_group_g2 = (
            f"  -d{short_d}, --dbsnp DBSNP\\s+\\[env var: DBSNP_PATH\\]\n"
            f"  -f{short_f}, --format FRMT\\s+\\[env var: OUTPUT_FORMAT\\]\n\n"
        )
        if not use_groups:
            option_and_group_sections = (
                help_lines_for_the_options_in_group_g1
                + help_lines_for_the_options_in_group_g2
            )
        else:
            option_and_group_sections = (
                "\ng1:\n"
                + help_lines_for_the_options_in_group_g1
                + "\ng2:\n"
                + help_lines_for_the_options_in_group_g2
            )
        self.assertRegex(
            self.format_help(),
            "usage: .*\\s+\\[-h\\] --genome GENOME \\[-v\\] -g MY_CFG_FILE\n?"
            "\\s+\\[-d DBSNP\\]\\s+\\[-f FRMT\\]\\s+vcf \\[vcf ...\\]\n\n"
            "positional arguments:\n"
            "  vcf \\s+ Variant file\\(s\\)\n\n"
            "%s:\n"
            "  -h, --help \\s+ show this help message and exit\n" % OPTIONAL_ARGS_STRING
            + option_and_group_sections
            + 7 * r"(.+\s*)",
        )

        self.assertParseArgsRaises(
            "invalid choice: 'ZZZ'",
            args="--genome hg19 -g %s --format ZZZ f.vcf" % config_file2_path,
        )
        self.assertParseArgsRaises(
            "unrecognized arguments: --bla",
            args="--bla --genome hg19 -g %s f.vcf" % config_file2_path,
        )

    def testBasicCase2_WithGroups(self):
        self.testBasicCase2(use_groups=True)

    def testCustomOpenFunction(self):
        expected_output = "dummy open called"

        def dummy_open(p):
            print(expected_output)
            return open(p)

        self.initParser(config_file_open_func=dummy_open)
        self.add_arg("--config", is_config_file=True)
        self.add_arg("--arg1", default=1, type=int)

        config_file_path = self.make_temp_file_holding_contents("arg1 2")

        with captured_output() as (out, _):
            args = self.parse("--config {}".format(config_file_path))
            self.assertTrue(hasattr(args, "arg1"))
            self.assertEqual(args.arg1, 2)
            output = out.getvalue().strip()
            self.assertEqual(output, expected_output)

    def testIgnoreHelpArgs(self):
        self.initParser()
        self.add_arg("--arg1")
        args, _ = self.parse_known("--arg2 --help", ignore_help_args=True)
        self.assertEqual(args.arg1, None)
        self.add_arg("--arg2")
        args, _ = self.parse_known("--arg2 3 --help", ignore_help_args=True)
        self.assertEqual(args.arg2, "3")
        self.assertIs(self.parser._exit_method_called, False)
        with captured_output():
            self.parse_known("--arg2 3 --help", ignore_help_args=False)
        self.assertIs(self.parser._exit_method_called, True)

    def testPositionalAndConfigVarLists(self):
        self.initParser()
        self.add_arg("a")
        self.add_arg("-x", "--arg", nargs="+")

        ns = self.parse(
            "positional_value",
            config_file_contents="""arg = [Shell, someword, anotherword]""",
        )

        self.assertEqual(ns.arg, ["Shell", "someword", "anotherword"])
        self.assertEqual(ns.a, "positional_value")

    @unittest.skipUnless(
        hasattr(argparse.ArgumentParser, "parse_intermixed_args"),
        "requires Python 3.7+",
    )
    def testParseIntermixedArgs_ReadsConfigFilesAndEnvVarsThatTheCommandLineOverrides(
        self,
    ):
        config_file_path = self.make_temp_file_holding_contents(
            "genome = hg38\ntag = [from_config]\n"
        )
        self.initParser(default_config_files=[config_file_path])
        self.add_arg("--genome")
        self.add_arg("--threads", env_var="THREADS")
        self.add_arg("--tag", action="append", env_var="TAG")
        self.add_arg("vcf", nargs="*")

        ns = self.parser.parse_intermixed_args(
            args=["a.vcf", "--tag", "from_command_line", "b.vcf"],
            env_vars={"THREADS": "4", "TAG": "from_env_var"},
        )
        self.assertEqual(ns.genome, "hg38")
        self.assertEqual(ns.threads, "4")
        # the command line value replaces the env var's and the config file's,
        # rather than being appended to them
        self.assertEqual(ns.tag, ["from_command_line"])
        self.assertEqual(ns.vcf, ["a.vcf", "b.vcf"])

        ns, unknown_args = self.parser.parse_known_intermixed_args(
            args=["a.vcf", "b.vcf", "--unknown"], env_vars={}
        )
        self.assertEqual(ns.genome, "hg38")
        self.assertEqual(ns.tag, ["from_config"])
        self.assertEqual(ns.vcf, ["a.vcf", "b.vcf"])
        self.assertEqual(unknown_args, ["--unknown"])

        ns = self.parser.parse_intermixed_args(
            args=["a.vcf", "--genome", "hg19"],
            config_file_contents="genome = chm13\n",
            env_vars={},
        )
        self.assertEqual(ns.genome, "hg19")
        ns = self.parser.parse_intermixed_args(
            args=["a.vcf"], config_file_contents="genome = chm13\n", env_vars={}
        )
        self.assertEqual(ns.genome, "chm13")

    def testCustomActionWithNargsAndConfigVarList(self):
        # https://github.com/bw2/ConfigArgParse/issues/354
        class CustomAction(configargparse.Action):
            def __call__(self, parser, namespace, values, option_string=None):
                setattr(namespace, self.dest, values)

        self.initParser()
        self.add_arg("-x", "--arg", nargs="+", action=CustomAction)

        ns = self.parse("", config_file_contents="""arg = [foo, bar]""")
        self.assertEqual(ns.arg, ["foo", "bar"])

    def testCustomActionWithoutNargsAndConfigVarList(self):
        class CustomAction(configargparse.Action):
            def __call__(self, parser, namespace, values, option_string=None):
                setattr(namespace, self.dest, values)

        self.initParser()
        self.add_arg("-x", "--arg", action=CustomAction)

        self.assertParseArgsRaises(
            "arg can't be set to a list",
            args="",
            config_file_contents="""arg = [foo, bar]""",
        )

    def testNargs1TakesAListFromAConfigFileOrEnvVar(self):
        """argparse stores an nargs=1 value as a one-element list."""
        self.initParser()
        self.add_arg("--x", nargs=1, env_var="X")
        self.add_arg("--y", nargs=1, action="append")
        ns = self.parse(
            args="", config_file_contents='y = [["a"], ["b"]]', env_vars={"X": "[c]"}
        )
        self.assertEqual(ns.x, ["c"])
        self.assertEqual(ns.y, [["a"], ["b"]])

    def testFormatValuesListsAPositionalDefaultOnlyWhenItWasntGiven(self):
        self.initParser()
        self.add_arg("--genome", default="hg19")
        self.add_arg("vcf", nargs="?", default="default.vcf")
        self.add_arg("bams", nargs="*", default=["default.bam"])
        self.parse(args=["given.vcf", "given.bam"], env_vars={})
        self.assertEqual(
            self.format_values(),
            "Command Line Args:   given.vcf given.bam\n"
            "Defaults:\n"
            "  --genome:          hg19\n",
        )
        self.parse(args=[], env_vars={})
        self.assertEqual(
            self.format_values(),
            "Defaults:\n"
            "  --genome:          hg19\n"
            "  vcf:               default.vcf\n"
            "  bams:              ['default.bam']\n",
        )

    def testMissingOptionValueIsAUsageErrorWithAConfigFilePathArg(self):
        # the parser that looks for the config file path hits the error first,
        # and before Python 3.9 its parse_known_args() then returns None
        self.initParser(args_for_setting_config_path=["-c", "--config"])
        self.add_arg("--foo")
        for args in (["-c"], ["--foo"], ["--foo", "-c", "x.ini"]):
            with self.subTest(args=args):
                self.assertParseArgsRaises("expected one argument", args=args)

    def testMutuallyExclusiveArgs(self):
        config_file_path = self.make_temp_file_holding_contents()

        p = self.parser
        g = p.add_argument_group(title="group1")
        g.add_arg("--genome", help="Path to genome file", required=True)
        g.add_arg("-v", dest="verbose", action="store_true")

        g = p.add_mutually_exclusive_group(required=True)
        g.add_arg("-f1", "--type1-cfg-file", is_config_file=True)
        g.add_arg("-f2", "--type2-cfg-file", is_config_file=True)

        g = p.add_mutually_exclusive_group(required=True)
        g.add_arg(
            "-f",
            "--format",
            choices=["BED", "MAF", "VCF", "WIG", "R"],
            dest="fmt",
            metavar="FRMT",
            env_var="OUTPUT_FORMAT",
            default="BED",
        )
        g.add_arg(
            "-b",
            "--bam",
            dest="fmt",
            action="store_const",
            const="BAM",
            env_var="BAM_FORMAT",
        )

        ns = self.parse(args="--genome hg19 -f1 %s --bam" % config_file_path)
        self.assertEqual(ns.genome, "hg19")
        self.assertEqual(ns.verbose, False)
        self.assertEqual(ns.fmt, "BAM")

        ns = self.parse(
            env_vars={"BAM_FORMAT": "true"},
            args="--genome hg19 -f1 %s" % config_file_path,
        )
        self.assertEqual(ns.genome, "hg19")
        self.assertEqual(ns.verbose, False)
        self.assertEqual(ns.fmt, "BAM")
        self.assertRegex(
            self.format_values(),
            "Command Line Args:   --genome hg19 -f1 [^\\s]+\n"
            "Environment Variables:\n"
            "  BAM_FORMAT: \\s+ true\n"
            "Defaults:\n"
            "  --format: \\s+ BED\n",
        )

        short_f1 = SHORT_OPT_METAVAR.format(metavar="TYPE1_CFG_FILE")
        short_f2 = SHORT_OPT_METAVAR.format(metavar="TYPE2_CFG_FILE")
        short_f = SHORT_OPT_METAVAR.format(metavar="FRMT")
        self.assertRegex(
            self.format_help(),
            r"usage: .*\s+\[-h\] --genome GENOME \[-v\]\s+\(-f1 TYPE1_CFG_FILE \|"
            r"\s+-f2 TYPE2_CFG_FILE\)\s+\(-f FRMT \| -b\)\n\n"
            "%s:\n"
            "  -h, --help            show this help message and exit\n"
            f"  -f1{short_f1}, --type1-cfg-file TYPE1_CFG_FILE\n"
            f"  -f2{short_f2}, --type2-cfg-file TYPE2_CFG_FILE\n"
            f"  -f{short_f}, --format FRMT\\s+\\[env var: OUTPUT_FORMAT\\]\n"
            "  -b, --bam\\s+\\[env var: BAM_FORMAT\\]\n\n"
            "group1:\n"
            "  --genome GENOME       Path to genome file\n"
            "  -v\n\n" % OPTIONAL_ARGS_STRING,
        )

    def testMutuallyExclusiveArgsOverriddenByOtherSources(self):
        # a config file or env var value for one member of a mutually exclusive
        # group is overridden by a different member set in a higher priority
        # source, instead of clashing with it (issue #164)
        def init_parser(**kwargs):
            p = self.initParser(**kwargs)
            g = p.add_mutually_exclusive_group(required=True)
            g.add_arg(
                "-m",
                "--mega",
                dest="mode",
                action="store_const",
                const="mega",
                env_var="MEGA",
            )
            g.add_arg(
                "-g",
                "--giga",
                dest="mode",
                action="store_const",
                const="giga",
                env_var="GIGA",
            )
            return p

        init_parser()
        ns = self.parse(args=[], config_file_contents="mega = true", env_vars={})
        self.assertEqual(ns.mode, "mega")

        # command line overrides config file
        ns = self.parse(
            args=["--giga"], config_file_contents="mega = true", env_vars={}
        )
        self.assertEqual(ns.mode, "giga")
        self.assertEqual(self.format_values(), "Command Line Args:   --giga\n")
        ns = self.parse(args=["-g"], config_file_contents="mega = true", env_vars={})
        self.assertEqual(ns.mode, "giga")

        # command line overrides env var
        ns = self.parse(args=["--giga"], env_vars={"MEGA": "true"})
        self.assertEqual(ns.mode, "giga")
        self.assertEqual(self.format_values(), "Command Line Args:   --giga\n")

        # env var overrides config file
        ns = self.parse(
            args=[], config_file_contents="mega = true", env_vars={"GIGA": "true"}
        )
        self.assertEqual(ns.mode, "giga")

        # a config file given on the command line overrides a default config file
        default_config_path = self.make_temp_file_holding_contents("mega = true\n")
        user_config_path = self.make_temp_file_holding_contents("giga = true\n")

        init_parser(default_config_files=[default_config_path])
        self.add_arg("-c", "--config", is_config_file=True)
        ns = self.parse(args=[], env_vars={})
        self.assertEqual(ns.mode, "mega")
        ns = self.parse(args=["-c", user_config_path], env_vars={})
        self.assertEqual(ns.mode, "giga")

        # members set together within a single source still conflict
        init_parser()
        self.assertParseArgsRaises(
            "not allowed with argument",
            args=[],
            config_file_contents="mega = true\ngiga = true",
            env_vars={},
        )
        self.assertParseArgsRaises(
            "not allowed with argument",
            args=[],
            env_vars={"MEGA": "true", "GIGA": "true"},
        )
        self.assertParseArgsRaises(
            "not allowed with argument", args=["--mega", "--giga"], env_vars={}
        )

    def testMutuallyExclusiveBoolArgsOverriddenByCommandLine(self):
        # the --feature / --no-feature pattern from issue #164, with the
        # mutually exclusive group inside an argument group
        g = self.parser.add_argument_group(title="features")
        g = g.add_mutually_exclusive_group()
        g.add_arg("--feature", dest="feature", action="store_true")
        g.add_arg("--no-feature", dest="feature", action="store_false")

        ns = self.parse(args=[], config_file_contents="feature = true", env_vars={})
        self.assertEqual(ns.feature, True)
        ns = self.parse(
            args=["--no-feature"], config_file_contents="feature = true", env_vars={}
        )
        self.assertEqual(ns.feature, False)
        ns = self.parse(
            args=["--feature"], config_file_contents="no-feature = true", env_vars={}
        )
        self.assertEqual(ns.feature, True)

    def testSubParsers(self):
        config_file1_path = self.make_temp_file_holding_contents("--i = B")
        config_file2_path = self.make_temp_file_holding_contents("p = 10")

        parser = configargparse.ArgumentParser(prog="myProg")
        subparsers = parser.add_subparsers(title="actions")

        parent_parser = configargparse.ArgumentParser(add_help=False)
        parent_parser.add_argument(
            "-p", "--p", type=int, required=True, help="set db parameter"
        )

        create_p = subparsers.add_parser(
            "create", parents=[parent_parser], help="create the orbix environment"
        )
        create_p.add_argument("--i", env_var="INIT", choices=["A", "B"], default="A")
        create_p.add_argument("-config", is_config_file=True)

        update_p = subparsers.add_parser(
            "update", parents=[parent_parser], help="update the orbix environment"
        )
        update_p.add_argument("-config2", is_config_file=True, required=True)

        ns = parser.parse_args(args="create -p 2 -config " + config_file1_path)
        self.assertEqual(ns.p, 2)
        self.assertEqual(ns.i, "B")

        ns = parser.parse_args(args="update -config2 " + config_file2_path)
        self.assertEqual(ns.p, 10)

    def testSubParserEnvVar(self):
        """Test that env vars work with subparsers (issue #350)."""
        parser = configargparse.ArgumentParser(auto_env_var_prefix="")
        parser.add_argument("--tenant", env_var="TENANT", required=True)
        subparsers = parser.add_subparsers(dest="action")
        appliance_parser = subparsers.add_parser("appliance")
        appliance_parser.add_argument("--token", env_var="TOKEN", required=True)

        # Test parent parser env var via env_vars param
        ns = parser.parse_args(
            args=["appliance", "--token", "test-token"],
            env_vars={"TENANT": "test-tenant"},
        )
        self.assertEqual(ns.tenant, "test-tenant")
        self.assertEqual(ns.token, "test-token")
        self.assertEqual(ns.action, "appliance")

        # Test both parent and subparser env vars via real os.environ
        with mock.patch.dict(
            os.environ, {"TENANT": "test-tenant", "TOKEN": "test-token"}
        ):
            ns = parser.parse_args(args=["appliance"])
            self.assertEqual(ns.tenant, "test-tenant")
            self.assertEqual(ns.token, "test-token")
            self.assertEqual(ns.action, "appliance")

    def testAddArgsErrors(self):
        self.assertRaisesRegex(
            ValueError,
            "arg with "
            "is_write_out_config_file_arg=True can't also have "
            "is_config_file_arg=True",
            self.add_arg,
            "-x",
            "--X",
            is_config_file=True,
            is_write_out_config_file_arg=True,
        )
        self.assertRaisesRegex(
            ValueError,
            "arg with is_write_out_config_file_arg=True must have action='store'",
            self.add_arg,
            "-y",
            "--Y",
            action="append",
            is_write_out_config_file_arg=True,
        )

    def testConfigFileSyntax(self):
        self.add_arg("-x", required=True, type=int)
        self.add_arg("--y", required=True, type=float)
        self.add_arg("--z")
        self.add_arg("--c")
        self.add_arg("--b", action="store_true")
        self.add_arg("--a", action="append", type=int)
        self.add_arg(
            "--m",
            action="append",
            nargs=3,
            metavar=("<a1>", "<a2>", "<a3>"),
        )

        ns = self.parse(
            args="-x 1",
            env_vars={},
            config_file_contents="""

        #inline comment 1
        # inline comment 2
          # inline comment 3
        ;inline comment 4
        ; inline comment 5
          ;inline comment 6

        ---   # separator 1
        -------------  # separator 2

        y=1.1
          y = 2.1
        y= 3.1  # with comment
        y= 4.1  ; with comment
        ---
        y:5.1
          y : 6.1
        y: 7.1  # with comment
        y: 8.1  ; with comment
        ---
        y  \t 9.1
          y 10.1
        y 11.1  # with comment
        y 12.1  ; with comment
        ---
        b
        b = True
        b: True
        ----
        a = 33
        ---
        z z 1
        ---
        m = [[1, 2, 3], [4, 5, 6]]
        """,
        )

        self.assertEqual(ns.x, 1)
        self.assertEqual(ns.y, 12.1)
        self.assertEqual(ns.z, "z 1")
        self.assertIsNone(ns.c)
        self.assertEqual(ns.b, True)
        self.assertEqual(ns.a, [33])
        self.assertRegex(
            self.format_values(),
            "Command Line Args: \\s+ -x 1\n"
            "Config File \\(method arg\\):\n"
            "  y: \\s+ 12.1\n"
            "  b: \\s+ True\n"
            "  a: \\s+ 33\n"
            "  z: \\s+ z 1\n",
        )
        self.assertEqual(ns.m, [["1", "2", "3"], ["4", "5", "6"]])

        # -x is not a long arg so can't be set via config file
        self.assertParseArgsRaises(
            "the following arguments are required: -x, --y",
            args="",
            config_file_contents="-x 3",
        )
        self.assertParseArgsRaises(
            "invalid float value: 'abc'", args="-x 5", config_file_contents="y: abc"
        )
        self.assertParseArgsRaises(
            "the following arguments are required: --y",
            args="-x 5",
            config_file_contents="z: 1",
        )

        # test unknown config file args
        self.assertParseArgsRaises(
            "bla", args="-x 1 --y 2.3", config_file_contents="bla=3"
        )

        ns, args = self.parse_known(
            "-x 10 --y 3.8", config_file_contents="bla=3", env_vars={"bla": "2"}
        )
        self.assertListEqual(args, ["--bla=3"])

        self.initParser(ignore_unknown_config_file_keys=False)
        ns, args = self.parse_known(
            args="-x 1", config_file_contents="bla=3", env_vars={"bla": "2"}
        )
        self.assertEqual(set(args), {"--bla=3", "-x", "1"})

    def testQuotedArgumentValues(self):
        self.initParser()
        self.add_arg("-a")
        self.add_arg("--b")
        self.add_arg("-c")
        self.add_arg("--d")
        self.add_arg("-e")
        self.add_arg("-q")
        self.add_arg("--quotes")

        # sys.argv equivalent of -a="1"  --b "1" -c= --d "" -e=: -q "\"'" --quotes "\"'"
        ns = self.parse(
            args=[
                "-a=1",
                "--b",
                "1",
                "-c=",
                "--d",
                "",
                "-e=:",
                "-q",
                "\"'",
                "--quotes",
                "\"'",
            ],
            env_vars={},
            config_file_contents="",
        )

        self.assertEqual(ns.a, "1")
        self.assertEqual(ns.b, "1")
        self.assertEqual(ns.c, "")
        self.assertEqual(ns.d, "")
        self.assertEqual(ns.e, ":")
        self.assertEqual(ns.q, "\"'")
        self.assertEqual(ns.quotes, "\"'")

    def testQuotedConfigFileValues(self):
        self.initParser()
        self.add_arg("--a")
        self.add_arg("--b")
        self.add_arg("--c")

        ns = self.parse(
            args="",
            env_vars={},
            config_file_contents="""
        a="1"
        b=:
        c=
        """,
        )

        self.assertEqual(ns.a, "1")
        self.assertEqual(ns.b, ":")
        self.assertEqual(ns.c, "")

    def testBooleanValuesCanBeExpressedAsNumbers(self):
        self.initParser()
        store_true_env_var_name = "STORE_TRUE"
        self.add_arg(
            "--boolean_store_true", action="store_true", env_var=store_true_env_var_name
        )

        result_namespace = self.parse(
            "", config_file_contents="""boolean_store_true = 1"""
        )
        self.assertTrue(result_namespace.boolean_store_true)

        result_namespace = self.parse(
            "", config_file_contents="""boolean_store_true = 0"""
        )
        self.assertFalse(result_namespace.boolean_store_true)

        result_namespace = self.parse("", env_vars={store_true_env_var_name: "1"})
        self.assertTrue(result_namespace.boolean_store_true)

        result_namespace = self.parse("", env_vars={store_true_env_var_name: "0"})
        self.assertFalse(result_namespace.boolean_store_true)

        self.initParser()
        store_false_env_var_name = "STORE_FALSE"
        self.add_arg(
            "--boolean_store_false",
            action="store_false",
            env_var=store_false_env_var_name,
        )

        result_namespace = self.parse(
            "", config_file_contents="""boolean_store_false = 1"""
        )
        self.assertFalse(result_namespace.boolean_store_false)

        result_namespace = self.parse(
            "", config_file_contents="""boolean_store_false = 0"""
        )
        self.assertTrue(result_namespace.boolean_store_false)

        result_namespace = self.parse("", env_vars={store_false_env_var_name: "1"})
        self.assertFalse(result_namespace.boolean_store_false)

        result_namespace = self.parse("", env_vars={store_false_env_var_name: "0"})
        self.assertTrue(result_namespace.boolean_store_false)

    def testConfigOrEnvValueErrors(self):
        # flag args accept yes/no as well as true/false
        self.initParser()
        self.add_arg("--height", env_var="HEIGHT", required=True)
        self.add_arg("--do-it", dest="x", env_var="FLAG1", action="store_true")
        self.add_arg("--dont-do-it", dest="y", env_var="FLAG2", action="store_false")
        ns = self.parse("", env_vars={"HEIGHT": "tall", "FLAG1": "yes"})
        self.assertEqual(ns.height, "tall")
        self.assertEqual(ns.x, True)
        ns = self.parse("", env_vars={"HEIGHT": "tall", "FLAG2": "yes"})
        self.assertEqual(ns.y, False)
        ns = self.parse("", env_vars={"HEIGHT": "tall", "FLAG2": "no"})
        self.assertEqual(ns.y, True)

        # error should occur when flag arg is given a value
        self.initParser()
        self.add_arg("-v", "--verbose", env_var="VERBOSE", action="store_true")
        self.assertParseArgsRaises(
            "Unexpected value for VERBOSE: 'bla'. "
            "Expecting 'true', 'false', 'yes', 'no', 'on', 'off', '1' or '0'",
            args="",
            env_vars={"VERBOSE": "bla"},
        )
        ns = self.parse("", config_file_contents="verbose=true", env_vars={})
        self.assertEqual(ns.verbose, True)
        ns = self.parse("", config_file_contents="verbose", env_vars={})
        self.assertEqual(ns.verbose, True)
        ns = self.parse("", env_vars={"VERBOSE": "true"})
        self.assertEqual(ns.verbose, True)
        ns = self.parse("", env_vars={"VERBOSE": "false"})
        self.assertEqual(ns.verbose, False)
        ns = self.parse("", config_file_contents="--verbose", env_vars={})
        self.assertEqual(ns.verbose, True)

        # a list value in an append arg's env var sets each list element,
        # converted by the arg's type
        self.initParser()
        self.add_arg("-f", "--file", env_var="FILES", action="append", type=int)
        ns = self.parse("", env_vars={"FILES": "[1,2,3]"})
        self.assertEqual(ns.file, [1, 2, 3])

    def testValuesStartingWithDash(self):
        self.initParser()
        self.add_arg("--arg0")
        self.add_arg("--arg1", env_var="ARG1")
        self.add_arg("--arg2")
        self.add_arg("--arg3", action="append")
        self.add_arg("--arg4", action="append", env_var="ARG4")
        self.add_arg("--arg5", action="append")
        self.add_arg("--arg6")

        ns = self.parse(
            "--arg0=-foo --arg3=-foo --arg3=-bar --arg6=-test-more-dashes",
            config_file_contents="arg2: -foo\narg5: [-foo, -bar]",
            env_vars={"ARG1": "-foo", "ARG4": "[-foo, -bar]"},
        )
        self.assertEqual(ns.arg0, "-foo")
        self.assertEqual(ns.arg1, "-foo")
        self.assertEqual(ns.arg2, "-foo")
        self.assertEqual(ns.arg3, ["-foo", "-bar"])
        self.assertEqual(ns.arg4, ["-foo", "-bar"])
        self.assertEqual(ns.arg5, ["-foo", "-bar"])
        self.assertEqual(ns.arg6, "-test-more-dashes")

    def testPriorityKnown(self):
        self.initParser()
        self.add_arg("--arg", env_var="ARG")

        ns = self.parse(
            "--arg command_line_val",
            config_file_contents="arg: config_val",
            env_vars={"ARG": "env_val"},
        )
        self.assertEqual(ns.arg, "command_line_val")

        ns = self.parse(
            "--arg=command_line_val",
            config_file_contents="arg: config_val",
            env_vars={"ARG": "env_val"},
        )
        self.assertEqual(ns.arg, "command_line_val")

        ns = self.parse(
            "", config_file_contents="arg: config_val", env_vars={"ARG": "env_val"}
        )
        self.assertEqual(ns.arg, "env_val")

    def testPriorityUnknown(self):
        self.initParser()

        ns, args = self.parse_known(
            "--arg command_line_val",
            config_file_contents="arg: config_val",
            env_vars={"arg": "env_val"},
        )
        self.assertListEqual(args, ["--arg", "command_line_val"])

        ns, args = self.parse_known(
            "--arg=command_line_val",
            config_file_contents="arg: config_val",
        )
        self.assertListEqual(args, ["--arg=command_line_val"])

    def testAutoEnvVarPrefix(self):
        self.initParser(auto_env_var_prefix="TEST_")
        self.add_arg("-a", "--arg0", is_config_file_arg=True)
        self.add_arg("-b", "--arg1", is_write_out_config_file_arg=True)
        self.add_arg("-x", "--arg2", env_var="TEST2", type=int)
        self.add_arg("-y", "--arg3", action="append", type=int)
        self.add_arg("-z", "--arg4", required=True)
        self.add_arg("-w", "--arg4-more", required=True)
        ns = self.parse(
            "",
            env_vars={
                "TEST_ARG0": "0",
                "TEST_ARG1": "1",
                "TEST_ARG2": "2",
                "TEST2": "22",
                "TEST_ARG4": "arg4_value",
                "TEST_ARG4_MORE": "magic",
            },
        )
        self.assertIsNone(ns.arg0)
        self.assertIsNone(ns.arg1)
        self.assertEqual(ns.arg2, 22)
        self.assertEqual(ns.arg4, "arg4_value")
        self.assertEqual(ns.arg4_more, "magic")

    def testEnvVarLists(self):
        self.initParser()
        self.add_arg("-x", "--arg2", env_var="TEST2")
        self.add_arg("-y", "--arg3", env_var="TEST3", type=int)
        self.add_arg("-z", "--arg4", env_var="TEST4", nargs="+")
        self.add_arg("-u", "--arg5", env_var="TEST5", nargs="+", type=int)
        self.add_arg("--arg6", env_var="TEST6")
        self.add_arg("--arg7", env_var="TEST7", action="append")
        ns = self.parse(
            "",
            env_vars={
                "TEST2": "22",
                "TEST3": "22",
                "TEST4": "[Shell, someword, anotherword]",
                "TEST5": "[22, 99, 33]",
                "TEST6": "[value6.1, value6.2, value6.3]",
                "TEST7": "[value7.1, value7.2, value7.3]",
            },
        )
        self.assertEqual(ns.arg2, "22")
        self.assertEqual(ns.arg3, 22)
        self.assertEqual(ns.arg4, ["Shell", "someword", "anotherword"])
        self.assertEqual(ns.arg5, [22, 99, 33])
        self.assertEqual(ns.arg6, "[value6.1, value6.2, value6.3]")
        self.assertEqual(ns.arg7, ["value7.1", "value7.2", "value7.3"])

    def testPositionalAndEnvVarLists(self):
        self.initParser()
        self.add_arg("a")
        self.add_arg("-x", "--arg", env_var="TEST", nargs="+")

        ns = self.parse(
            "positional_value", env_vars={"TEST": "[Shell, someword, anotherword]"}
        )

        self.assertEqual(ns.arg, ["Shell", "someword", "anotherword"])
        self.assertEqual(ns.a, "positional_value")

    def testCounterCommandLine(self):
        self.initParser()
        self.add_arg("--verbose", "-v", action="count", default=0)

        ns = self.parse(args="-v -v -v", env_vars={})
        self.assertEqual(ns.verbose, 3)

        ns = self.parse(args="-vvv", env_vars={})
        self.assertEqual(ns.verbose, 3)

    def testCounterConfigFile(self):
        self.initParser()
        self.add_arg("--verbose", "-v", action="count", default=0)

        ns = self.parse(
            args="",
            env_vars={},
            config_file_contents="""
        verbose""",
        )
        self.assertEqual(ns.verbose, 1)

        ns = self.parse(
            args="",
            env_vars={},
            config_file_contents="""
        verbose=3""",
        )
        self.assertEqual(ns.verbose, 3)


class TestMisc(TestCase):
    # TODO test different action types with config file, env var

    """Test edge cases"""

    def setUp(self):
        self.initParser(args_for_setting_config_path=[])

    @mock.patch("argparse.ArgumentParser.__init__")
    def testKwrgsArePassedToArgParse(self, argparse_init):
        kwargs_for_argparse = {"allow_abbrev": False, "whatever_other_arg": "something"}

        parser = configargparse.ArgumentParser(
            add_config_file_help=False, **kwargs_for_argparse
        )

        argparse_init.assert_called_with(parser, **kwargs_for_argparse)

    def testGlobalInstances(self, name=None):
        p = configargparse.getArgumentParser(name, prog="prog", usage="test")
        self.assertEqual(p.usage, "test")
        self.assertEqual(p.prog, "prog")
        self.assertRaisesRegex(
            ValueError,
            "kwargs besides 'name' can only be passed in the first time",
            configargparse.getArgumentParser,
            name,
            prog="prog",
        )

        p2 = configargparse.getArgumentParser(name)
        self.assertEqual(p, p2)

    def testGlobalInstances_WithName(self):
        self.testGlobalInstances("name1")
        self.testGlobalInstances("name2")

    def testAddArguments_ArgValidation(self):
        self.assertRaises(ValueError, self.add_arg, "positional", env_var="bla")
        action = self.add_arg("positional")
        self.assertIsNotNone(action)
        self.assertEqual(action.dest, "positional")

    def testAddArguments_IsConfigFilePathArg(self):
        self.assertRaises(
            ValueError, self.add_arg, "c", action="store_false", is_config_file=True
        )

        self.add_arg("-c", "--config", is_config_file=True)
        self.add_arg("--x", required=True)

        # verify parsing from config file
        config_file_path = self.make_temp_file_holding_contents("x=bla")

        ns = self.parse(args="-c %s" % config_file_path)
        self.assertEqual(ns.x, "bla")

    def testConstructor_ConfigFileArgs(self):
        # Test constructor args:
        #   args_for_setting_config_path
        #   config_arg_is_required
        #   config_arg_help_message
        default_config_file_path = self.make_temp_file_holding_contents("genome=hg19")

        self.initParser(
            args_for_setting_config_path=["-c", "--config"],
            config_arg_is_required=True,
            config_arg_help_message="my config file",
            default_config_files=[default_config_file_path],
        )
        self.add_arg("--genome", help="Path to genome file", required=True)
        self.assertParseArgsRaises("arguments are required: -c/--config", args="")

        override_config_file_path = self.make_temp_file_holding_contents()
        ns = self.parse("-c " + override_config_file_path)
        self.assertEqual(ns.genome, "hg19")

        # the -c config file should override default config file values
        with open(override_config_file_path, "w") as f:
            f.write("genome=hg20")
        ns = self.parse("-c " + override_config_file_path)
        self.assertEqual(ns.genome, "hg20")

        short_c = SHORT_OPT_METAVAR.format(metavar="CONFIG_FILE")
        self.assertRegex(
            self.format_help(),
            r"usage: .*\s+\[-h\] -c CONFIG_FILE --genome GENOME\n\n"
            r"%s:\n"
            r"  -h, --help\s+ show this help message and exit\n"
            rf"  -c{short_c}, --config CONFIG_FILE\s+ my config file\n"
            r"  --genome GENOME\s+ Path to genome file\n\n" % OPTIONAL_ARGS_STRING
            + 5 * r"(.+\s*)",
        )

        # just run print_values() to make sure it completes and returns None
        output = StringIO()
        self.assertIsNone(self.parser.print_values(file=output))
        self.assertIn("Command Line Args:", output.getvalue())

        # test ignore_unknown_config_file_keys=False
        self.initParser(ignore_unknown_config_file_keys=False)
        self.assertRaisesRegex(
            argparse.ArgumentError,
            "unrecognized arguments",
            self.parse,
            args=[],
            config_file_contents="arg1 = 3",
        )
        ns, args = self.parse_known(args=[], config_file_contents="arg1 = 3")
        self.assertEqual(args, ["--arg1=3"])

        # test ignore_unknown_config_file_keys=True
        self.initParser(ignore_unknown_config_file_keys=True)
        # parses without an "unrecognized arguments" error
        self.parse(args="", config_file_contents="arg1 = 3")
        ns, args = self.parse_known(args=[], config_file_contents="arg1 = 3")
        self.assertEqual(args, [])

    def test_AbbrevConfigFileArgs(self):
        """Tests that abbreviated values don't get pulled from config file."""
        config_file_path = self.make_temp_file_holding_contents(
            "a2a = 0.5\na3a = 0.5\n"
        )

        self.initParser()

        self.add_arg(
            "-c",
            "--config_file",
            required=False,
            is_config_file=True,
            help="config file path",
        )

        self.add_arg("--hello", type=int, required=False)

        command = "-c {} --hello 2".format(config_file_path)

        known, unknown = self.parse_known(command)

        self.assertListEqual(unknown, ["--a2a=0.5", "--a3a=0.5"])

    def test_FormatHelp(self):
        self.initParser(
            args_for_setting_config_path=["-c", "--config"],
            config_arg_is_required=True,
            config_arg_help_message="my config file",
            default_config_files=["~/.myconfig"],
            args_for_writing_out_config_file=["-w", "--write-config"],
        )
        self.add_arg("--arg1", help="Arg1 help text", required=True)
        self.add_arg("--flag", help="Flag help text", action="store_true")

        short_c = SHORT_OPT_METAVAR.format(metavar="CONFIG_FILE")
        short_w = SHORT_OPT_METAVAR.format(metavar="CONFIG_OUTPUT_PATH")
        self.assertRegex(
            self.format_help(),
            r"usage: .*\s+\[-h\] -c CONFIG_FILE\s+"
            r"\[-w CONFIG_OUTPUT_PATH\]\s* --arg1\s+ARG1\s*\[--flag\]\s*"
            "%s:\\s*"
            "-h, --help \\s* show this help message and exit "
            rf"-c{short_c}, --config CONFIG_FILE\s+my config file "
            rf"-w{short_w}, --write-config CONFIG_OUTPUT_PATH takes "
            r"the current command line args and writes them "
            r"out to a config file at the given path, then exits "
            r"--arg1 ARG1 Arg1 help text "
            r"--flag Flag help text "
            "Args that start with '--' can also be set in a "
            r"config file \(~/.myconfig or specified via -c\). "
            r"Config file syntax allows: key=value, flag=true, stuff=\[a,b,c\] "
            r"\(for details, see syntax at https://goo.gl/R74nmi\). "
            r"In general, command-line values override config file values "
            r"which override defaults. ".replace(" ", r"\s*") % OPTIONAL_ARGS_STRING,
        )

    def test_FormatHelpProg(self):
        self.initParser("format_help_prog")
        self.assertRegex(self.format_help(), "usage: format_help_prog .*")

    def test_FormatHelpProgLib(self):
        parser = argparse.ArgumentParser("format_help_prog")
        self.assertRegex(parser.format_help(), "usage: format_help_prog .*")

    def assert_write_out_config_file_arg_writes_expected_contents(
        self,
        write_out_config_file_arg,
        other_command_line_args,
        expected_config_file_contents,
        **parse_kwargs,
    ):
        """Parse args that ask to write out a config file, then check the file.

        Also checks that pointing the write-out arg at a path that can't be
        written to raises a ValueError. self.parser must already be set up
        with write_out_config_file_arg in its args_for_writing_out_config_file.

        Args:
            write_out_config_file_arg: The arg that writes out a config file,
                e.g. "-w".
            other_command_line_args: The rest of the command line, as one
                string.
            expected_config_file_contents: What the written file should hold,
                apart from leading and trailing whitespace.
            **parse_kwargs: Passed on to the parse that writes the file, e.g.
                env_vars or config_file_contents.
        """
        config_file_path = self.make_temp_file_holding_contents()
        command_line_args = "%s %s %s" % (
            write_out_config_file_arg,
            config_file_path,
            other_command_line_args,
        )

        self.assertFalse(self.parser._exit_method_called)
        self.parse(command_line_args, **parse_kwargs)
        self.assertTrue(self.parser._exit_method_called)

        with open(config_file_path) as f:
            self.assertEqual(f.read().strip(), expected_config_file_contents.strip())
        self.assertRaisesRegex(
            ValueError,
            "Couldn't open / for writing:",
            self.parse,
            args="%s %s /" % (command_line_args, write_out_config_file_arg),
        )

    def testConstructor_WriteOutConfigFileArgs(self):
        """Test constructor args args_for_writing_out_config_file and
        write_out_config_file_arg_help_message, with a short and a long arg.

        There was a bug where the long version of the
        args_for_writing_out_config_file was being dumped into the resultant
        output config file
        """
        for write_out_config_file_arg in ("-w", "--write-config"):
            with self.subTest(write_out_config_file_arg=write_out_config_file_arg):
                self.initParser(
                    args_for_writing_out_config_file=[write_out_config_file_arg],
                    write_out_config_file_arg_help_message="write config",
                )

                self.add_arg("-not-config-file-settable")
                self.add_arg("--config-file-settable-arg", type=int)
                self.add_arg("--config-file-settable-arg2", type=int, default=3)
                self.add_arg("--config-file-settable-flag", action="store_true")
                self.add_arg("--config-file-settable-custom")
                self.add_arg("-l", "--config-file-settable-list", action="append")

                self.assertRegex(
                    self.format_help(),
                    write_out_config_file_arg + r" CONFIG_OUTPUT_PATH\s+write config",
                )
                self.assert_write_out_config_file_arg_writes_expected_contents(
                    write_out_config_file_arg,
                    other_command_line_args=(
                        "-not-config-file-settable some_value "
                        "--config-file-settable-arg 1 "
                        "--config-file-settable-flag "
                        "--config-file-settable-custom custom_value "
                        "-l a -l b -l c -l d"
                    ),
                    expected_config_file_contents=(
                        "config-file-settable-arg = 1\n"
                        "config-file-settable-flag = true\n"
                        "config-file-settable-custom = custom_value\n"
                        'config-file-settable-list = ["a", "b", "c", "d"]\n'
                        "config-file-settable-arg2 = 3\n"
                    ),
                )

    def testConstructor_WriteOutConfigFileArgs2(self):
        # Test constructor args:
        #   args_for_writing_out_config_file
        #   write_out_config_file_arg_help_message
        self.initParser(
            args_for_writing_out_config_file=["-w"],
            write_out_config_file_arg_help_message="write config",
        )

        self.add_arg("-not-config-file-settable")
        self.add_arg("-a", "--arg1", type=int, env_var="ARG1")
        self.add_arg("-b", "--arg2", type=int, default=3)
        self.add_arg("-c", "--arg3")
        self.add_arg("-d", "--arg4")
        self.add_arg("-e", "--arg5")
        self.add_arg(
            "--config-file-settable-flag", action="store_true", env_var="FLAG_ARG"
        )
        self.add_arg("-l", "--config-file-settable-list", action="append")

        self.assertRegex(self.format_help(), r"-w CONFIG_OUTPUT_PATH\s+write config")
        self.assert_write_out_config_file_arg_writes_expected_contents(
            "-w",
            other_command_line_args=(
                "-not-config-file-settable some_value -l a -l b -l c -l d"
            ),
            expected_config_file_contents=(
                'config-file-settable-list = ["a", "b", "c", "d"]\n'
                "arg1 = 10\n"
                "config-file-settable-flag = true\n"
                "arg3 = bla3\n"
                "arg4 = bla4\n"
                "arg2 = 3\n"
            ),
            env_vars={"ARG1": "10", "FLAG_ARG": "true", "SOME_OTHER_ENV_VAR": "2"},
            config_file_contents="arg3 = bla3\narg4 = bla4",
        )

    def testWriteOutConfigFileArgs_ListValuesReadBackUnchanged(self):
        """A written out config file must give back the same list values.

        Lists used to be written as [a,b, c], which split an element with a
        comma in it, and garbled the lists of lists that append with nargs="+"
        stores.
        """
        config_file_path = self.make_temp_file_holding_contents()

        def init_parser():
            self.initParser(
                args_for_setting_config_path=["-c"],
                args_for_writing_out_config_file=["-w"],
            )
            self.add_arg("--appended", action="append")
            self.add_arg("--appended-groups", action="append", nargs="+")
            self.add_arg("--several", nargs="+")

        command_line_args = [
            "--appended",
            "a,b",
            "--appended",
            "café",
            "--appended-groups",
            "a,b",
            "c",
            "--appended-groups",
            "d",
            "--several",
            "x, y",
            "line1\nline2",
            "echo hi # note",
        ]
        init_parser()
        self.parse(command_line_args + ["-w", config_file_path])
        self.assertTrue(self.parser._exit_method_called)

        init_parser()
        ns = self.parse(["-c", config_file_path])
        self.assertEqual(ns.appended, ["a,b", "café"])
        self.assertEqual(ns.appended_groups, [["a,b", "c"], ["d"]])
        self.assertEqual(ns.several, ["x, y", "line1\nline2", "echo hi # note"])

    def testWriteOutConfigFileArg_CantBeSetFromAConfigFile(self):
        """A config file must not be able to trigger writing out a config file.

        Doing so overwrites the given path and then exits, so a config file that
        an attacker controls could otherwise destroy an arbitrary file without
        the user passing any command line args at all.
        """
        # argparse resolves an unambiguous abbreviation of a long option, so an
        # abbreviated key reaches the same arg, and rejecting only the exact
        # key would leave the same file overwrite open
        for key in ("write-config", "write-conf"):
            with self.subTest(key=key):
                victim_path = self.make_file_holding_original_contents()

                self.initParser(
                    args_for_writing_out_config_file=["-w", "--write-config"]
                )
                self.add_arg("--name", default="world")

                self.assertParseArgsRaises(
                    "%s can only be set on the command line" % key,
                    args=[],
                    config_file_contents="name = attacker\n%s = %s"
                    % (key, victim_path),
                )
                self.assertFalse(self.parser._exit_method_called)

                self.assert_file_still_holds_original_contents(victim_path)

    def testWriteOutConfigFileArg_ConfigFileCantRedirectTheUsersOwnPath(self):
        """A config file must not be able to change where the user asked to write.

        Config file args are inserted just before a '--' separator, which here
        is after the user's own -w, so a config file entry could otherwise win
        the last-one-wins store and send the output to a path of its choosing
        instead of the one the user gave.
        """
        users_path = self.make_unused_path_in_a_new_temp_dir()

        victim_path = self.make_file_holding_original_contents()

        self.initParser(args_for_writing_out_config_file=["-w", "--write-config"])
        self.add_arg("--name", default="world")
        self.add_arg("rest", nargs="*")

        self.assertParseArgsRaises(
            "write-config can only be set on the command line",
            args=["-w", users_path, "--", "extra"],
            config_file_contents="write-config = %s" % victim_path,
        )

        self.assert_file_still_holds_original_contents(victim_path)
        self.assertFalse(os.path.exists(users_path))

    def testWriteOutConfigFileArg_UnrelatedArgsDontMakeItReachable(self):
        """An unrelated arg that merely looks similar must not enable the write.

        The config file is the only thing naming the write-out arg here, so it
        must be rejected no matter what else the command line contains.
        """
        victim_path = self.make_file_holding_original_contents()

        self.initParser(args_for_writing_out_config_file=["-w", "--write-config"])
        self.add_arg("-week", action="store_true")
        self.add_arg("--name", default="world")

        self.assertParseArgsRaises(
            "write-config can only be set on the command line",
            args=["-week", "--name", "bob"],
            config_file_contents="write-config = %s" % victim_path,
        )

        self.assert_file_still_holds_original_contents(victim_path)

    def testWriteOutConfigFileArg_CantBeSetFromAnEnvVarValue(self):
        victim_path = self.make_file_holding_original_contents()

        self.initParser(args_for_writing_out_config_file=["-w", "--write-config"])
        self.add_arg("--files", nargs="*", env_var="FILES")

        self.assertParseArgsRaises(
            "FILES can't have a list element that starts with '-'",
            args=[],
            env_vars={"FILES": json.dumps(["--write-config", victim_path])},
        )
        self.assertFalse(self.parser._exit_method_called)

        self.assert_file_still_holds_original_contents(victim_path)

    def testWriteOutConfigFileArg_CantBeSetFromAConfigFileValueInAnyForm(self):
        """No form of the write-out arg can be set from a config file list value.

        A list value becomes several command line args, so a value can name an
        arg just as a key can. Each form here would be read as an option, and a
        list element that argparse would read as an option is rejected outright
        (unlike a negative number or a lone '-', see
        tests/test_list_elements_are_read_as_values.py), before any write-out
        check runs, so none of them can reach the write-out arg.
        testWriteOutConfigFileArg_RejectionCoversEveryFormArgparseAccepts
        covers how the write-out check itself matches each form.
        """
        for value_form in (
            "--write-config, %s",
            "-w%s",
            "-vw%s",
            "--write-config=%s",
            "--write-conf",
            "-v=w",
        ):
            victim_path = self.make_file_holding_original_contents()

            self.initParser(args_for_writing_out_config_file=["-w", "--write-config"])
            self.add_arg("-v", action="store_true")
            self.add_arg("--files", nargs="+")

            self.assertParseArgsRaises(
                "files can't have a list element that starts with '-'",
                args=[],
                config_file_contents="files = [a, %s]"
                % (value_form.replace("%s", victim_path)),
            )
            self.assertFalse(self.parser._exit_method_called, msg=value_form)

            self.assert_file_still_holds_original_contents(victim_path, msg=value_form)

    def testWriteOutConfigFileArg_RejectionCoversEveryFormArgparseAccepts(self):
        """Whatever argparse binds to the write-out arg has to be rejected.

        The rejection has to recognize an arg the same way argparse does, so
        this asks argparse itself: for each arg string, if a plain
        argparse.ArgumentParser with the same options sets the write-out dest
        from it, configargparse must refuse to take that arg from a config file.
        """
        arg_strings = [
            "-w",
            "-wPATH",
            "-w=PATH",
            "-vw",
            "-vwPATH",
            "-xvw",
            "-xvwPATH",
            "--write-config",
            "--write-config=PATH",
            "--write-conf",
            "--write",
            "--writ",
            "--name",
            "--nam",
            "-v",
            "-x",
            "-n",
            "-nPATH",
            "-nw",
            "-5",
            "--",
            "--=PATH",
            "--=",
            "-=PATH",
            "-",
            "",
            "PATH",
            "-write-config",
            "-write-config=PATH",
            "-write-conf",
            "-wr",
            "-wr=PATH",
            "-v=w",
            "-force=w",
        ]

        for allow_abbrev in (True, False):
            for write_out_option_strings in (
                ["-w", "--write-config"],
                ["--write-config"],
                ["-w"],
                ["-write-config"],
            ):
                parser = configargparse.ArgumentParser(
                    add_help=False,
                    allow_abbrev=allow_abbrev,
                    args_for_writing_out_config_file=write_out_option_strings,
                )
                reference = argparse.ArgumentParser(
                    add_help=False, allow_abbrev=allow_abbrev
                )
                reference.add_argument(
                    *write_out_option_strings, dest="write_out_config_file_to_this_path"
                )
                for p in (parser, reference):
                    p.add_argument("-v", action="store_true")
                    p.add_argument("-x", action="store_true")
                    p.add_argument("-force", action="store_true")
                    p.add_argument("--write", action="store_true")
                    p.add_argument("--name")
                    p.add_argument("-n", dest="nn")

                for arg_string in arg_strings:
                    argparse_sets_it = False
                    for trailing in ([], ["PATH2"]):
                        try:
                            with captured_output():
                                ns, _ = reference.parse_known_args(
                                    [arg_string] + trailing
                                )
                        except SystemExit:
                            continue
                        if ns.write_out_config_file_to_this_path is not None:
                            argparse_sets_it = True

                    if argparse_sets_it:
                        with self.subTest(
                            arg_string=arg_string,
                            allow_abbrev=allow_abbrev,
                            write_out=write_out_option_strings,
                        ):
                            self.assertTrue(
                                parser._names_a_write_out_config_file_arg(
                                    arg_string, [parser]
                                )
                            )

    def testWriteOutConfigFileArg_ArgSharingItsPrefixStillWorks(self):
        # argparse resolves an exact match straight to that arg, so a config key
        # naming a real arg can't reach the write-out one and must be accepted
        # even when the write-out arg's name starts with it
        self.initParser(args_for_writing_out_config_file=["-w", "--write-config"])
        self.add_arg("--write", action="store_true")
        self.add_arg("--name", default="world")

        ns = self.parse(args=[], config_file_contents="write = true\nname = bob")
        self.assertTrue(ns.write)
        self.assertEqual(ns.name, "bob")
        self.assertFalse(self.parser._exit_method_called)

    def testWriteOutConfigFileArg_UnknownConfigKeyStillReachesExtras(self):
        # a key that only looks like an abbreviation of the write-out arg when
        # abbreviations are enabled must stay an ordinary unknown key when they
        # aren't, rather than becoming an error
        self.initParser(
            allow_abbrev=False,
            args_for_writing_out_config_file=["-w", "--write-out-config-file"],
        )
        self.add_arg("--name", default="world")

        ns, extras = self.parse_known(args=[], config_file_contents="write = something")
        self.assertEqual(ns.name, "world")
        self.assertEqual(extras, ["--write=something"])
        self.assertFalse(self.parser._exit_method_called)

    @skip_unless_pyyaml_is_installed
    def testWriteOutConfigFileArg_ConfigFileKeysThatArentStringsStillWork(self):
        # YAML maps 'on' to a bool, and ignore_unknown_config_file_keys=True is
        # there to tolerate exactly that kind of foreign key
        self.initParser(
            args_for_writing_out_config_file=["-w", "--write-config"],
            config_file_parser_class=configargparse.YAMLConfigFileParser,
            ignore_unknown_config_file_keys=True,
        )
        self.add_arg("--name")

        ns = self.parse(args=[], config_file_contents="name: bob\non: something\n")
        self.assertEqual(ns.name, "bob")
        self.assertFalse(self.parser._exit_method_called)

    def testWriteOutConfigFileArg_EmptyConfigValueIsntAnArgsFile(self):
        # an empty value isn't a reference to a file of args, even though every
        # string starts with one
        self.initParser(
            args_for_writing_out_config_file=["-w", "--write-config"],
            fromfile_prefix_chars="@",
        )
        self.add_arg("--files", nargs="+")

        ns = self.parse(args=[], config_file_contents='files = ["a", ""]')
        self.assertEqual(ns.files, ["a", ""])
        self.assertFalse(self.parser._exit_method_called)

    def testWriteOutConfigFileArg_ConfigValuesThatLookLikeOptionsStillWork(self):
        # a plain value is passed as --key=value, so it can't be read as an
        # option: a lone '-' or a negative number still parses
        self.initParser(args_for_writing_out_config_file=["-w", "--write-config"])
        self.add_arg("--dash-value")
        self.add_arg("--num", type=int)

        ns = self.parse(args=[], config_file_contents="dash-value = -\nnum = -5")
        self.assertEqual(ns.dash_value, "-")
        self.assertEqual(ns.num, -5)
        self.assertFalse(self.parser._exit_method_called)

    def testWriteOutConfigFileArg_CantBeSetFromAConfigFileByPrefixAlone(self):
        # '--' on its own is argparse's end-of-options separator, but '--=path'
        # is an abbreviation that matches every long option
        victim_path = self.make_file_holding_original_contents()

        self.initParser(
            add_help=False, args_for_writing_out_config_file=["-w", "--write-config"]
        )
        self.add_arg("-n")

        self.assertParseArgsRaises(
            "can only be set on the command line",
            args=[],
            config_file_contents="- = %s" % victim_path,
        )

        self.assert_file_still_holds_original_contents(victim_path)

    def testWriteOutConfigFileArg_AnArgSharingItsDestCantChooseThePath(self):
        """Only the write-out arg itself decides where the config file goes.

        Another arg can share its dest and put a value of its own in the
        namespace: on the same parser, through parents=, or from a subparser,
        which argparse parses into the same namespace. None of them may choose
        the path, whether or not the command line asks for a config file.
        """

        def sharing_the_dest_on_one_parser():
            self.initParser(args_for_writing_out_config_file=["-w"])
            self.add_arg("--out", dest="write_out_config_file_to_this_path")
            self.add_arg("rest", nargs="*")
            return "out", ["--", "extra"]

        def sharing_the_dest_through_parents():
            base = configargparse.ArgParser(add_help=False)
            base.add_argument("-o", "--output")
            write_out = configargparse.ArgParser(add_help=False)
            write_out.add_argument(
                "-w", dest="output", is_write_out_config_file_arg=True
            )
            self.initParser(parents=[base, write_out])
            self.add_arg("rest", nargs="*")
            return "output", ["--", "extra"]

        victim_path = self.make_file_holding_original_contents()

        for build_parser in (
            sharing_the_dest_on_one_parser,
            sharing_the_dest_through_parents,
        ):
            # the rest of the command line puts the config file's args after the
            # write-out arg, which is where a value from one could otherwise win
            # the last-one-wins store
            config_key, rest_of_command_line = build_parser()
            config_file_contents = "%s = %s" % (config_key, victim_path)

            # with nothing on the command line asking for it, nothing is written
            self.parse(
                args=rest_of_command_line, config_file_contents=config_file_contents
            )
            self.assertFalse(self.parser._exit_method_called, msg=config_key)
            self.assert_file_still_holds_original_contents(victim_path, msg=config_key)

            # and when it is asked for, it goes where the command line said
            users_path = self.make_unused_path_in_a_new_temp_dir()

            build_parser()
            self.parse(
                args=["-w", users_path] + rest_of_command_line,
                config_file_contents=config_file_contents,
            )
            self.assertTrue(self.parser._exit_method_called, msg=config_key)

            self.assert_file_still_holds_original_contents(victim_path, msg=config_key)
            self.assertTrue(os.path.exists(users_path), msg=config_key)

    def testWriteOutConfigFileArg_ASubparserArgSharingItsDestCantChooseThePath(self):
        """A subcommand parses into the same namespace, and loads its own env vars.

        So an arg on a subcommand can share a write-out arg's dest and be set
        from the environment without the parent ever seeing it. It still must
        not decide where the config file gets written.
        """
        victim_path = self.make_file_holding_original_contents()

        users_path = self.make_unused_path_in_a_new_temp_dir()

        self.initParser(args_for_writing_out_config_file=["-w"])
        self.add_arg("--name", default="world")
        subparsers = self.parser.add_subparsers(dest="cmd")
        subparsers.add_parser("go").add_argument(
            "--out",
            dest="write_out_config_file_to_this_path",
            env_var="WRITE_OUT_CONFIG_TO",
        )

        with mock.patch.dict(os.environ, {"WRITE_OUT_CONFIG_TO": victim_path}):
            self.parse(args=["-w", users_path, "go"])
        self.assertTrue(self.parser._exit_method_called)

        self.assert_file_still_holds_original_contents(victim_path)
        self.assertTrue(os.path.exists(users_path))

    def testWriteOutConfigFileArg_ConfigFileCantReadArgsFromASubparsersFile(self):
        # only the subparser has fromfile_prefix_chars here. Config file args go
        # before the subcommand, but the subcommand can't always be told from
        # the args (see testWriteOutConfigFileArg_SubcommandsAreTakenTogether),
        # so every subcommand's args-file characters are checked
        victim_path = self.make_file_holding_original_contents()
        args_file_path = self.make_temp_file_holding_contents("-w\n%s\n" % victim_path)

        self.initParser()
        self.add_arg("--extra", nargs="*")
        subparsers = self.parser.add_subparsers(dest="cmd")
        subparsers.add_parser(
            "go", fromfile_prefix_chars="@", args_for_writing_out_config_file=["-w"]
        )

        self.assertParseArgsRaises(
            "would read command line args from a file",
            args=["go", "--"],
            config_file_contents="extra = [@%s]" % args_file_path,
        )

    def testWriteOutConfigFileArg_CantBeSetFromAConfigFileOnASubparser(self):
        """A subcommand's write-out arg can't be set from the parent's config file.

        Config file args go before the subcommand, but which subcommand runs
        can't always be told from the args (a file of args can name it, see
        testWriteOutConfigFileArg_CantBeSetWhenAnArgsFileCouldPickTheSubcommand),
        so they are checked against every subcommand's write-out args too.
        """
        victim_path = self.make_file_holding_original_contents()

        self.initParser()
        subparsers = self.parser.add_subparsers(dest="cmd")
        subparsers.add_parser(
            "go", args_for_writing_out_config_file=["-w", "--write-config"]
        )

        # the parent has no such arg, so the key becomes --write-config=<path>,
        # which the check against the subcommand's write-out args turns down
        self.assertParseArgsRaises(
            "can only be set on the command line",
            args=["go", "--"],
            config_file_contents="write-config = %s" % victim_path,
        )

    def testWriteOutConfigFileArg_OnASubparserStillWorks(self):
        config_file_path = self.make_temp_file_holding_contents()

        self.initParser()
        subparsers = self.parser.add_subparsers(dest="cmd")
        sub = subparsers.add_parser(
            "go", args_for_writing_out_config_file=["-w", "--write-config"]
        )
        sub.add_argument("--name")

        # the subparser is a parser of its own, so it's the one that exits here
        with captured_output():
            self.assertRaises(
                SystemExit, self.parse, ["go", "-w", config_file_path, "--name", "bob"]
            )

        with open(config_file_path) as f:
            self.assertEqual(f.read(), "name = bob\n")

    def testWriteOutConfigFileArg_OnASubcommandRestrictsTheWholeParser(self):
        """A write-out arg on any subcommand rules that name out everywhere.

        Which subcommand runs can't be told from the args: an option's value
        reads exactly like a subcommand name, and the args built from a config
        file can name a subcommand themselves. So every subcommand is taken
        into account, whatever the command line looks like. The cost is an
        error on a key named after a write-out arg on a subcommand that wasn't
        going to run, which is the safe direction to be wrong in.
        """
        for args in ([], ["deploy"]):
            self.initParser()
            self.add_arg("--output", env_var="APP_OUTPUT")
            subparsers = self.parser.add_subparsers(dest="cmd")
            subparsers.add_parser(
                "deploy", args_for_writing_out_config_file=["--output-config"]
            )

            # '--output' is an abbreviation of the subcommand's '--output-config'
            self.assertParseArgsRaises(
                "can only be set on the command line",
                args=args,
                config_file_contents="output = /tmp/report.txt",
            )
            self.assertParseArgsRaises(
                "can only be set on the command line",
                args=args,
                env_vars={"APP_OUTPUT": "/tmp/report.txt"},
            )

        # a name that doesn't reach it is unaffected
        self.initParser()
        self.add_arg("--other")
        subparsers = self.parser.add_subparsers(dest="cmd")
        subparsers.add_parser(
            "deploy", args_for_writing_out_config_file=["--output-config"]
        )
        ns = self.parse(args=[], config_file_contents="other = /tmp/report.txt")
        self.assertEqual(ns.other, "/tmp/report.txt")
        self.assertFalse(self.parser._exit_method_called)

    def testWriteOutConfigFileArg_SubcommandArgsFileCharOnlyMattersWhenAWriteOutArgIsBelowIt(
        self,
    ):
        """An args file only feeds the parser that expands it, and its own subcommands.

        So a value starting with a subcommand's args-file character is refused
        when a write-out arg sits at or below that subcommand, and left alone
        when it can't reach one. A value starting with the parser's own
        args-file character is always refused (see
        tests/test_list_elements_are_read_as_values.py).
        """
        # the subcommand reads args files, but no write-out arg lives under it
        self.initParser(args_for_writing_out_config_file=["-w"])
        self.add_arg("--notify", nargs="+")
        subparsers = self.parser.add_subparsers(dest="cmd")
        subparsers.add_parser("deploy", fromfile_prefix_chars="@")

        ns = self.parse(args=[], config_file_contents='notify = ["@oncall", "b"]')
        self.assertEqual(ns.notify, ["@oncall", "b"])
        self.assertFalse(self.parser._exit_method_called)

        # and now one does
        self.initParser()
        self.add_arg("--notify", nargs="+")
        subparsers = self.parser.add_subparsers(dest="cmd")
        subparsers.add_parser(
            "deploy", fromfile_prefix_chars="@", args_for_writing_out_config_file=["-w"]
        )

        self.assertParseArgsRaises(
            "would read command line args from a file",
            args=[],
            config_file_contents='notify = ["@oncall", "b"]',
        )

    def testWriteOutConfigFileArg_CantBeSetWhenAnArgsFileCouldPickTheSubcommand(self):
        """An @file on the command line can name the subcommand itself.

        argparse expands it after the config file's args have been checked, so
        which subparser will run isn't known yet and every one of them has to
        be taken into account.
        """
        victim_path = self.make_file_holding_original_contents()
        args_file_path = self.make_temp_file_holding_contents("go\n")

        self.initParser(fromfile_prefix_chars="@")
        self.add_arg("--write-config")
        subparsers = self.parser.add_subparsers(dest="cmd")
        subparsers.add_parser("go", args_for_writing_out_config_file=["--write-config"])

        self.assertParseArgsRaises(
            "can only be set on the command line",
            args=["@" + args_file_path],
            config_file_contents="write-config = %s" % victim_path,
        )

        self.assert_file_still_holds_original_contents(victim_path)

    def testWriteOutConfigFileArg_CantBeSetOnASubparserBehindAPlainArgparseOne(self):
        # a subparser doesn't have to be one of ours to have one of ours under
        # it, so the search for write-out args walks through it either way
        victim_path = self.make_file_holding_original_contents()

        self.initParser()
        self.add_arg("--pair", nargs="+")
        outer = self.parser.add_subparsers(
            dest="l1", parser_class=argparse.ArgumentParser
        ).add_parser("outer")
        outer.add_subparsers(
            dest="l2", parser_class=configargparse.ArgumentParser
        ).add_parser("inner", args_for_writing_out_config_file=["-w"])

        self.assertParseArgsRaises(
            "pair can't have a list element that starts with '-'",
            args=["outer", "inner", "--"],
            config_file_contents="pair = [a, b, -w, %s]" % victim_path,
        )
        # the list element is rejected before the write-out check runs, so check
        # directly that the write-out check finds the inner subcommand's arg
        self.assertTrue(
            self.parser._names_a_write_out_config_file_arg(
                "-w", configargparse._subparsers_of(self.parser)
            )
        )

        self.assert_file_still_holds_original_contents(victim_path)

    def testWriteOutConfigFileArg_SubcommandsAreTakenTogether(self):
        """Every subcommand is considered, whatever the command line looks like.

        Which one will run can't be told from the args alone, since a value
        belonging to an earlier option reads exactly like a subcommand name, so
        a write-out arg on any of them turns the key down. That costs an error
        on a key named after another subcommand's write-out arg, which is the
        safe direction to be wrong in.
        """
        victim_path = self.make_file_holding_original_contents()

        def build():
            self.initParser()
            self.add_arg("--mode")
            subparsers = self.parser.add_subparsers(dest="cmd")
            build_parser = subparsers.add_parser("build")
            build_parser.add_argument("rest", nargs="*")
            subparsers.add_parser(
                "run", args_for_writing_out_config_file=["--write-config"]
            )

        # the value of --mode reads as the name of a subcommand that has no
        # write-out arg, while the one that does is the one actually named
        build()
        self.assertParseArgsRaises(
            "can only be set on the command line",
            args=["--mode", "build", "run", "--"],
            config_file_contents="write-config = %s" % victim_path,
        )
        self.assert_file_still_holds_original_contents(victim_path)

        # even when the args all stay with the parent and a subcommand without
        # a write-out arg
        build()
        self.assertParseArgsRaises(
            "can only be set on the command line",
            args=["--mode", "x", "build"],
            config_file_contents="write-config = %s" % victim_path,
        )
        self.assert_file_still_holds_original_contents(victim_path)

        # while a key that doesn't name any subcommand's write-out arg is
        # unaffected
        build()
        ns = self.parse(args=["--mode", "x", "build"], config_file_contents="mode = y")
        self.assertEqual(ns.cmd, "build")
        self.assertFalse(self.parser._exit_method_called)

    def testWriteOutConfigFileArg_AnyPlainStoreActionSpellingWorks(self):
        # a program's own store action keeps whatever it does, and records too
        class ExpandUser(argparse._StoreAction):
            def __call__(self_inner, parser, namespace, values, option_string=None):
                argparse._StoreAction.__call__(
                    self_inner,
                    parser,
                    namespace,
                    os.path.expanduser(values),
                    option_string,
                )

        # a store action reached through the registry is wrapped like any other
        class MyStore(argparse._StoreAction):
            pass

        # argparse takes several spellings for the same store action, including
        # a registered name and any callable that returns an action, and all of
        # them have to end up recording the path
        for action in (
            "store",
            None,
            argparse._StoreAction,
            ExpandUser,
            "mystore",
            functools.partial(argparse._StoreAction),
        ):
            with self.subTest(action=action):
                config_file_path = self.make_temp_file_holding_contents()

                self.initParser()
                self.parser.register("action", "mystore", MyStore)
                self.add_arg("-w", is_write_out_config_file_arg=True, action=action)
                self.add_arg("--name")

                self.parse(["-w", config_file_path, "--name", "bob"])
                self.assertTrue(self.parser._exit_method_called)
                with open(config_file_path) as f:
                    self.assertEqual(f.read(), "name = bob\n")

    def testWriteOutConfigFileArg_EmptyPathIsStillReported(self):
        # an empty path is a path the user asked for, so it fails the way any
        # other unwritable path does rather than being quietly skipped
        self.initParser(args_for_writing_out_config_file=["-w"])
        self.add_arg("--name")

        self.assertRaisesRegex(
            ValueError,
            "Couldn't open  for writing",
            self.parse,
            ["--name", "bob", "-w", ""],
        )

    def testWriteOutConfigFileArg_OnASubcommandReachedByACustomDispatcher(self):
        """A program can register its own subcommand dispatcher.

        It won't be an argparse._SubParsersAction, so the search for write-out
        args recognizes the shape as well: subcommand names mapped to parsers,
        taking the rest of the args.
        """

        class CustomParsers(argparse.Action):
            def __init__(
                self_inner,
                option_strings,
                prog=None,
                parser_class=None,
                dest=argparse.SUPPRESS,
                required=False,
                help=None,
                metavar=None,
            ):
                self_inner._name_parser_map = {}
                argparse.Action.__init__(
                    self_inner,
                    option_strings=option_strings,
                    dest=dest,
                    nargs=argparse.PARSER,
                    choices=self_inner._name_parser_map,
                    required=required,
                    help=help,
                    metavar=metavar,
                )
                self_inner._parser_class = parser_class

            def add_parser(self_inner, name, **kwargs):
                parser = self_inner._parser_class(**kwargs)
                self_inner._name_parser_map[name] = parser
                return parser

            def __call__(self_inner, parser, namespace, values, option_string=None):
                self_inner._name_parser_map[values[0]].parse_known_args(
                    values[1:], namespace
                )

        victim_path = self.make_file_holding_original_contents()

        self.initParser()
        self.parser.register("action", "parsers", CustomParsers)
        self.parser.add_subparsers(
            dest="cmd", parser_class=configargparse.ArgumentParser
        ).add_parser(
            "deploy", args_for_writing_out_config_file=["-w", "--write-config"]
        )

        self.assertEqual(len(configargparse._subparsers_of(self.parser)), 2)
        # only found through the custom dispatcher, so this key is turned down
        self.assertParseArgsRaises(
            "can only be set on the command line",
            args=["deploy", "--"],
            config_file_contents="write-config = %s" % victim_path,
        )

        self.assert_file_still_holds_original_contents(victim_path)

    def testWriteOutConfigFileArg_UsesThePathTheActionStored(self):
        """A store action of the program's own may change the path on its way in.

        The file has to be written where the action put it, not where the raw
        arg pointed.
        """

        class AppendsIniExtensionToPath(argparse._StoreAction):
            def __call__(self_inner, parser, namespace, values, option_string=None):
                argparse._StoreAction.__call__(
                    self_inner, parser, namespace, values + ".ini", option_string
                )

        config_file_path = self.make_temp_file_holding_contents(suffix=".ini")

        self.initParser()
        self.add_arg(
            "-w", is_write_out_config_file_arg=True, action=AppendsIniExtensionToPath
        )
        self.add_arg("--name", default="bob")

        self.parse(["-w", config_file_path[: -len(".ini")]])
        self.assertTrue(self.parser._exit_method_called)
        with open(config_file_path) as f:
            self.assertEqual(f.read(), "name = bob\n")

    def testWriteOutConfigFileArg_RejectionDoesntRelyOnErrorStopping(self):
        """error() is meant to stop the program, but a program can override exit().

        The args are dropped rather than added when one of them names a
        write-out arg, so the protection holds either way.
        """

        class KeepsGoing(configargparse.ArgumentParser):
            def exit(self_inner, status=0, message=None):
                pass

            def error(self_inner, message):
                self_inner.exit(2, message)

        victim_path = self.make_file_holding_original_contents()

        parser = KeepsGoing(args_for_writing_out_config_file=["-w", "--write-config"])

        with captured_output():
            parser.parse_known_args(
                args=[],
                config_file_contents="write-config = %s" % victim_path,
            )

        self.assert_file_still_holds_original_contents(victim_path)

    def testSubcommandsDispatchedByAPlainChoicesDictStillParse(self):
        # an ordinary program can dispatch its own subcommands with a dict of
        # choices that holds anything at all, and it has no write-out arg
        def handler():
            pass

        self.initParser()
        self.add_arg("--verbose", action="store_true")
        self.add_arg("command", nargs=argparse.PARSER, choices={"run": handler})

        # the dict values aren't parsers, and looking for write-out args must
        # not trip over them. The args added from the config file go before
        # the subcommand, so the PARSER positional doesn't take them.
        ns = self.parse(["run", "x"], config_file_contents="verbose = true")
        self.assertTrue(ns.verbose)
        self.assertEqual(ns.command, ["run", "x"])

        self.initParser()
        self.add_arg("--verbose", action="store_true")
        self.add_arg("command", nargs=argparse.PARSER, choices={"run": handler})
        ns = self.parse(["run", "x"])
        self.assertEqual(ns.command, ["run", "x"])

    def testWriteOutConfigFileArg_PathIsForgottenBetweenParses(self):
        """A path from one parse must not still be there for the next one.

        Uses a parser whose exit() doesn't stop, so parsing carries on past the
        first write the way a program overriding exit() would.
        """

        class KeepsGoing(configargparse.ArgumentParser):
            def exit(self_inner, status=0, message=None):
                pass

        first_path = self.make_unused_path_in_a_new_temp_dir()

        parser = KeepsGoing(args_for_writing_out_config_file=["-w"])
        parser.add_argument("--name", default="bob")

        with captured_output():
            parser.parse_known_args(["-w", first_path])
        self.assertTrue(os.path.exists(first_path))
        # removed so the check below can tell whether the next parse writes it
        os.remove(first_path)

        # the second parse doesn't ask for one, so nothing is written
        with captured_output():
            parser.parse_known_args([])
        self.assertFalse(os.path.exists(first_path))

    def testWriteOutConfigFileArg_WritesTheStringAValueWasConvertedFrom(self):
        """str() of a dict from type=json.loads is a string json.loads rejects."""
        for add_arg_kwargs, args, env_vars in (
            ({}, ["--settings", '{"a": 1}'], {}),
            ({"nargs": "+"}, ["--settings", '{"a": 1}', "[2]"], {}),
            ({"action": "append"}, ["--settings", '{"a": 1}', "--settings", "[2]"], {}),
            ({"env_var": "SETTINGS"}, [], {"SETTINGS": '{"a": 1}'}),
        ):
            with self.subTest(add_arg_kwargs=add_arg_kwargs):
                _, parsed_value, value_read_back = (
                    self.write_config_file_with_the_write_out_arg_and_read_it_back(
                        "--settings",
                        dict(type=json.loads, **add_arg_kwargs),
                        args,
                        env_vars,
                    )
                )
                self.assertEqual(value_read_back, parsed_value)

        # a value changed after parsing is written as it now is
        self.initParser()
        self.add_arg("--settings", type=json.loads)
        ns = self.parse(args=["--settings", '{"a": 1}'])
        ns.settings = {"b": 2}
        self.assertEqual(
            self.parser.get_items_for_config_file_output(
                self.parser.get_source_to_settings_dict(), ns
            ),
            OrderedDict([("settings", "{'b': 2}")]),
        )

    def testWriteOutConfigFileArg_TupleDefaultOfAMultiValueOptionReadsBack(self):
        written_contents, _, value_read_back = (
            self.write_config_file_with_the_write_out_arg_and_read_it_back(
                "--size", {"nargs": 2, "type": int, "default": (640, 480)}, []
            )
        )
        self.assertEqual(written_contents, "size = [640, 480]\n")
        self.assertEqual(value_read_back, [640, 480])

    def testWriteOutConfigFileArg_WritesADefaultOnlyWhereItReadsBack(self):
        """Read back without a default, the option gets that default anyway."""
        # a string default that type= converts is written as that string
        written_contents, parsed_value, value_read_back = (
            self.write_config_file_with_the_write_out_arg_and_read_it_back(
                "--output", {"type": json.loads, "default": '{"a": 1}'}, []
            )
        )
        self.assertEqual(written_contents, 'output = {"a": 1}\n')
        self.assertEqual(value_read_back, parsed_value)
        written_contents, _, value_read_back = (
            self.write_config_file_with_the_write_out_arg_and_read_it_back(
                "--output", {"type": argparse.FileType("w"), "default": "-"}, []
            )
        )
        self.assertEqual(written_contents, "output = -\n")
        self.assertIs(value_read_back, sys.stdout)

        # one that can't be written so it reads back is left out
        for add_arg_kwargs in (
            {"type": argparse.FileType("w"), "default": sys.stdout},
            {"nargs": "+", "default": []},
        ):
            with self.subTest(add_arg_kwargs=add_arg_kwargs):
                written_contents, _, _ = (
                    self.write_config_file_with_the_write_out_arg_and_read_it_back(
                        "--output", add_arg_kwargs, []
                    )
                )
                self.assertEqual(written_contents, "")

        # and so is the default of an option in a mutually exclusive group,
        # which read back would conflict with the member that was set, or with
        # another member's default
        for args, expected_items in (
            (["--fast"], OrderedDict([("fast", "true")])),
            ([], OrderedDict()),
        ):
            with self.subTest(args=args):
                self.initParser()
                group = self.parser.add_mutually_exclusive_group()
                group.add_argument("--fast", action="store_true")
                group.add_argument("--level", type=int, default=3)
                group.add_argument("--preset", default="balanced")
                ns = self.parse(args=args, env_vars={})
                self.assertEqual(
                    self.parser.get_items_for_config_file_output(
                        self.parser.get_source_to_settings_dict(), ns
                    ),
                    expected_items,
                )

    def testWriteOutConfigFileArg_EnumMembersReadBackByNameOrByValue(self):
        from enum import Enum

        class Color(Enum):
            RED = "red"
            GREEN = "green"

        def color_by_name(name):
            return Color[name]

        for add_arg_kwargs, args, expected_contents in (
            # a lookup by name takes the name, from the command line or a default
            ({"type": color_by_name}, ["--color", "GREEN"], "color = GREEN\n"),
            ({"type": color_by_name, "default": Color.RED}, [], "color = RED\n"),
            (
                {"type": color_by_name, "nargs": "+"},
                ["--color", "GREEN", "RED"],
                'color = ["GREEN", "RED"]\n',
            ),
            # while type=Color takes the value
            ({"type": Color}, ["--color", "green"], "color = green\n"),
        ):
            with self.subTest(add_arg_kwargs=add_arg_kwargs, args=args):
                written_contents, parsed_value, value_read_back = (
                    self.write_config_file_with_the_write_out_arg_and_read_it_back(
                        "--color", add_arg_kwargs, args
                    )
                )
                self.assertEqual(written_contents, expected_contents)
                self.assertEqual(value_read_back, parsed_value)

    def testWriteOutConfigFileArg_FlagWhoseConstIsAListIsWrittenAsTrue(self):
        # a flag's value is told apart from its const by identity
        self.initParser()
        self.add_arg(
            "--fast", action="store_const", const=["-O2", "-march=native"], dest="flags"
        )
        self.add_arg("--tag", action="append_const", const=["a", "b"], dest="tags")
        ns = self.parse(args=["--fast", "--tag"])
        self.assertEqual(
            self.parser.get_items_for_config_file_output(
                self.parser.get_source_to_settings_dict(), ns
            ),
            OrderedDict([("fast", "true"), ("tag", "true")]),
        )

    def testWriteOutConfigFileArg_LeavesOutTheConfigFilePathArg(self):
        """The written file already holds what the config file it names held.

        Read back, a key for the config file path arg would set that arg
        without the file it names being opened.
        """
        config_file_path = self.make_temp_file_holding_contents("genome = hg19\n")
        output_path = self.make_unused_path_in_a_new_temp_dir()
        self.initParser(
            args_for_setting_config_path=["-c", "--config-file"],
            args_for_writing_out_config_file=["-w"],
        )
        self.add_arg("--genome")
        self.add_arg("--level")
        with captured_output():
            self.parse(args=["-c", config_file_path, "--level", "3", "-w", output_path])
        with open(output_path) as output_file:
            self.assertEqual(output_file.read(), "level = 3\ngenome = hg19\n")

    def testWriteOutConfigFileArg_CantHaveAnEnvVar(self):
        # an env var that sets one would overwrite the path it names, so the
        # combination is rejected up front rather than at parse time
        self.initParser()
        self.assertRaisesRegex(
            ValueError,
            "is_write_out_config_file_arg=True can't also have env_var set",
            self.add_arg,
            "--write-config",
            is_write_out_config_file_arg=True,
            env_var="WRITE_CONFIG",
        )

    def testWriteOutConfigFileArg_IgnoreUnknownConfigFileKeys(self):
        # ignore_unknown_config_file_keys=True discards the key like any other
        # unknown one, so it neither writes the file nor errors
        victim_path = self.make_file_holding_original_contents()

        self.initParser(
            args_for_writing_out_config_file=["-w", "--write-config"],
            ignore_unknown_config_file_keys=True,
        )
        self.add_arg("--name", default="world")

        ns = self.parse(args=[], config_file_contents="write-config = %s" % victim_path)
        self.assertEqual(ns.name, "world")
        self.assertFalse(self.parser._exit_method_called)

        self.assert_file_still_holds_original_contents(victim_path)

    def testWriteOutConfigFileArg_CommandLineFormsAllStillWork(self):
        """Every form of the arg that argparse accepts must still write the file."""
        for command_line_args in (
            ["--write-config", "%s"],  # long form
            ["--write-config=%s"],  # long form with the value attached
            ["--write-conf", "%s"],  # unambiguous abbreviation
            ["-w", "%s"],  # short form
            ["-w%s"],  # short form with the value attached
            ["-vw", "%s"],  # bundled with another short option
            ["-vw%s"],  # bundled, with the value attached
        ):
            config_file_path = self.make_temp_file_holding_contents()

            self.initParser(args_for_writing_out_config_file=["-w", "--write-config"])
            self.add_arg("-v", action="store_true")
            self.add_arg("--name")

            self.parse(
                [a.replace("%s", config_file_path) for a in command_line_args]
                + ["--name", "bob"]
            )
            self.assertTrue(self.parser._exit_method_called, msg=str(command_line_args))

            with open(config_file_path) as f:
                self.assertEqual(f.read(), "name = bob\n", msg=str(command_line_args))

    def testWriteOutConfigFileArg_CantBePositional(self):
        """A positional can't be one of these.

        A config file value becomes a plain command line arg, and a positional
        would take one of those just as readily as one the user typed. Telling
        them apart would mean working out how argparse hands out positionals
        over a whole arg list, so the combination is turned down instead.
        """
        self.initParser()
        self.assertRaisesRegex(
            ValueError,
            "is_write_out_config_file_arg=True can't be a positional arg",
            self.add_arg,
            "outpath",
            nargs="?",
            is_write_out_config_file_arg=True,
        )

    def testWriteOutConfigFileArg_FromfilePrefixChars(self):
        # argparse expands @file args into the command line, so an arg supplied
        # that way is still a command line arg
        config_file_path = self.make_temp_file_holding_contents()
        args_file_path = self.make_temp_file_holding_contents(
            "--write-config\n%s\n" % config_file_path
        )

        self.initParser(
            args_for_writing_out_config_file=["-w", "--write-config"],
            fromfile_prefix_chars="@",
        )
        self.add_arg("--name")

        self.parse(["@" + args_file_path, "--name", "bob"])
        self.assertTrue(self.parser._exit_method_called)

        with open(config_file_path) as f:
            self.assertEqual(f.read(), "name = bob\n")

    def testOptionInAnArgsFileOverridesConfigFileAndEnvVarValues(self):
        args_file_path = self.make_temp_file_holding_contents("--genome\nGRCh38\n")
        self.initParser(fromfile_prefix_chars="@")
        self.add_arg("--genome", env_var="GENOME")
        self.add_arg("--level")
        for args in (["@" + args_file_path], ["@" + args_file_path, "--level", "1"]):
            with self.subTest(args=args):
                ns = self.parse(
                    args=args, config_file_contents="genome = hg19", env_vars={}
                )
                self.assertEqual(ns.genome, "GRCh38")
                ns = self.parse(args=args, env_vars={"GENOME": "hg19"})
                self.assertEqual(ns.genome, "GRCh38")

        # an args file that can't be read is a usage error, as in argparse
        self.assertParseArgsRaises(
            "No such file",
            args=["@" + args_file_path + ".missing"],
            env_vars={},
        )

    def testWriteOutConfigFileArg_OutputFileIsntTruncatedOnFailure(self):
        """Nothing is opened for writing until the contents to write exist."""

        class FailingConfigFileParser(configargparse.DefaultConfigFileParser):
            def serialize(self_inner, items):
                raise ValueError("can't serialize that")

        victim_path = self.make_file_holding_original_contents()

        self.initParser(
            args_for_writing_out_config_file=["-w"],
            config_file_parser_class=FailingConfigFileParser,
        )
        self.add_arg("--name")

        self.assertRaises(
            ValueError, self.parse, args=["-w", victim_path, "--name", "bob"]
        )
        self.assertFalse(self.parser._exit_method_called)

        self.assert_file_still_holds_original_contents(victim_path)

    def testMethodAliases(self):
        p = self.parser
        p.add("-a", "--arg-a", default=3)
        p.add_arg("-b", "--arg-b", required=True)
        p.add_argument("-c")

        g1 = p.add_argument_group(title="group1", description="descr")
        g1.add("-d", "--arg-d", required=True)
        g1.add_arg("-e", "--arg-e", required=True)
        g1.add_argument("-f", "--arg-f", default=5)

        g2 = p.add_mutually_exclusive_group(required=True)
        g2.add("-x", "--arg-x")
        g2.add_arg("-y", "--arg-y")
        g2.add_argument("-z", "--arg-z", default=5)

        # verify that flags must be globally unique
        g2 = p.add_argument_group(title="group2", description="descr")
        self.assertRaises(argparse.ArgumentError, g1.add, "-c")
        self.assertRaises(argparse.ArgumentError, g2.add, "-f")

        self.initParser()
        p = self.parser
        options = p.parse(args=[])
        self.assertDictEqual(vars(options), {})

    def testConfigOpenFuncError(self):
        # test OSError -- str(OSError(errno, strerror)) formats as
        # "[Errno N] strerror", so the full message is preserved.
        def error_func(path):
            raise OSError(9, "some error")

        self.initParser(config_file_open_func=error_func)
        self.parser.add_argument("-g", is_config_file=True)
        self.assertParseArgsRaises(
            r"Unable to open config file: file\.txt\. Error: \[Errno 9\] some error",
            args="-g file.txt",
        )

        # test other error
        def error_func(path):
            raise Exception("custom error")

        self.initParser(config_file_open_func=error_func)
        self.parser.add_argument("-g", is_config_file=True)
        self.assertParseArgsRaises(
            "Unable to open config file: file.txt. Error: custom error",
            args="-g file.txt",
        )

        # test custom 2-arg exception -- both args must be preserved, not
        # formatted like an OSError's (errno, strerror) pair, which would
        # silently drop the first arg.
        class CustomTwoArgError(Exception):
            pass

        def error_func(path):
            raise CustomTwoArgError("CODE", "human message")

        self.initParser(config_file_open_func=error_func)
        self.parser.add_argument("-g", is_config_file=True)
        self.assertParseArgsRaises(
            r"Unable to open config file: file\.txt\. Error: \('CODE', 'human message'\)",
            args="-g file.txt",
        )

    def testDoubleDashSeparator(self):
        """Test that -- separator correctly separates optional from positional args
        when config file or env vars are used. Regression test for issue #298."""
        config_file_path = self.make_temp_file_holding_contents(
            "list = [1, 2, 3]\n", suffix=".conf"
        )

        # Test with config file and -- separator
        self.initParser(args_for_setting_config_path=["-c", "--config"])
        self.parser.add_argument("--list", action="append")
        self.parser.add_argument("positional_arg", nargs="*")

        ns = self.parse(args="-c %s -- foo bar" % config_file_path)
        self.assertEqual(ns.positional_arg, ["foo", "bar"])
        self.assertEqual(ns.list, ["1", "2", "3"])

        # Test without -- separator
        ns = self.parse(args="-c %s foo bar" % config_file_path)
        self.assertEqual(ns.positional_arg, ["foo", "bar"])
        self.assertEqual(ns.list, ["1", "2", "3"])

        # Test with env var and -- separator
        self.initParser()
        self.parser.add_argument("--list", action="append", env_var="MY_LIST")
        self.parser.add_argument("positional_arg", nargs="*")

        with mock.patch.dict(os.environ, {"MY_LIST": "[1,2,3]"}):
            ns = self.parse(args="-- foo bar")
            self.assertEqual(ns.positional_arg, ["foo", "bar"])
            self.assertEqual(ns.list, ["1", "2", "3"])

    def testRemainderWithConfigFile(self):
        """Test that nargs=REMAINDER works correctly with config files.
        Regression test for issue #285."""
        config_file_path = self.make_temp_file_holding_contents(
            "config_file_option=value_from_config\n", suffix=".cfg"
        )

        self.initParser()
        self.parser.add_argument(
            "--config", is_config_file=True, default=config_file_path
        )
        self.parser.add_argument("--config_file_option", nargs="*", default=None)
        self.parser.add_argument(
            "remainder_option", nargs=argparse.REMAINDER, default=None
        )

        ns = self.parse(args=["test"])
        self.assertEqual(ns.remainder_option, ["test"])
        self.assertEqual(ns.config_file_option, ["value_from_config"])

        ns = self.parse(args=["test", "arg1", "arg2"])
        self.assertEqual(ns.remainder_option, ["test", "arg1", "arg2"])
        self.assertEqual(ns.config_file_option, ["value_from_config"])

    def testOptionWithRemainderNargsTakesEveryArgAfterIt(self):
        """The args after an option with nargs=REMAINDER are its values, not options."""
        config_file_path = self.make_temp_file_holding_contents("genome = HCMV\n")
        self.initParser(default_config_files=[config_file_path])
        self.add_arg("--genome", env_var="GENOME")
        self.add_arg("--run", nargs=argparse.REMAINDER)

        # a value that looks like this parser's own option doesn't override
        # the config file or env var value, which goes before '--run'
        ns = self.parse(args=["--run", "tool", "--genome", "hg19"], env_vars={})
        self.assertEqual(ns.genome, "HCMV")
        self.assertEqual(ns.run, ["tool", "--genome", "hg19"])
        ns = self.parse(
            args=["--run", "tool", "--genome", "hg19"], env_vars={"GENOME": "mm10"}
        )
        self.assertEqual(ns.genome, "mm10")

        # nor does a '-h' among its values skip the config files
        ns = self.parse(args=["--run", "grep", "-h", "x"], env_vars={})
        self.assertEqual(ns.genome, "HCMV")
        self.assertEqual(ns.run, ["grep", "-h", "x"])

        # or get dropped by ignore_help_args
        ns, _ = self.parse_known(
            args=["--run", "du", "-h"], ignore_help_args=True, env_vars={}
        )
        self.assertEqual(ns.run, ["du", "-h"])

        # the same option before '--run' still overrides
        ns = self.parse(args=["--genome", "hg38", "--run", "tool"], env_vars={})
        self.assertEqual(ns.genome, "hg38")

    def testEmptyValuesIgnored(self):
        """Test that empty string values from config files and env vars are ignored
        for args with nargs. Regression test for issue #296."""
        # Test empty value in config file
        config_file_path = self.make_temp_file_holding_contents(
            "test-opt=\n", suffix=".conf"
        )

        self.initParser()
        self.parser.add_argument(
            "--config", is_config_file=True, default=config_file_path
        )
        self.parser.add_argument(
            "--test-opt", nargs=2, default=["default1", "default2"]
        )

        ns = self.parse(args=[])
        self.assertEqual(ns.test_opt, ["default1", "default2"])

        # Test empty environment variable
        self.initParser()
        self.parser.add_argument(
            "--test-opt", nargs=2, env_var="TEST_OPT", default=["default1", "default2"]
        )

        with mock.patch.dict(os.environ, {"TEST_OPT": ""}):
            ns = self.parse(args=[])
            self.assertEqual(ns.test_opt, ["default1", "default2"])

    def testConfigArgOrderWithOptsOnCli(self):
        """Test that config args with nargs/append are parsed correctly when
        optional args are already on the command line."""
        config_file_path = self.make_temp_file_holding_contents(
            "from_config = [x, y]\n", suffix=".conf"
        )

        self.initParser()
        self.parser.add_argument(
            "--config", is_config_file=True, default=config_file_path
        )
        self.parser.add_argument("--from_config", action="append")
        self.parser.add_argument("--cli_opt")
        self.parser.add_argument("positional", nargs="*")

        ns = self.parse(args="--cli_opt val pos1 pos2")
        self.assertEqual(ns.positional, ["pos1", "pos2"])
        self.assertEqual(ns.cli_opt, "val")
        self.assertEqual(ns.from_config, ["x", "y"])

    def testConfigArgOrderNoOptsOnCli(self):
        """Test that config args are parsed correctly when no optional args
        are on the command line (only positional args)."""
        config_file_path = self.make_temp_file_holding_contents(
            "flag\n", suffix=".conf"
        )

        self.initParser()
        self.parser.add_argument(
            "--config", is_config_file=True, default=config_file_path
        )
        self.parser.add_argument("--flag", action="store_true")
        self.parser.add_argument("positional")

        ns = self.parse(args="mypos")
        self.assertEqual(ns.positional, "mypos")
        self.assertTrue(ns.flag)

    def testEnvVarArgOrderWithOptsOnCli(self):
        """Test that env var args with nargs/append are parsed correctly when
        optional args are already on the command line."""
        self.initParser()
        self.parser.add_argument("--from_env", action="append", env_var="FROM_ENV")
        self.parser.add_argument("--cli_opt")
        self.parser.add_argument("positional", nargs="*")

        with mock.patch.dict(os.environ, {"FROM_ENV": "[a,b]"}):
            ns = self.parse(args="--cli_opt val pos1 pos2")
            self.assertEqual(ns.positional, ["pos1", "pos2"])
            self.assertEqual(ns.cli_opt, "val")
            self.assertEqual(ns.from_env, ["a", "b"])

    def testEnvVarArgOrderNoOptsOnCli(self):
        """Test that env var args are parsed correctly when no optional args
        are on the command line (only positional args)."""
        self.initParser()
        self.parser.add_argument("--flag", action="store_true", env_var="MY_FLAG")
        self.parser.add_argument("positional")

        with mock.patch.dict(os.environ, {"MY_FLAG": "true"}):
            ns = self.parse(args="mypos")
            self.assertEqual(ns.positional, "mypos")
            self.assertTrue(ns.flag)


class TestConfigFileParsers(TestCase):
    """Test ConfigFileParser subclasses in isolation"""

    def assert_each_line_parses_to_key_and_value(self, lines_and_expected_key_value):
        """Parse each line on its own with DefaultConfigFileParser and check
        the single key and value it gives.

        Args:
            lines_and_expected_key_value: A list of (line, key, value) tuples,
                where key and value are what parsing that line should give.
        """
        p = configargparse.DefaultConfigFileParser()
        for line, expected_key, expected_value in lines_and_expected_key_value:
            with self.subTest(line=line):
                self.assertDictEqual(
                    dict(p.parse(StringIO(line))),
                    {expected_key: expected_value},
                    msg="Line %r" % line,
                )

    def testDefaultConfigFileParser_ListRoundtrip(self):
        parser = configargparse.DefaultConfigFileParser()
        cases = [
            ["a,b", "c"],
            [" padded ", ""],
            ['"quoted"', "back\\slash"],
            ["1", "true", "null"],
            [["a,b"], ["c"]],
            ["café", "naïve"],
            # JSON escapes these, so they can't start a new key
            ["line1\nline2", "x\rother = injected"],
            # parse() reads whitespace followed by these as a comment's start
            ["issue #12", "a ;b", "c\N{NO-BREAK SPACE}#d"],
        ]
        for values in cases:
            with self.subTest(values=values):
                rendered = parser.serialize({"files": values})
                # newline=None reads a lone \r as a line break, the way open()
                # does when parse_args reads a config file
                self.assertEqual(
                    parser.parse(StringIO(rendered, newline=None)), {"files": values}
                )

    def testDefaultConfigFileParser_ListWritesNonAsciiTextAsIs(self):
        # config files are meant to be edited by hand, so non-ASCII text must be
        # written as is rather than as a JSON escape code
        parser = configargparse.DefaultConfigFileParser()
        self.assertEqual(parser.serialize({"files": ["café"]}), 'files = ["café"]\n')

    def testDefaultConfigFileParser_Basic(self):
        p = configargparse.DefaultConfigFileParser()
        self.assertGreater(len(p.get_syntax_description()), 0)

        # test the simplest case
        input_config_str = StringIO("""a: 3\n""")
        parsed_obj = p.parse(input_config_str)
        output_config_str = p.serialize(parsed_obj)

        self.assertEqual(
            input_config_str.getvalue().replace(": ", " = "), output_config_str
        )

        self.assertDictEqual(parsed_obj, {"a": "3"})

    def testDefaultConfigFileParser_SerializeWritesNonStringValuesWithFormat(self):
        # a value that isn't a plain string is written with format(), which
        # differs from str() for an Enum before Python 3.11
        from enum import Enum, IntEnum

        class Color(str, Enum):
            RED = "red"

        class Level(IntEnum):
            HIGH = 2

        p = configargparse.DefaultConfigFileParser()
        self.assertEqual(
            p.serialize(OrderedDict([("color", Color.RED), ("level", Level.HIGH)])),
            "color = {}\nlevel = {}\n".format(Color.RED, Level.HIGH),
        )

    def testDefaultConfigFileParser_SerializeRejectsNewlines(self):
        # this format puts one key per line, so a newline in a value would come
        # back as extra keys rather than as part of the value. A list is written
        # as JSON, which escapes newlines (see
        # testDefaultConfigFileParser_ListRoundtrip), so it isn't rejected.
        p = configargparse.DefaultConfigFileParser()
        self.assertRaisesRegex(
            ValueError,
            "can't contain newlines",
            p.serialize,
            OrderedDict([("a", "3\nb = 4")]),
        )
        self.assertRaisesRegex(
            ValueError,
            "can't contain newlines",
            p.serialize,
            OrderedDict([("a\nb", "3")]),
        )

    def testDefaultConfigFileParser_FlagWithACommentAfterItIsAFlag(self):
        p = configargparse.DefaultConfigFileParser()
        self.assertEqual(
            p.parse(StringIO("verbose  # be loud\nquiet ; or not\n")),
            OrderedDict([("verbose", "true"), ("quiet", "true")]),
        )
        # after a separator other than whitespace, '#' still starts the value
        self.assertEqual(
            p.parse(StringIO("color = #ff0000\n")), OrderedDict([("color", "#ff0000")])
        )

    def testDefaultConfigFileParser_All(self):
        p = configargparse.DefaultConfigFileParser()

        # test the all syntax case
        config_lines = [
            "# comment1 ",
            "[ some section ]",
            "----",
            "---------",
            "_a: 3",
            "; comment2 ",
            "_b = c",
            "_list_arg1 = [a, b, c]",
            "_str_arg = true",
            "_list_arg2 = [1, 2, 3]",
        ]

        # test parse
        input_config_str = StringIO("\n".join(config_lines) + "\n")
        parsed_obj = p.parse(input_config_str)

        # test serialize
        output_config_str = p.serialize(parsed_obj)
        self.assertEqual(
            '_a = 3\n_b = c\n_list_arg1 = ["a", "b", "c"]\n'
            "_str_arg = true\n_list_arg2 = [1, 2, 3]\n",
            output_config_str,
        )

        self.assertDictEqual(
            parsed_obj,
            {
                "_a": "3",
                "_b": "c",
                "_list_arg1": ["a", "b", "c"],
                "_str_arg": "true",
                "_list_arg2": [1, 2, 3],
            },
        )

        self.assertListEqual(parsed_obj["_list_arg1"], ["a", "b", "c"])
        self.assertListEqual(parsed_obj["_list_arg2"], [1, 2, 3])

    def testDefaultConfigFileParser_BasicValues(self):
        # test the all syntax case
        config_lines = [
            ("key = value # comment # comment", "key", "value"),
            ("key=value#comment ", "key", "value#comment"),
            ("key=value", "key", "value"),
            ("key =value", "key", "value"),
            ("key= value", "key", "value"),
            ("key = value", "key", "value"),
            ("key  =  value", "key", "value"),
            (" key  =  value ", "key", "value"),
            ("key:value", "key", "value"),
            ("key :value", "key", "value"),
            ("key: value", "key", "value"),
            ("key : value", "key", "value"),
            ("key  :  value", "key", "value"),
            (" key  :  value ", "key", "value"),
            ("key value", "key", "value"),
            ("key  value", "key", "value"),
            (" key    value ", "key", "value"),
        ]

        self.assert_each_line_parses_to_key_and_value(config_lines)

    def testDefaultConfigFileParser_QuotedValues(self):
        # test the all syntax case
        config_lines = [
            ('key="value"', "key", "value"),
            ('key  =  "value"', "key", "value"),
            (' key  =  "value" ', "key", "value"),
            ('key=" value "', "key", " value "),
            ('key  =  " value "', "key", " value "),
            (' key  =  " value " ', "key", " value "),
            ("key='value'", "key", "value"),
            ("key  =  'value'", "key", "value"),
            (" key  =  'value' ", "key", "value"),
            ("key=' value '", "key", " value "),
            ("key  =  ' value '", "key", " value "),
            (" key  =  ' value ' ", "key", " value "),
            ('key="', "key", '"'),
            ('key  =  "', "key", '"'),
            (' key  =  " ', "key", '"'),
            ("key = '\"value\"'", "key", '"value"'),
            ("key = \"'value'\"", "key", "'value'"),
            ('key = ""value""', "key", '"value"'),
            ("key = ''value''", "key", "'value'"),
            ('key="value', "key", '"value'),
            ('key  =  "value', "key", '"value'),
            (' key  =  "value ', "key", '"value'),
            ('key=value"', "key", 'value"'),
            ('key  =  value"', "key", 'value"'),
            (' key  =  value " ', "key", 'value "'),
            ("key='value", "key", "'value"),
            ("key  =  'value", "key", "'value"),
            (" key  =  'value ", "key", "'value"),
            ("key=value'", "key", "value'"),
            ("key  =  value'", "key", "value'"),
            (" key  =  value ' ", "key", "value '"),
        ]

        self.assert_each_line_parses_to_key_and_value(config_lines)

    def testDefaultConfigFileParser_BlankValues(self):
        # test the all syntax case
        config_lines = [
            ("key=", "key", ""),
            ("key =", "key", ""),
            ("key= ", "key", ""),
            ("key = ", "key", ""),
            ("key  =  ", "key", ""),
            (" key  =   ", "key", ""),
            ("key:", "key", ""),
            ("key :", "key", ""),
            ("key: ", "key", ""),
            ("key : ", "key", ""),
            ("key  :  ", "key", ""),
            (" key  :   ", "key", ""),
        ]

        self.assert_each_line_parses_to_key_and_value(config_lines)

    def testDefaultConfigFileParser_UnspecifiedValues(self):
        # test the all syntax case
        config_lines = [
            ("key ", "key", "true"),
            ("key", "key", "true"),
            ("key  ", "key", "true"),
            (" key     ", "key", "true"),
        ]

        self.assert_each_line_parses_to_key_and_value(config_lines)

    def testDefaultConfigFileParser_ColonEqualSignValue(self):
        # test the all syntax case
        config_lines = [
            ("key=:", "key", ":"),
            ("key =:", "key", ":"),
            ("key= :", "key", ":"),
            ("key = :", "key", ":"),
            ("key  =  :", "key", ":"),
            (" key  =  : ", "key", ":"),
            ("key:=", "key", "="),
            ("key :=", "key", "="),
            ("key: =", "key", "="),
            ("key : =", "key", "="),
            ("key  :  =", "key", "="),
            (" key  :  = ", "key", "="),
            ("key==", "key", "="),
            ("key ==", "key", "="),
            ("key= =", "key", "="),
            ("key = =", "key", "="),
            ("key  =  =", "key", "="),
            (" key  =  = ", "key", "="),
            ("key::", "key", ":"),
            ("key ::", "key", ":"),
            ("key: :", "key", ":"),
            ("key : :", "key", ":"),
            ("key  :  :", "key", ":"),
            (" key  :  : ", "key", ":"),
        ]

        self.assert_each_line_parses_to_key_and_value(config_lines)

    def testDefaultConfigFileParser_ValuesWithComments(self):
        # test the all syntax case
        config_lines = [
            ("key=value#comment ", "key", "value#comment"),
            ("key=value #comment", "key", "value"),
            (" key  =  value  #  comment", "key", "value"),
            ("key:value#comment", "key", "value#comment"),
            ("key:value #comment", "key", "value"),
            (" key  :  value  #  comment", "key", "value"),
            ("key=value;comment ", "key", "value;comment"),
            ("key=value ;comment", "key", "value"),
            (" key  =  value  ;  comment", "key", "value"),
            ("key:value;comment", "key", "value;comment"),
            ("key:value ;comment", "key", "value"),
            (" key  :  value  ;  comment", "key", "value"),
            ("key = value # comment # comment", "key", "value"),
            ('key = "value # comment" # comment', "key", "value # comment"),
            ('key = "#" ; comment', "key", "#"),
            ('key = ";" # comment', "key", ";"),
        ]

        self.assert_each_line_parses_to_key_and_value(config_lines)

    def testDefaultConfigFileParser_NegativeValues(self):
        # test the all syntax case
        config_lines = [
            ("key = -10", "key", "-10"),
            ("key : -10", "key", "-10"),
            ("key -10", "key", "-10"),
            ('key = "-10"', "key", "-10"),
            ("key  =  '-10'", "key", "-10"),
            ("key=-10", "key", "-10"),
        ]

        self.assert_each_line_parses_to_key_and_value(config_lines)

    def testDefaultConfigFileParser_KeySyntax(self):
        # test the all syntax case
        config_lines = [
            ("key_underscore = value", "key_underscore", "value"),
            ("key_underscore=", "key_underscore", ""),
            ("key_underscore", "key_underscore", "true"),
            ("_key_underscore = value", "_key_underscore", "value"),
            ("_key_underscore=", "_key_underscore", ""),
            ("_key_underscore", "_key_underscore", "true"),
            ("key_underscore_ = value", "key_underscore_", "value"),
            ("key_underscore_=", "key_underscore_", ""),
            ("key_underscore_", "key_underscore_", "true"),
            ("key-dash = value", "key-dash", "value"),
            ("key-dash=", "key-dash", ""),
            ("key-dash", "key-dash", "true"),
            ("key@word = value", "key@word", "value"),
            ("key@word=", "key@word", ""),
            ("key@word", "key@word", "true"),
            ("key$word = value", "key$word", "value"),
            ("key$word=", "key$word", ""),
            ("key$word", "key$word", "true"),
            ("key.word = value", "key.word", "value"),
            ("key.word=", "key.word", ""),
            ("key.word", "key.word", "true"),
        ]

        self.assert_each_line_parses_to_key_and_value(config_lines)

    @skip_unless_pyyaml_is_installed
    def testYAMLConfigFileParser_Basic(self):
        p = configargparse.YAMLConfigFileParser()
        self.assertGreater(len(p.get_syntax_description()), 0)

        input_config_str = StringIO("""a: '3'\n""")
        parsed_obj = p.parse(input_config_str)
        output_config_str = p.serialize(dict(parsed_obj))

        self.assertEqual(input_config_str.getvalue(), output_config_str)

        self.assertDictEqual(parsed_obj, {"a": "3"})

    @skip_unless_pyyaml_is_installed
    def testYAMLConfigFileParser_All(self):
        p = configargparse.YAMLConfigFileParser()

        # test the all syntax case
        config_lines = [
            "a: '3'",
            "list_arg:",
            "- 1",
            "- 2",
            "- 3",
        ]

        # test parse
        input_config_str = StringIO("\n".join(config_lines) + "\n")
        parsed_obj = p.parse(input_config_str)

        # test serialize
        output_config_str = p.serialize(parsed_obj)
        self.assertEqual(input_config_str.getvalue(), output_config_str)

        self.assertDictEqual(parsed_obj, {"a": "3", "list_arg": [1, 2, 3]})

    @skip_unless_pyyaml_is_installed
    def testYAMLConfigFileParser_w_ArgumentParser_parsed_values(self):
        parser = configargparse.ArgumentParser(
            config_file_parser_class=configargparse.YAMLConfigFileParser
        )
        parser.add_argument("-c", "--config", is_config_file=True)
        parser.add_argument("--verbosity", action="count")
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--level", type=int)

        config_lines = ["verbosity: 3", "verbose: true", "level: 35"]
        config_str = "\n".join(config_lines) + "\n"
        config_file = self.make_temp_file_holding_contents(config_str)
        args = parser.parse_args(["--config=%s" % config_file])
        self.assertEqual(args.verbosity, 3)
        self.assertTrue(args.verbose)
        self.assertEqual(args.level, 35)


# TomlConfigParser.parse() reads TOML with tomllib (Python 3.11+), or else with
# the toml package, and can't parse anything without one of them
skip_unless_a_toml_reading_library_is_installed = unittest.skipUnless(
    any(importlib.util.find_spec(name) is not None for name in ("tomllib", "toml")),
    "neither tomllib nor the toml package is installed",
)

# TomlConfigParser.serialize() writes TOML with the toml package only, since
# tomllib can't write
skip_unless_the_toml_package_is_installed = unittest.skipUnless(
    importlib.util.find_spec("toml") is not None, "toml package not installed"
)


class TestTomlConfigParser(unittest.TestCase):
    def make_toml_stream(self, content):
        """Return an in-memory text stream holding the given TOML.

        Args:
            content: The TOML text, which is dedented first.

        Returns:
            A StringIO positioned at the start of the dedented text.
        """
        return StringIO(textwrap.dedent(content))

    @skip_unless_a_toml_reading_library_is_installed
    def test_section(self):
        f = self.make_toml_stream("""
            [section]
            key1 = 'toml1'
            key2 = 'toml2'
            """)
        parser = configargparse.TomlConfigParser(["section"])
        self.assertEqual(parser.parse(f), {"key1": "toml1", "key2": "toml2"})

    @skip_unless_a_toml_reading_library_is_installed
    def test_no_sections(self):
        f = self.make_toml_stream("""
            [section]
            key1 = 'toml1'
            key2 = 'toml2'
            """)
        parser = configargparse.TomlConfigParser([])
        self.assertEqual(parser.parse(f), {})

    @skip_unless_a_toml_reading_library_is_installed
    def test_empty_section(self):
        f = self.make_toml_stream("[section]")
        parser = configargparse.TomlConfigParser(["section"])
        self.assertEqual(parser.parse(f), {})

    @skip_unless_a_toml_reading_library_is_installed
    def test_empty_file(self):
        f = self.make_toml_stream("")
        parser = configargparse.TomlConfigParser(["section"])
        self.assertEqual(parser.parse(f), {})

    @skip_unless_a_toml_reading_library_is_installed
    def test_advanced(self):
        f = self.make_toml_stream("""
            [tool.section]
            key1 = "toml1"
            key2 = [1, 2, 3]
            """)
        parser = configargparse.TomlConfigParser(["tool.section"])
        self.assertEqual(parser.parse(f), {"key1": "toml1", "key2": [1, 2, 3]})

    def test_read_without_toml_packages(self):
        f = self.make_toml_stream("""
            [tool.section]
            key1 = "toml1"
            key2 = [1, 2, 3]
            """)
        parser = configargparse.TomlConfigParser(["tool.section"])

        with mock.patch.dict(sys.modules, {"toml": None, "tomllib": None}):
            with self.assertRaises(configargparse.ConfigFileParserMissingDependency):
                parser.parse(f)

    @skip_unless_a_toml_reading_library_is_installed
    def test_binary_read_works(self):
        # parse() accepts a binary stream as well as a text one
        f = BytesIO(b'[tool.section]\nkey1 = "toml1"\n')
        parser = configargparse.TomlConfigParser(["tool.section"])
        # Should successfully parse binary stream
        self.assertEqual(parser.parse(f), {"key1": "toml1"})

    @skip_unless_the_toml_package_is_installed
    def test_serialize_with_section(self):
        parser = configargparse.TomlConfigParser(["section"])
        items = OrderedDict([("key1", "val1"), ("key2", "val2")])
        result = parser.serialize(items)
        # Verify round-trip: parse the serialized output
        parsed = parser.parse(StringIO(result))
        self.assertEqual(parsed, {"key1": "val1", "key2": "val2"})

    @skip_unless_the_toml_package_is_installed
    def test_serialize_nested_section(self):
        parser = configargparse.TomlConfigParser(["tool.section"])
        items = OrderedDict([("key1", "val1")])
        result = parser.serialize(items)
        parsed = parser.parse(StringIO(result))
        self.assertEqual(parsed, {"key1": "val1"})

    @skip_unless_the_toml_package_is_installed
    def test_serialize_without_sections_is_rejected(self):
        # parse() reads nothing back from a file written with no section
        parser = configargparse.TomlConfigParser([])
        items = OrderedDict([("key1", "val1")])
        with self.assertRaisesRegex(ValueError, "section"):
            parser.serialize(items)

    def test_serialize_without_toml_package(self):
        parser = configargparse.TomlConfigParser(["section"])
        items = OrderedDict([("key1", "val1")])
        # Mock tomllib import succeeding but toml import failing
        with mock.patch.dict(sys.modules, {"toml": None}):
            with self.assertRaises(configargparse.ConfigFileParserMissingDependency):
                parser.serialize(items)


class TestIniConfigParserSerialize(unittest.TestCase):
    def test_serialize_basic(self):
        parser = configargparse.IniConfigParser(["section"], False)
        items = OrderedDict([("key1", "val1"), ("key2", "val2")])
        result = parser.serialize(items)
        # Verify round-trip
        parsed = parser.parse(StringIO(result))
        self.assertEqual(parsed, {"key1": "val1", "key2": "val2"})

    def test_serialize_list_as_python_syntax(self):
        parser = configargparse.IniConfigParser(["section"], False)
        items = OrderedDict([("key1", ["a", "b", "c"])])
        result = parser.serialize(items)
        parsed = parser.parse(StringIO(result))
        self.assertEqual(parsed, {"key1": ["a", "b", "c"]})

    def test_serialize_list_multiline(self):
        parser = configargparse.IniConfigParser(["section"], True)
        items = OrderedDict([("key1", ["a", "b", "c"])])
        result = parser.serialize(items)
        # Should use multiline format: each element on its own indented line
        self.assertEqual(result, "[section]\nkey1 = \n\ta\n\tb\n\tc\n\n")

    def test_serialize_without_sections_is_rejected(self):
        # parse() reads nothing back from a file written with no section
        parser = configargparse.IniConfigParser([], False)
        items = OrderedDict([("key1", "val1")])
        with self.assertRaisesRegex(ValueError, "section"):
            parser.serialize(items)

    def test_serialized_percent_values_read_back_unchanged(self):
        for split_ml_text_to_list in (False, True):
            parser = configargparse.IniConfigParser(["section"], split_ml_text_to_list)
            for value in (
                "%Y-%m-%d",
                "50%",
                "100%%",
                "%(asctime)s %(message)s",
                ["50%", "%(x)s"],
            ):
                with self.subTest(split=split_ml_text_to_list, value=value):
                    contents = parser.serialize({"key": value})
                    self.assertEqual(
                        parser.parse(StringIO(contents)),
                        OrderedDict([("key", value)]),
                    )

    def test_write_config_file_preserves_scalar_values(self):
        for split_ml_text_to_list in (False, True):
            for value in ("", "[a-z]", "'quoted'", "first\nsecond"):
                with self.subTest(split=split_ml_text_to_list, value=value):
                    parser = configargparse.ArgumentParser(
                        config_file_parser_class=configargparse.IniConfigParser(
                            ["section"], split_ml_text_to_list
                        )
                    )
                    parser.add_argument("--value")
                    namespace = parser.parse_args(["--value", value])
                    with tempfile.TemporaryDirectory() as directory:
                        path = os.path.join(directory, "settings.ini")
                        parser.write_config_file(namespace, [path], exit_after=False)
                        with open(path) as stream:
                            restored = parser.parse_args(
                                [], config_file_contents=stream.read()
                            )
                    self.assertEqual(restored.value, value)

    def test_serialized_ambiguous_strings_read_back_unchanged(self):
        for split_ml_text_to_list in (False, True):
            parser = configargparse.IniConfigParser(["section"], split_ml_text_to_list)
            for value in (
                "",
                "   ",
                "[data]",
                "[1, 2]",
                "'quoted'",
                '"quoted"',
                " leading",
                "trailing ",
                "first\nsecond",
                "first\n# not a comment\nsecond",
                "first\r\nsecond",
                "[50%, %(asctime)s]",
                "simple",
            ):
                with self.subTest(split=split_ml_text_to_list, value=value):
                    contents = parser.serialize({"key": value})
                    self.assertEqual(parser.parse(StringIO(contents)), {"key": value})


class TestCompositeConfigParser(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_cwd = os.getcwd()
        os.chdir(self.tmpdir.name)
        self.parser = self.make_parser(ignore_unknown_config_file_keys=False)

    def make_parser(self, ignore_unknown_config_file_keys):
        """Make a parser that reads config.yaml, config.toml and config.ini.

        Each file can be in any of the three formats, and a later file in that
        list overrides an earlier one.

        Args:
            ignore_unknown_config_file_keys: Passed on to the parser. The TOML
                file from write_toml_file() holds a 'special' table that no arg
                is registered for, so a test that checks which file wins has
                to set this to True to get past it.

        Returns:
            The new parser.
        """
        parser = configargparse.ArgParser(
            description="test",
            default_config_files=["config.yaml", "config.toml", "config.ini"],
            config_file_parser_class=configargparse.CompositeConfigParser(
                [
                    configargparse.TomlConfigParser(["section"]),
                    configargparse.YAMLConfigFileParser,
                    configargparse.IniConfigParser(["section"], False),
                ]
            ),
            ignore_unknown_config_file_keys=ignore_unknown_config_file_keys,
        )
        parser.add_argument("--config", is_config_file=True)
        parser.add_argument("--key1", type=str)
        parser.add_argument("--key2", type=str)
        return parser

    def tearDown(self):
        os.chdir(self.old_cwd)
        self.tmpdir.cleanup()

    def write_yaml_file(self, content="key1: yaml1\nkey2: yaml2"):
        with open("config.yaml", "w") as f:
            f.write(content)

    def write_ini_file(
        self, content="[section]\nkey1=ini1\nkey2=ini2\n; this is an ini comment"
    ):
        with open("config.ini", "w") as f:
            f.write(content)

    def write_toml_file(
        self,
        content="[section]\nkey1 = 'toml1'\nkey2 = 'toml2'\nspecial.key = { a = 1 }\n# this is a toml comment",
    ):
        with open("config.toml", "w") as f:
            f.write(content)

    def test_plain(self):
        self.assertEqual(
            vars(self.parser.parse_args([])),
            {"config": None, "key1": None, "key2": None},
        )

    @skip_unless_a_toml_reading_library_is_installed
    @skip_unless_pyyaml_is_installed
    def test_with_all_configs(self):
        self.write_yaml_file()
        self.write_ini_file()
        self.write_toml_file()
        parser = self.make_parser(ignore_unknown_config_file_keys=True)
        self.assertEqual(
            vars(parser.parse_args([])),
            {"config": None, "key1": "ini1", "key2": "ini2"},
        )

    @skip_unless_a_toml_reading_library_is_installed
    @skip_unless_pyyaml_is_installed
    def test_with_all_configs_override(self):
        self.write_yaml_file()
        self.write_ini_file()
        self.write_toml_file()
        parser = self.make_parser(ignore_unknown_config_file_keys=True)
        self.assertEqual(
            vars(parser.parse_args(["--key1", "val1"])),
            {"config": None, "key1": "val1", "key2": "ini2"},
        )

    @skip_unless_pyyaml_is_installed
    def test_missing_primary_config(self):
        self.write_yaml_file()
        self.assertEqual(
            vars(self.parser.parse_args([])),
            {"config": None, "key1": "yaml1", "key2": "yaml2"},
        )

    @skip_unless_a_toml_reading_library_is_installed
    @skip_unless_pyyaml_is_installed
    def test_toml(self):
        self.write_yaml_file()
        self.write_toml_file()
        parser = self.make_parser(ignore_unknown_config_file_keys=True)
        self.assertEqual(
            vars(parser.parse_args([])),
            {"config": None, "key1": "toml1", "key2": "toml2"},
        )

    @skip_unless_a_toml_reading_library_is_installed
    @skip_unless_pyyaml_is_installed
    def test_override_primary_config(self):
        self.write_yaml_file()
        self.write_ini_file()
        self.write_toml_file()
        parser = self.make_parser(ignore_unknown_config_file_keys=True)
        self.assertEqual(
            vars(parser.parse_args(["--config", "config.yaml"])),
            {"config": "config.yaml", "key1": "yaml1", "key2": "yaml2"},
        )

    @skip_unless_a_toml_reading_library_is_installed
    @skip_unless_pyyaml_is_installed
    def test_toml_extra(self):
        self.write_yaml_file()
        self.write_ini_file()
        # no 'special' table here, so the extra keys are the only unknown ones
        self.write_toml_file(
            "[section]\nkey1 = 'toml1'\nkey2 = 'toml2'\nkey3 = 'toml3'\nkey4 = 'toml4'\n"
        )
        with captured_output() as (_, err):
            with self.assertRaises(SystemExit):
                self.parser.parse_args([])
        self.assertIn(
            "unrecognized arguments: --key3=toml3 --key4=toml4\n", err.getvalue()
        )

    @skip_unless_pyyaml_is_installed
    def test_composite_warns_if_missing_dependency(self):
        self.write_yaml_file()
        self.write_ini_file()

        # import PyYAML before patching: patch.dict restores the whole
        # sys.modules dict on exit, dropping modules first imported inside it,
        # which would leave PyYAML half-loaded for later tests
        import yaml  # noqa: F401

        with mock.patch.dict(sys.modules, {"toml": None, "tomllib": None}):
            with self.assertWarnsRegex(UserWarning, "TomlConfigParser"):
                ns = self.parser.parse_args([])
        # the parser that could not run is reported, but the chain still works
        self.assertEqual(ns.key1, "ini1")

    def test_composite_warns_if_wrong_order(self):
        with self.assertWarns(SyntaxWarning):
            configargparse.CompositeConfigParser(
                [
                    configargparse.IniConfigParser(["section"], False),
                    configargparse.TomlConfigParser(["section"]),
                ]
            )()

    def test_composite_serialize_delegates_to_first_parser(self):
        composite = configargparse.CompositeConfigParser(
            [
                configargparse.IniConfigParser(["section"], False),
                configargparse.DefaultConfigFileParser,
            ]
        )()
        items = OrderedDict([("key1", "val1"), ("key2", "val2")])
        result = composite.serialize(items)
        # Should use INI format (first parser)
        self.assertIn("[section]", result)
        self.assertIn("key1 = val1", result)

    def test_composite_serialize_empty_parsers(self):
        composite = configargparse.CompositeConfigParser([])()
        with self.assertRaises(configargparse.ConfigFileParserException):
            composite.serialize(OrderedDict())

    def test_composite_syntax_description_names_each_format(self):
        composite = configargparse.CompositeConfigParser(
            [
                configargparse.ConfigparserConfigFileParser,
                configargparse.YAMLConfigFileParser,
                configargparse.DefaultConfigFileParser,
                configargparse.TomlConfigParser(["section"]),
                configargparse.IniConfigParser(["section"], False),
            ]
        )()
        format_names = [
            line.split(":")[0]
            for line in composite.get_syntax_description().splitlines()
            if line.startswith("[")
        ]
        self.assertEqual(
            format_names,
            ["[1] CONFIGPARSER", "[2] YAML", "[3] DEFAULT", "[4] TOML", "[5] INI"],
        )


class TestCallableDefaultConfigFiles(TestCase):
    def assert_number_of_config_file_sources(self, expected_count):
        """Check how many config file sources the last parse recorded.

        Args:
            expected_count: How many keys of get_source_to_settings_dict()
                should be config file sources.
        """
        config_file_sources = [
            source_key
            for source_key in self.parser.get_source_to_settings_dict()
            if source_key.startswith(configargparse._CONFIG_FILE_SOURCE_KEY)
        ]
        self.assertEqual(len(config_file_sources), expected_count)

    def testCallableReturningStringIO(self):
        def loader():
            return StringIO("genome=hg19\n")

        self.initParser(default_config_files=[loader])
        self.add_arg("--genome")
        ns = self.parse("")
        self.assertEqual(ns.genome, "hg19")

    def testCallableReturningNoneRaises(self):
        def empty_loader():
            return None

        self.initParser(default_config_files=[empty_loader])
        self.add_arg("--genome")
        with self.assertRaisesRegex(TypeError, r"empty_loader.*returned None"):
            self.parse("")

    def testCallableRaisingIsWrapped(self):
        def boom_loader():
            raise RuntimeError("boom")

        self.initParser(default_config_files=[boom_loader])
        self.add_arg("--genome")
        with self.assertRaises(configargparse.ConfigFileParserException) as ctx:
            self.parse("")
        self.assertIn("boom_loader", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, RuntimeError)
        self.assertEqual(str(ctx.exception.__cause__), "boom")

    def testCallableRaisingMultiArgException(self):
        # Wrapping must not depend on the original exception class accepting a
        # single string arg. CalledProcessError requires (returncode, cmd), so
        # rebuilding it with type(e)(...) would crash.
        import subprocess

        def boom_loader():
            raise subprocess.CalledProcessError(1, ["cmd"])

        self.initParser(default_config_files=[boom_loader])
        self.add_arg("--genome")
        with self.assertRaises(configargparse.ConfigFileParserException) as ctx:
            self.parse("")
        self.assertIn("boom_loader", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, subprocess.CalledProcessError)

    def testCallableStreamNameAssignmentFailsRaises(self):
        closed = []

        class NoNameStream:
            __slots__ = ("_closed",)

            def __init__(self_inner):
                self_inner._closed = False

            def read(self_inner):
                return ""

            def __iter__(self_inner):
                return iter([])

            def close(self_inner):
                self_inner._closed = True
                closed.append(self_inner)

        # NoNameStream's __slots__ allows only _closed and it has no __dict__,
        # so assigning stream.name raises AttributeError.
        def slot_loader():
            return NoNameStream()

        self.initParser(default_config_files=[slot_loader])
        self.add_arg("--genome")
        with self.assertRaises(configargparse.ConfigFileParserException) as ctx:
            self.parse("")
        self.assertIn("slot_loader", str(ctx.exception))
        self.assertIn(".name attribute could not be set", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, AttributeError)
        # The returned stream must still be closed by the parser even though
        # the .name assignment failed.
        self.assertEqual(len(closed), 1)
        self.assertTrue(closed[0]._closed)

    def testCallableInvokedEachParse(self):
        calls = {"count": 0}

        def loader():
            calls["count"] += 1
            return StringIO("genome=hg19\n")

        self.initParser(default_config_files=[loader])
        self.add_arg("--genome")
        self.parse("")
        self.parse("")
        self.parse("")
        self.assertEqual(calls["count"], 3)

    def testCallableStreamIsClosed(self):
        opened = []

        def loader():
            return TrackingStringIO("genome=hg19\n", opened)

        self.initParser(default_config_files=[loader])
        self.add_arg("--genome")
        self.parse("")
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)

    def testMixedStringsAndCallables(self):
        config_file_path = self.make_temp_file_holding_contents(
            "genome=from_file\n", suffix=".ini"
        )

        def loader():
            return StringIO("genome=from_callable\n")

        # Later entries override earlier ones, so the callable wins.
        self.initParser(default_config_files=[config_file_path, loader])
        self.add_arg("--genome")
        ns = self.parse("")
        self.assertEqual(ns.genome, "from_callable")

        # Reverse order: the file should win.
        self.initParser(default_config_files=[loader, config_file_path])
        self.add_arg("--genome")
        ns = self.parse("")
        self.assertEqual(ns.genome, "from_file")

    def testCallableWithRealFile(self):
        config_file_path = self.make_temp_file_holding_contents(
            "genome=hg19\n", suffix=".ini"
        )

        opened = []

        def loader():
            f = open(config_file_path)
            opened.append(f)
            return f

        self.initParser(default_config_files=[loader])
        self.add_arg("--genome")
        ns = self.parse("")
        self.assertEqual(ns.genome, "hg19")
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)

    def testInvalidEntryType(self):
        with self.assertRaisesRegex(TypeError, r"default_config_files\[0\]"):
            configargparse.ArgParser(default_config_files=[123])

        with self.assertRaisesRegex(TypeError, r"default_config_files\[1\]"):
            configargparse.ArgParser(default_config_files=["ok.ini", 42])

    def testFormatHelpRendersCallableName(self):
        def my_loader():
            return StringIO("")

        self.initParser(default_config_files=[my_loader])
        self.add_arg("--genome")
        help_text = self.format_help()
        self.assertIn("my_loader", help_text)
        self.assertNotIn("<function", help_text)

    def testPathLikeEntryAccepted(self):
        config_file_path = self.make_temp_file_holding_contents(
            "genome=hg19\n", suffix=".ini"
        )

        self.initParser(default_config_files=[pathlib.Path(config_file_path)])
        self.add_arg("--genome")
        ns = self.parse("")
        self.assertEqual(ns.genome, "hg19")

    def testTwoLambdasGetDistinctSourceKeys(self):
        # Two lambdas both have __name__ == "<lambda>"; without disambiguation
        # by index they'd merge into one source key in format_values output.
        l1 = lambda: StringIO("genome=hg19\n")
        l2 = lambda: StringIO("vcf=foo.vcf\n")

        self.initParser(default_config_files=[l1, l2])
        self.add_arg("--genome")
        self.add_arg("--vcf")
        self.parse("")
        self.assert_number_of_config_file_sources(2)
        # format_values should not crash either
        self.parser.format_values()

    def testEarlierStreamsClosedWhenLaterEntryFails(self):
        opened = []

        def good_loader():
            return TrackingStringIO("genome=hg19\n", opened)

        def bad_loader():
            raise RuntimeError("nope")

        self.initParser(default_config_files=[good_loader, bad_loader])
        self.add_arg("--genome")
        with self.assertRaises(configargparse.ConfigFileParserException):
            self.parse("")
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)

    def testEarlierStreamsClosedWhenParserRaisesUnexpectedException(self):
        # a custom parser raising a non-ConfigFileParserException must not
        # leak streams that were opened but not yet processed.
        class BadParser(configargparse.ConfigFileParser):
            def get_syntax_description(self):
                return ""

            def parse(self_inner, stream):
                raise ValueError("parser bug")

            def serialize(self_inner, items):
                return ""

        opened = []

        def loader_a():
            return TrackingStringIO("genome=hg19\n", opened)

        def loader_b():
            return TrackingStringIO("genome=hg20\n", opened)

        self.initParser(
            default_config_files=[loader_a, loader_b],
            config_file_parser_class=BadParser,
        )
        self.add_arg("--genome")
        with self.assertRaises(ValueError):
            self.parse("")
        self.assertEqual(len(opened), 2)
        self.assertTrue(all(s.closed for s in opened))

    def testFormatValuesHandlesPipeInCallableName(self):
        def loader():
            s = StringIO("genome=hg19\n")
            s.name = "weird|name|with|pipes"
            return s

        self.initParser(default_config_files=[loader])
        self.add_arg("--genome")
        self.parse("")
        # Must not raise on pipe characters in the source name.
        output = self.parser.format_values()
        self.assertIn("weird|name|with|pipes", output)

    def testPathLikeAndCallableEntryTreatedAsPath(self):
        # An object that is both os.PathLike and callable should be treated as
        # a path; the callable branch must not be taken first.
        config_file_path = self.make_temp_file_holding_contents(
            "genome=hg19\n", suffix=".ini"
        )

        self.initParser(
            default_config_files=[PathLikeThatFailsIfCalled(config_file_path)]
        )
        self.add_arg("--genome")
        ns = self.parse("")
        self.assertEqual(ns.genome, "hg19")

    def testDefaultStreamsClosedWhenUserConfigPhaseRaises(self):
        # when an is_config_file_arg's type= callback raises during the
        # inner argparse phase of _open_config_files, the default config
        # streams already opened earlier must be closed.
        opened = []

        def loader():
            return TrackingStringIO("genome=hg19\n", opened)

        def bad_type(value):
            raise RuntimeError("type exploded")

        self.initParser(default_config_files=[loader])
        self.add_arg("--genome")
        self.add_arg("--config", is_config_file_arg=True, type=bad_type)

        with self.assertRaises(RuntimeError):
            self.parse(["--config", "anything"])

        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].closed)

    def testTwoCallableStreamsWithSameNameDoNotCollide(self):
        # even if two callables both return streams whose .name attribute
        # is set to the same string, they must remain distinct sources.
        def loader_a():
            s = StringIO("genome=hg19\n")
            s.name = "shared"
            return s

        def loader_b():
            s = StringIO("vcf=foo.vcf\n")
            s.name = "shared"
            return s

        self.initParser(default_config_files=[loader_a, loader_b])
        self.add_arg("--genome")
        self.add_arg("--vcf")
        self.parse("")
        self.assert_number_of_config_file_sources(2)

    def testCallableStreamNameMatchingGeneratedFormatDoesNotCollide(self):
        # a callable whose returned stream's .name happens to match the
        # library's generated "<entry_label>[<i>]" pattern must not collide
        # with another callable that would otherwise generate the same name.
        def loader_with_clashing_name():
            s = StringIO("genome=hg19\n")
            s.name = "loader[1]"  # matches what the lib would generate
            return s

        def loader():  # __name__ == "loader"; at index 1
            return StringIO("vcf=foo.vcf\n")

        self.initParser(default_config_files=[loader_with_clashing_name, loader])
        self.add_arg("--genome")
        self.add_arg("--vcf")
        self.parse("")
        self.assert_number_of_config_file_sources(2)

    def testStreamClosedExactlyOnceOnSuccess(self):
        # on a successful parse, each stream's close() must be called
        # exactly once.
        close_calls = []

        class CountingStringIO(StringIO):
            def close(self_inner):
                close_calls.append(self_inner)
                super().close()

        def loader():
            return CountingStringIO("genome=hg19\n")

        self.initParser(default_config_files=[loader])
        self.add_arg("--genome")
        self.parse("")
        self.assertEqual(len(close_calls), 1)

    def testFormatHelpShowsPathForPathLikeAndCallable(self):
        # an entry that's both PathLike and callable is treated as a path
        # at runtime; format_help must label it the same way (not as
        # <callable>).
        self.initParser(
            default_config_files=[PathLikeThatFailsIfCalled("/tmp/example.ini")]
        )
        self.add_arg("--genome")
        help_text = self.format_help()
        self.assertIn("/tmp/example.ini", help_text)
        self.assertNotIn("<callable>", help_text)

    def testFormatHelpHandlesBytesReturningPathLike(self):
        # PEP 519 allows __fspath__() to return bytes. format_help must not
        # crash when joining bytes path entries with str strings.
        class BytesPath:
            def __fspath__(self_inner):
                return b"/tmp/bytes-example.ini"

        self.initParser(default_config_files=[BytesPath()])
        self.add_arg("--genome")
        # Just must not raise; the path gets rendered (subject to textwrap,
        # so we check only for stable substrings).
        help_text = self.format_help()
        # textwrap may insert a newline mid-path, so check fragments only
        self.assertIn("/tmp/bytes", help_text.replace("\n", ""))
        self.assertIn("example.ini", help_text)

    def testFormatHelpHandlesMalformedPathLike(self):
        # A PathLike whose __fspath__ returns something other than str/bytes
        # would cause os.fspath() to raise TypeError. format_help must not
        # crash on invalid input — fall back to repr().
        class BrokenPath:
            def __fspath__(self_inner):
                return None

            # A short repr with no spaces, so help text wrapping can't split it
            def __repr__(self_inner):
                return "<BrokenPath>"

        self.initParser(default_config_files=[BrokenPath()])
        self.add_arg("--genome")
        # The entry is shown by its repr() in the parenthesized list of
        # default config files, which ends with a period
        self.assertIn("(<BrokenPath>).", self.format_help())


class TestStoreFalseConfigOutput(unittest.TestCase):
    def make_parser(self, default=True):
        parser = configargparse.ArgumentParser()
        parser.add_argument(
            "-n",
            "--no-feature",
            "--disable-feature",
            action="store_false",
            dest="feature",
            default=default,
            env_var="CONFIGARGPARSE_TEST_NO_FEATURE",
        )
        parser.add_argument("--label", env_var="CONFIGARGPARSE_TEST_LABEL")
        return parser

    def assert_round_trip(self, parser, namespace, reader):
        contents = write_config_file_and_read_it_back(parser, namespace)
        restored = reader.parse_args([], config_file_contents=contents, env_vars={})
        self.assertEqual(vars(restored), vars(namespace))
        # assertEqual alone would accept 0 coming back as False
        for name, value in vars(namespace).items():
            self.assertIs(type(getattr(restored, name)), type(value), name)
        return contents

    def test_store_false_command_line_round_trip(self):
        for default in (True, False):
            for option in ("-n", "--no-feature", "--disable-feature"):
                with self.subTest(default=default, option=option):
                    parser = self.make_parser(default)
                    namespace = parser.parse_args([option], env_vars={})
                    self.assertFalse(namespace.feature)
                    self.assert_round_trip(parser, namespace, self.make_parser(default))

    def test_store_false_environment_round_trip(self):
        for default in (True, False):
            for value in ("true", "false"):
                with self.subTest(default=default, value=value):
                    parser = self.make_parser(default)
                    with mock.patch.dict(
                        os.environ,
                        {
                            "CONFIGARGPARSE_TEST_NO_FEATURE": value,
                            "CONFIGARGPARSE_TEST_LABEL": "example",
                        },
                    ):
                        namespace = parser.parse_args([])
                    self.assertEqual(namespace.feature, default and value == "false")
                    self.assertEqual(namespace.label, "example")
                    self.assert_round_trip(parser, namespace, self.make_parser(default))

    def test_store_false_config_round_trip(self):
        for default in (True, False):
            for value in ("true", "false"):
                with self.subTest(default=default, value=value):
                    parser = self.make_parser(default)
                    namespace = parser.parse_args(
                        [], config_file_contents="no-feature = " + value, env_vars={}
                    )
                    self.assert_round_trip(parser, namespace, self.make_parser(default))

    def test_non_boolean_store_false_default_round_trip(self):
        # 1 and "yes" are truthy, so writing them as-is would invoke the flag
        for default in (0, 1, "yes"):
            with self.subTest(default=default):
                parser = self.make_parser(default)
                namespace = parser.parse_args(
                    [],
                    env_vars={
                        "CONFIGARGPARSE_TEST_NO_FEATURE": "false",
                        "CONFIGARGPARSE_TEST_LABEL": "example",
                    },
                )
                self.assertIs(namespace.feature, default)
                self.assert_round_trip(parser, namespace, self.make_parser(default))

    def test_store_false_defaults_are_not_added(self):
        for default in (True, False):
            with self.subTest(default=default):
                parser = self.make_parser(default)
                namespace = parser.parse_args([], env_vars={})
                contents = self.assert_round_trip(
                    parser, namespace, self.make_parser(default)
                )
                self.assertEqual(contents, "")

    def test_other_boolean_actions_round_trip(self):
        actions = [("store_true", False, ["--feature"])]
        if hasattr(argparse, "BooleanOptionalAction"):
            actions += [
                (argparse.BooleanOptionalAction, True, ["--no-feature"]),
                (argparse.BooleanOptionalAction, False, ["--feature"]),
            ]
        for action, default, args in actions:
            with self.subTest(action=action, default=default):
                parser = configargparse.ArgumentParser()
                parser.add_argument("--feature", action=action, default=default)
                namespace = parser.parse_args(args, env_vars={})
                reader = configargparse.ArgumentParser()
                reader.add_argument("--feature", action=action, default=default)
                self.assert_round_trip(parser, namespace, reader)

    def test_store_const_round_trip(self):
        def make_parser():
            parser = configargparse.ArgumentParser()
            parser.add_argument(
                "--fast",
                action="store_const",
                const="fast",
                dest="mode",
                default="slow",
                env_var="CONFIGARGPARSE_TEST_FAST",
            )
            parser.add_argument("--label", env_var="CONFIGARGPARSE_TEST_LABEL")
            return parser

        cases = [
            (["--fast"], {}, "fast", "fast = true\n"),
            (
                [],
                {"CONFIGARGPARSE_TEST_FAST": "true", "CONFIGARGPARSE_TEST_LABEL": "x"},
                "fast",
                "fast = true\nlabel = x\n",
            ),
            (
                [],
                {"CONFIGARGPARSE_TEST_FAST": "false", "CONFIGARGPARSE_TEST_LABEL": "x"},
                "slow",
                "fast = false\nlabel = x\n",
            ),
        ]
        for args, env_vars, expected_mode, expected_contents in cases:
            with self.subTest(args=args, env_vars=env_vars):
                parser = make_parser()
                namespace = parser.parse_args(args, env_vars=env_vars)
                self.assertEqual(namespace.mode, expected_mode)
                contents = self.assert_round_trip(parser, namespace, make_parser())
                self.assertEqual(contents, expected_contents)


@unittest.skipUnless(hasattr(argparse, "BooleanOptionalAction"), "requires Python 3.9+")
class TestBooleanOptionalAliases(unittest.TestCase):
    aliases = (
        ("-f", "--feature"),
        ("--feature", "-f"),
        ("-f", "-F", "--feature"),
        ("-f", "--feature", "--enabled"),
        ("--feature", "--enabled", "-f"),
        ("--feature",),
    )

    def make_parser(self, aliases):
        parser = configargparse.ArgumentParser()
        parser.add_argument(
            *aliases,
            action=argparse.BooleanOptionalAction,
            dest="feature",
            env_var="FEATURE",
        )
        return parser

    def test_boolean_values_from_environment_and_config(self):
        values = (
            ("true", True),
            ("yes", True),
            ("on", True),
            ("1", True),
            ("false", False),
            ("no", False),
            ("off", False),
            ("0", False),
        )
        for aliases in self.aliases:
            for value, expected in values:
                for source in ("environment", "config"):
                    with self.subTest(aliases=aliases, value=value, source=source):
                        parser = self.make_parser(aliases)
                        kwargs = {"env_vars": {}}
                        if source == "environment":
                            kwargs["env_vars"] = {"FEATURE": value}
                        else:
                            kwargs["config_file_contents"] = "feature=" + value
                        self.assertIs(parser.parse_args([], **kwargs).feature, expected)

    def test_environment_precedence_and_command_line_overrides(self):
        for aliases in self.aliases:
            parser = self.make_parser(aliases)
            negative_options = [
                "--no-" + option[2:] for option in aliases if option.startswith("--")
            ]
            for value, config_value, expected in (
                ("false", "true", False),
                ("true", "false", True),
            ):
                for option in (None, *aliases, *negative_options):
                    with self.subTest(aliases=aliases, value=value, option=option):
                        args = [] if option is None else [option]
                        wanted = (
                            expected
                            if option is None
                            else not option.startswith("--no-")
                        )
                        result = parser.parse_args(
                            args,
                            env_vars={"FEATURE": value},
                            config_file_contents="feature=" + config_value,
                        )
                        self.assertIs(result.feature, wanted)
            self.assertIsNone(parser.parse_args([], env_vars={}).feature)
            self.assertTrue(
                parser.parse_args(
                    [], env_vars={}, config_file_contents="feature=true"
                ).feature
            )
            self.assertFalse(
                parser.parse_args([], env_vars={"FEATURE": "false"}).feature
            )
            self.assertIsNone(parser.parse_args([], env_vars={}).feature)

    def test_saved_configuration_round_trip(self):
        for aliases in self.aliases:
            for option in ("--feature", "--no-feature"):
                with self.subTest(aliases=aliases, option=option):
                    parser = self.make_parser(aliases)
                    namespace = parser.parse_args([option], env_vars={})
                    contents = write_config_file_and_read_it_back(parser, namespace)
                    self.assertIs(
                        self.make_parser(aliases)
                        .parse_args([], env_vars={}, config_file_contents=contents)
                        .feature,
                        namespace.feature,
                    )

    def test_no_prefixed_config_key_sets_the_opposite_value(self):
        for aliases in (("-f", "--feature"), ("--feature",)):
            for key in ("no-feature", "--no-feature"):
                for value, expected in (("true", False), ("false", True)):
                    with self.subTest(aliases=aliases, key=key, value=value):
                        namespace = self.make_parser(aliases).parse_args(
                            [],
                            env_vars={},
                            config_file_contents="%s = %s" % (key, value),
                        )
                        self.assertIs(namespace.feature, expected)

    def test_short_only_aliases_reject_false_values(self):
        for aliases in (("-f",), ("-f", "-F")):
            with self.subTest(aliases=aliases):
                parser = replace_error_method(self.make_parser(aliases))
                self.assertIs(
                    parser.parse_args([], env_vars={"FEATURE": "true"}).feature, True
                )
                with self.assertRaisesRegex(
                    argparse.ArgumentError, "FEATURE can't set feature to False"
                ):
                    parser.parse_args([], env_vars={"FEATURE": "false"})

    def test_negative_forms_that_dont_start_with_two_dashes(self):
        # From Python 3.15, argparse also adds '-nofeature' after '-feature' and
        # '++no-feature' after '++feature'. Earlier versions add no negative
        # form for these, so a false value can't be set there.
        for prefix_chars, option in (("-", "-feature"), ("+", "++feature")):
            with self.subTest(option=option):
                parser = replace_error_method(
                    configargparse.ArgumentParser(prefix_chars=prefix_chars)
                )
                action = parser.add_argument(
                    option,
                    action=argparse.BooleanOptionalAction,
                    dest="feature",
                    env_var="FEATURE",
                )
                self.assertIs(
                    parser.parse_args([], env_vars={"FEATURE": "true"}).feature, True
                )
                if len(action.option_strings) == 1:
                    with self.assertRaisesRegex(
                        argparse.ArgumentError, "FEATURE can't set feature to False"
                    ):
                        parser.parse_args([], env_vars={"FEATURE": "false"})
                    continue
                self.assertIs(
                    parser.parse_args([], env_vars={"FEATURE": "false"}).feature, False
                )
                if option == "++feature":
                    namespace = parser.parse_args(
                        [], env_vars={}, config_file_contents="no-feature = true"
                    )
                    self.assertIs(namespace.feature, False)


class TestConfigparserDefaultSection(TestCase):
    def test_default_only_values(self):
        parser = configargparse.ConfigparserConfigFileParser()
        contents = (
            "[DEFAULT]\nName = alice\nlabel = %(name)s\nempty =\n"
            "items = ['a', 'b']\nmessage = first\n    second\n"
        )
        self.assertEqual(
            parser.parse(StringIO(contents)),
            OrderedDict(
                [
                    ("name", "alice"),
                    ("label", "alice"),
                    ("empty", ""),
                    ("items", ["a", "b"]),
                    ("message", "first second"),
                ]
            ),
        )

    def test_saved_config_round_trip(self):
        parser = self.initParser(
            config_file_parser_class=configargparse.ConfigparserConfigFileParser
        )
        parser.add_argument("--name")
        parser.add_argument("--count", type=int)
        parser.add_argument("--matrix", nargs="+", type=json.loads)
        expected = parser.parse_args(
            ["--name", "alice", "--count", "3", "--matrix", "[1, 2]", "[3, 4]"]
        )
        contents = write_config_file_and_read_it_back(parser, expected)
        actual = parser.parse_args([], config_file_contents=contents)
        self.assertEqual(actual, expected)

    def test_blank_line_in_value_does_not_start_a_new_key(self):
        # how an earlier version of serialize() wrote a value with a blank line
        contents = "[DEFAULT]\nnotes = line1\n\t\n\tupload-url = https://x\n"
        self.assertEqual(
            configargparse.ConfigparserConfigFileParser().parse(StringIO(contents)),
            OrderedDict([("notes", "line1  upload-url = https://x")]),
        )

    def test_named_sections_keep_default_overrides(self):
        parser = configargparse.ConfigparserConfigFileParser()
        contents = (
            "[DEFAULT]\nname = default\nshared = value\n"
            "[first]\nname = alice\n[first_extra]\nname = bob\n"
        )
        self.assertEqual(
            parser.parse(StringIO(contents)),
            OrderedDict([("name", "bob"), ("shared", "value")]),
        )

    def test_serialized_values_read_back_as_the_expected_value_without_warnings(self):
        parser = configargparse.ConfigparserConfigFileParser()
        for value, expected in (
            ("file[0-9].txt", "file[0-9].txt"),
            ("[value6.1, value6.2]", "[value6.1, value6.2]"),
            (["a", "50%"], ["a", "50%"]),
            ("50%", "50%"),
            ("100%%", "100%%"),
            ("%(asctime)s %(message)s", "%(asctime)s %(message)s"),
            ("a;b", "a;b"),
            ("#ff0000", "#ff0000"),
            (";", ";"),
            # in a list or a quoted string, "#" and ";" are written as escapes
            (["issue #12", "b ; c"], ["issue #12", "b ; c"]),
            ("Results [v2] ; final", "Results [v2] ; final"),
            ([pathlib.Path("a.txt"), float("inf")], ["a.txt", "inf"]),
            ([["a", 1, pathlib.Path("b.txt")], ["c"]], [["a", 1, "b.txt"], ["c"]]),
            # a tuple reads back as the string the option's type was given
            ((224, 224), "(224, 224)"),
            ([(1, 2), (3, 4)], [(1, 2), (3, 4)]),
            ("line1\nline2", "line1 line2"),
            # a blank line or a lone \r must not start a new key
            ("x\n\nother = injected", "x  other = injected"),
            ("x\rother = injected", "x other = injected"),
        ):
            with self.subTest(value=value), warnings.catch_warnings():
                # no case here is warned about, including those where newlines
                # become spaces or a tuple or Path becomes its str()
                warnings.simplefilter("error")
                # newline=None reads a lone \r as a line break, the way open()
                # does when parse_args reads a config file
                stream = StringIO(parser.serialize({"key": value}), newline=None)
                self.assertEqual(parser.parse(stream), OrderedDict([("key", expected)]))

    def test_serialize_warns_about_values_read_back_cut_short(self):
        parser = configargparse.ConfigparserConfigFileParser()
        for value in ("run #1", "a ; b", "a\t#b"):
            with self.subTest(value=value):
                with self.assertWarnsRegex(
                    UserWarning, "value of key .*cut short"
                ) as caught:
                    contents = parser.serialize({"key": value})
                self.assertIn("\nkey=", contents)
                # the value may be a secret, so the warning must not show it
                self.assertNotIn(str(value), str(caught.warning))

    def test_bad_interpolation_raises_config_file_parser_exception(self):
        parser = configargparse.ConfigparserConfigFileParser()
        for contents, problem in (
            ("[DEFAULT]\npct = 50%secret\n", "isn't '%%'.*Write %%"),
            ("[section]\npct = 50%secret\n", "isn't '%%'.*Write %%"),
            (
                "[section]\nlabel = %(missing)ssecret\n",
                "isn't in the same section or in \\[DEFAULT\\].*Write %%",
            ),
            (
                "[DEFAULT]\npath = /base\n[job]\npath = %(path)ssecret\n",
                "refer back to it",
            ),
        ):
            with self.subTest(contents=contents):
                with self.assertRaisesRegex(
                    configargparse.ConfigFileParserException,
                    "Couldn't parse config file value for (pct|label|path): .*"
                    + problem,
                ) as caught:
                    parser.parse(StringIO(contents))
                # the value may be a secret, so neither the message nor a
                # traceback (which would show a chained exception) may show it
                exception = caught.exception
                self.assertNotIn(
                    "secret",
                    "".join(
                        traceback.format_exception(
                            type(exception), exception, exception.__traceback__
                        )
                    ),
                )


class TestFalseFlagPrecedence(unittest.TestCase):
    false_values = ("false", "no", "off", "0", "FALSE")
    actions = (("store_true", False), ("store_false", True))

    def test_environment_overrides_config(self):
        for action_name, expected in self.actions:
            for value in self.false_values:
                for config_key in ("feature", "alias"):
                    with self.subTest(action=action_name, value=value, key=config_key):
                        parser = configargparse.ArgumentParser()
                        action = parser.add_argument(
                            "--feature",
                            "--alias",
                            action=action_name,
                            env_var="FEATURE",
                        )
                        result = parser.parse_args(
                            [],
                            env_vars={"FEATURE": value},
                            config_file_contents=config_key + "=true",
                        )
                        self.assertIs(result.feature, expected)
                        self.assertEqual(
                            parser.get_source_to_settings_dict(),
                            {"environment_variables": {"FEATURE": (action, value)}},
                        )
                        self.assertIn("Environment Variables:", parser.format_values())
                        self.assertIn(value, parser.format_values())
                        self.assertNotIn("Config File", parser.format_values())

    def test_later_config_overrides_earlier_config(self):
        with tempfile.TemporaryDirectory() as directory:
            lower = pathlib.Path(directory) / "default.ini"
            higher = pathlib.Path(directory) / "user.ini"
            lower.write_text("feature=true\n")
            for action_name, expected in self.actions:
                for value in self.false_values:
                    with self.subTest(action=action_name, value=value):
                        higher.write_text("alias=" + value + "\n")
                        parser = configargparse.ArgumentParser(
                            default_config_files=[lower, higher]
                        )
                        action = parser.add_argument(
                            "--feature", "--alias", action=action_name
                        )
                        self.assertIs(
                            parser.parse_args([], env_vars={}).feature, expected
                        )
                        self.assertEqual(
                            parser.get_source_to_settings_dict(),
                            {
                                f"{configargparse._CONFIG_FILE_SOURCE_KEY}|{higher}": {
                                    "alias": (action, value)
                                }
                            },
                        )
                        self.assertNotIn(str(lower), parser.format_values())

    def test_command_line_overrides_false_environment_and_config(self):
        for action_name, expected in self.actions:
            for value in self.false_values:
                for option in ("-f", "--feature", "--alias"):
                    with self.subTest(action=action_name, value=value, option=option):
                        parser = configargparse.ArgumentParser()
                        parser.add_argument(
                            "-f",
                            "--feature",
                            "--alias",
                            action=action_name,
                            env_var="FEATURE",
                        )
                        result = parser.parse_args(
                            [option],
                            env_vars={"FEATURE": value},
                            config_file_contents="feature=false",
                        )
                        self.assertIs(result.feature, not expected)
                        self.assertEqual(
                            parser.get_source_to_settings_dict(),
                            {"command_line": {"": (None, [option])}},
                        )

    def test_false_does_not_replace_custom_default(self):
        for action_name, _ in self.actions:
            with self.subTest(action=action_name):
                parser = configargparse.ArgumentParser()
                parser.add_argument(
                    "--feature", action=action_name, default="unset", env_var="FEATURE"
                )
                result = parser.parse_args(
                    [],
                    env_vars={"FEATURE": "false"},
                    config_file_contents="feature=true",
                )
                self.assertEqual(result.feature, "unset")

    def test_false_does_not_select_mutually_exclusive_member(self):
        parser = configargparse.ArgumentParser()
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--feature", action="store_true", env_var="FEATURE")
        group.add_argument("--other", action="store_true")
        result = parser.parse_args(
            [],
            env_vars={"FEATURE": "false"},
            config_file_contents="feature=true\nother=true",
        )
        self.assertFalse(result.feature)
        self.assertTrue(result.other)
        # Two active members within a single source must still conflict.
        with self.assertRaises(SystemExit), captured_output():
            parser.parse_args(
                [], env_vars={}, config_file_contents="feature=true\nother=true"
            )
        # A false value alone must not satisfy a required group.
        with self.assertRaises(SystemExit), captured_output():
            parser.parse_args([], env_vars={"FEATURE": "false"})

    def test_shared_environment_variable_overrides_both_actions(self):
        parser = configargparse.ArgumentParser()
        parser.add_argument("--first", action="store_true", env_var="FEATURE")
        parser.add_argument("--second", action="store_true", env_var="FEATURE")
        result = parser.parse_args(
            [],
            env_vars={"FEATURE": "false"},
            config_file_contents="first=true\nsecond=true",
        )
        self.assertFalse(result.first)
        self.assertFalse(result.second)

    def test_flag_actions_reset_between_parses(self):
        parser = configargparse.ArgumentParser()
        parser.add_argument("--feature", action="store_true", env_var="FEATURE")
        self.assertFalse(
            parser.parse_args(
                [], env_vars={"FEATURE": "false"}, config_file_contents="feature=true"
            ).feature
        )
        self.assertTrue(
            parser.parse_args(
                [], env_vars={}, config_file_contents="feature=true"
            ).feature
        )

    def test_empty_environment_value_still_falls_back_to_config(self):
        parser = configargparse.ArgumentParser()
        parser.add_argument("--items", nargs="+", env_var="ITEMS")
        result = parser.parse_args(
            [], env_vars={"ITEMS": ""}, config_file_contents="items=[one,two]"
        )
        self.assertEqual(result.items, ["one", "two"])
        self.assertNotIn("environment_variables", parser.get_source_to_settings_dict())

    def test_empty_list_does_not_override_lower_priority_list(self):
        # only flags are tracked, so an empty list still falls back to lower
        # priority sources
        parser = configargparse.ArgumentParser()
        parser.add_argument("--item", action="append", env_var="ITEM")
        result = parser.parse_args(
            [], env_vars={"ITEM": "[]"}, config_file_contents="item = [a]"
        )
        self.assertEqual(result.item, ["a"])

        with tempfile.TemporaryDirectory() as directory:
            lower = pathlib.Path(directory) / "default.ini"
            higher = pathlib.Path(directory) / "user.ini"
            lower.write_text("item = [a]\n")
            higher.write_text("item = []\n")
            parser = configargparse.ArgumentParser(default_config_files=[lower, higher])
            parser.add_argument("--item", action="append")
            self.assertEqual(parser.parse_args([], env_vars={}).item, ["a"])

    def test_zero_count_overrides_lower_priority_count(self):
        # a count arg is a flag too, and "0" is one of its false values
        parser = configargparse.ArgumentParser()
        parser.add_argument("-v", "--verbose", action="count", env_var="VERBOSE")
        result = parser.parse_args(
            [], env_vars={"VERBOSE": "0"}, config_file_contents="verbose = 3"
        )
        self.assertIsNone(result.verbose)


class TestOptionsBeforeARemainderPositional(TestCase):
    """The options before a REMAINDER positional's args are this parser's own.

    A REMAINDER positional takes every arg from its first one on, so the
    values of the options before it, however many there are, must not be
    mistaken for its first arg.
    """

    def make_parser_with_a_remainder_positional(self, **kwargs):
        parser = configargparse.ArgumentParser(**kwargs)
        parser.add_argument("--files", nargs="+")
        parser.add_argument("--tag", action="append", env_var="TAG")
        parser.add_argument("--output")
        parser.add_argument("--level", nargs="?")
        parser.add_argument("--name")
        parser.add_argument("rest", nargs=argparse.REMAINDER)
        return parser

    def test_command_line_option_after_a_variable_length_option_overrides(self):
        for args in (
            ["--files", "a", "b", "--tag", "cli"],
            ["--out", "x", "--tag", "cli"],
            ["--level", "debug", "--tag", "cli"],
        ):
            with self.subTest(args=args):
                parser = self.make_parser_with_a_remainder_positional()
                ns = parser.parse_args(
                    args, config_file_contents="tag = cfg", env_vars={}
                )
                self.assertEqual(ns.tag, ["cli"])
                ns = parser.parse_args(args, env_vars={"TAG": "[env]"})
                self.assertEqual(ns.tag, ["cli"])

    def test_config_file_arg_after_a_variable_length_option_is_read(self):
        config_file_path = self.make_temp_file_holding_contents("name = from_file\n")
        parser = self.make_parser_with_a_remainder_positional()
        parser.add_argument("-c", is_config_file=True)
        ns = parser.parse_args(["--files", "a", "-c", config_file_path, "cmd"])
        self.assertEqual(ns.name, "from_file")
        self.assertEqual(ns.rest, ["cmd"])

    def test_mutually_exclusive_option_after_a_variable_length_option(self):
        parser = self.make_parser_with_a_remainder_positional()
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--fast", action="store_true")
        group.add_argument("--slow", action="store_true")
        ns = parser.parse_args(
            ["--files", "x", "--slow"], config_file_contents="fast = true", env_vars={}
        )
        self.assertFalse(ns.fast)
        self.assertTrue(ns.slow)

    def test_written_config_file_holds_options_after_a_variable_length_option(self):
        output_path = self.make_unused_path_in_a_new_temp_dir()
        parser = self.make_parser_with_a_remainder_positional(
            args_for_writing_out_config_file=["-w"]
        )
        with captured_output(), self.assertRaises(SystemExit):
            parser.parse_args(
                ["--files", "a", "--name", "x", "-w", output_path], env_vars={}
            )
        with open(output_path) as output_file:
            self.assertEqual(output_file.read(), 'files = ["a"]\nname = x\n')

    def test_ignore_help_args_drops_help_after_a_variable_length_option(self):
        parser = self.make_parser_with_a_remainder_positional()
        ns, _ = parser.parse_known_args(
            ["--files", "a", "-h"], ignore_help_args=True, env_vars={}
        )
        self.assertEqual(ns.files, ["a"])


################################################################################
# since configargparse should work as a drop-in replacement for argparse
# in all situations, run argparse unittests on configargparse by modifying
# their source code to use configargparse.ArgumentParser

try:
    import test.test_argparse
except ImportError:
    logging.error(
        "\n\n"
        "============================\n"
        "ERROR: Many tests couldn't be run because 'import test.test_argparse' "
        "failed. Try building/installing python from source rather than through"
        " a package manager.\n"
        "============================\n"
    )

    class TestArgparseOwnTestSuiteIsUnavailable(unittest.TestCase):
        def test_argparse_own_test_suite_was_not_run(self):
            """Show the missing argparse tests as a skip in the test summary."""
            self.skipTest(
                "'import test.test_argparse' failed, so argparse's own tests "
                "weren't run against configargparse"
            )

else:
    test_argparse_source_file = inspect.getsourcefile(test.test_argparse)
    test_argparse_source_code = inspect.getsource(test.test_argparse)
    configargparse_dir = os.path.dirname(
        os.path.abspath(configargparse.__file__)
    ).replace("\\", "/")
    test_argparse_source_code = (
        test_argparse_source_code.replace(
            "argparse.ArgumentParser", "configargparse.ArgumentParser"
        )
        .replace("TestHelpFormattingMetaclass", "_TestHelpFormattingMetaclass")
        .replace("test_main", "_test_main")
        .replace(
            "import argparse\n        parser = configargparse.ArgumentParser()",
            "import sys; "
            f"sys.path.insert(0, {configargparse_dir!r}); "
            "import configargparse\n        parser = configargparse.ArgumentParser()",
        )
        .replace(
            "self.assertEqual(cm.filename, __file__)",
            "self.assertEqual(cm.filename, test_argparse_source_file)",
        )
    )

    # pytest tries to collect tests from TestHelpFormattingMetaclass, and
    # test_main, and raises a warning when it finds it's not a test class
    # nor test function. Renaming TestHelpFormattingMetaclass and test_main
    # prevents pytest from trying.

    # Python 3.14+ TestProgName runs its source in an isolated (-I) subprocess,
    # which ignores PYTHONPATH and the working directory, so the script puts
    # the directory of the configargparse under test on sys.path itself (with
    # forward slashes so Windows backslashes aren't read as string escapes),
    # on the same line as the import to keep line numbers unchanged.
    # Several 3.14+ tests also check that a warning's filename is the test
    # file, so the exec'd code is compiled under the upstream test file's path
    # (which also gives tracebacks the right file and line numbers) and
    # compared against that path.

    # run or debug a subset of the argparse tests
    # test_argparse_source_code = test_argparse_source_code.replace(
    #   "(TestCase)", "").replace(
    #   "(ParserTestCase)", "").replace(
    #   "(HelpTestCase)", "").replace(
    #   ", TestCase", "").replace(
    #   ", ParserTestCase", "")
    # test_argparse_source_code = test_argparse_source_code.replace(
    #   "class TestMessageContentError", "class TestMessageContentError(TestCase)")

    exec(compile(test_argparse_source_code, test_argparse_source_file, "exec"))

    # Python 3.15+ TestLazyImports checks which modules importing argparse
    # pulls in, which says nothing about configargparse, and its subprocess
    # scripts would call configargparse.ArgumentParser without importing it
    globals().pop("TestLazyImports", None)
