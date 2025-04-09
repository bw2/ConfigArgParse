import logging
from textwrap import dedent as dd

################################################################################
# since configargparse should work as a drop-in replacement for argparse
# in all situations, run argparse unittests on configargparse by modifying
# their source code to use configargparse.ArgumentParser

try:
    import test.test_argparse
    #Sig = test.test_argparse.Sig
    #NS = test.test_argparse.NS
except ImportError:
    logging.warning(dd("""\

        ============================
        WARNING: Many tests couldn't be run because 'import test.test_argparse'
        failed. Try building/installing python from source rather than through
        a package manager.
        ============================
        """))
else:
    test_argparse_source_code = inspect.getsource(test.test_argparse)
    test_argparse_source_code = test_argparse_source_code.replace(
        'argparse.ArgumentParser', 'configargparse.ArgumentParser').replace(
        'TestHelpFormattingMetaclass', '_TestHelpFormattingMetaclass').replace(
        'test_main', '_test_main')

    # pytest tries to collect tests from TestHelpFormattingMetaclass, and
    # test_main, and raises a warning when it finds it's not a test class
    # nor test function. Renaming TestHelpFormattingMetaclass and test_main
    # prevents pytest from trying.

    # run or debug a subset of the argparse tests
    #test_argparse_source_code = test_argparse_source_code.replace(
    #   "(TestCase)", "").replace(
    #   "(ParserTestCase)", "").replace(
    #   "(HelpTestCase)", "").replace(
    #   ", TestCase", "").replace(
    #   ", ParserTestCase", "")
    #test_argparse_source_code = test_argparse_source_code.replace(
    #   "class TestMessageContentError", "class TestMessageContentError(TestCase)")

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
