import os, sys, re
import logging
import inspect
import configargparse
from textwrap import dedent as dd

################################################################################
# since configargparse should work as a drop-in replacement for argparse
# in all situations, run argparse unittests on configargparse by modifying
# their source code to use configargparse.ArgumentParser

test_argparse_source_code = None
remove_i18n_helper = False
try:
    import test.test_argparse
    test_argparse_source_code = inspect.getsource(test.test_argparse)
except ImportError:
    # Try loading it from a local copy, since it's just one file we can get from
    # the CPython source repo.
    py_version = sys.version.split()[0]

    try:
        with open(os.path.join( os.path.dirname(__file__), '..',
                                f"local_test_argparse_{py_version}.py" )) as fh:
            test_argparse_source_code = fh.read()
            remove_i18n_helper = True # See below
    except FileNotFoundError:
        link = f"https://github.com/python/cpython/raw/refs/tags/v{py_version}/Lib/test/test_argparse.py"

        logging.warning(dd(f"""\

            ============================
            WARNING: Many tests couldn't be run because 'import test.test_argparse'
            failed. Try building/installing python from source rather than through
            a package manager, or else just fetch the file:

            $ wget -O local_test_argparse_{py_version}.py {link}
            ============================
            """))

if test_argparse_source_code:

    # pytest tries to collect tests from TestHelpFormattingMetaclass, and
    # test_main, and raises a warning when it finds it's not a test class
    # nor test function. Renaming TestHelpFormattingMetaclass and test_main
    # prevents pytest from trying.

    replacements = { 'argparse.ArgumentParser': 'configargparse.ArgumentParser',
                     'TestHelpFormattingMetaclass': '_TestHelpFormattingMetaclass',
                     'test_main': '_test_main' }

    if remove_i18n_helper:
        # If we have just downloaded the single source file for the test then we also need
        # to remove an indirect dependency on test.test_tools and skip the i18n tests.
        replacements.update({ r'from test.support.i18n_helper import .*': '',
                              r'TestTranslationsBase': 'unittest.TestCase',
                              r'^((\s*)def test_translations)': r'\2@unittest.skip\n\1' })


    for search, replace in replacements.items():
        search = f"(?:\\b|^){search}(?:\\b|$)"
        test_argparse_source_code = re.sub(search,
                                           replace,
                                           test_argparse_source_code,
                                           flags=re.MULTILINE)

    exec(test_argparse_source_code)

    # print argparse unittest source code
    def print_source_code(source_code, line_numbers, context_lines=10):
         for n in line_numbers:
             logging.debug("##### Code around line %s #####" % n)
             lines_to_print = set(range(n - context_lines, n + context_lines))
             for n2, line in enumerate(source_code.split("\n"), 1):
                 if n2 in lines_to_print:
                     logging.debug("%s %5d: %s" % (
                        "**" if n2 == n else "  ", n2, line))
    #print_source_code(test_argparse_source_code, [4540, 4565])
