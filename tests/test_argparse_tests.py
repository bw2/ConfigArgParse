import os, sys
import logging
import inspect
import configargparse
from textwrap import dedent as dd

################################################################################
# since configargparse should work as a drop-in replacement for argparse
# in all situations, run argparse unittests on configargparse by modifying
# their source code to use configargparse.ArgumentParser

test_argparse_source_code = None
try:
    import test.test_argparse

    test_argparse_source_code = inspect.getsource(test.test_argparse)
except ImportError:
    # Try loading it from a local copy, since it's just one file we can get from
    # the CPython source repo.
    py_version = sys.version.split()[0]

    try:
        with open(
            os.path.join(
                os.path.dirname(__file__), "..", f"local_test_argparse_{py_version}.py"
            )
        ) as fh:
            test_argparse_source_code = fh.read()
    except FileNotFoundError:
        link = f"https://github.com/python/cpython/raw/refs/tags/v{py_version}/Lib/test/test_argparse.py"

        logging.warning(
            dd(
                f"""\

            ============================
            WARNING: Many tests couldn't be run because 'import test.test_argparse'
            failed. Try building/installing python from source rather than through
            a package manager, or else just fetch the file:

            $ wget -O local_test_argparse_{py_version}.py {link}
            ============================
            """
            )
        )

if test_argparse_source_code:
    test_argparse_source_code = (
        test_argparse_source_code.replace(
            "argparse.ArgumentParser", "configargparse.ArgumentParser"
        )
        .replace("TestHelpFormattingMetaclass", "_TestHelpFormattingMetaclass")
        .replace("test_main", "_test_main")
        .replace("test_exit_on_error", "_test_exit_on_error")
    )

    # pytest tries to collect tests from TestHelpFormattingMetaclass, and
    # test_main, and raises a warning when it finds it's not a test class
    # nor test function. Renaming TestHelpFormattingMetaclass and test_main
    # prevents pytest from trying.

    # run or debug a subset of the argparse tests
    # test_argparse_source_code = test_argparse_source_code.replace(
    #   "(TestCase)", "").replace(
    #   "(ParserTestCase)", "").replace(
    #   "(HelpTestCase)", "").replace(
    #   ", TestCase", "").replace(
    #   ", ParserTestCase", "")
    # test_argparse_source_code = test_argparse_source_code.replace(
    #   "class TestMessageContentError", "class TestMessageContentError(TestCase)")

    exec(test_argparse_source_code)

    # print argparse unittest source code
    def print_source_code(source_code, line_numbers, context_lines=10):
        for n in line_numbers:
            logging.debug("##### Code around line %s #####" % n)
            lines_to_print = set(range(n - context_lines, n + context_lines))
            for n2, line in enumerate(source_code.split("\n"), 1):
                if n2 in lines_to_print:
                    logging.debug("%s %5d: %s" % ("**" if n2 == n else "  ", n2, line))

    # print_source_code(test_argparse_source_code, [4540, 4565])
