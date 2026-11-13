import json
import os
import tempfile

import configargparse
from tests.test_end_of_options import TestCaseThatChecksParseArgsErrors


class TestListElementsFromConfigFilesAndEnvVarsMustBeReadAsValues(
    TestCaseThatChecksParseArgsErrors
):
    """A list from a config file or env var becomes separate command line args.

    argparse must read each element as a value, so an element it would read as
    an option, or as the name of a file of args, is refused.
    """

    def test_list_element_cannot_be_read_as_an_option(self):
        # otherwise an env var or config file list element could set an option
        # that isn't meant to be set from there, or read another config file
        for prefix in ("-", "+"):
            for source, kwargs in (
                (
                    "environment",
                    {"env_vars": {"ITEMS": '["b", "%sadmin"]' % (prefix * 2)}},
                ),
                (
                    "environment config file arg",
                    {"env_vars": {"ITEMS": '["b", "%sc", "other.ini"]' % prefix}},
                ),
                (
                    "environment nested list",
                    {"env_vars": {"PAIR": '[["a", "%sadmin"]]' % (prefix * 2)}},
                ),
                (
                    "config",
                    {
                        "env_vars": {},
                        "config_file_contents": "items = [b, %sadmin]" % (prefix * 2),
                    },
                ),
            ):
                with self.subTest(prefix=prefix, source=source):
                    parser = configargparse.ArgumentParser(prefix_chars=prefix)
                    parser.add_argument(prefix + "c", is_config_file=True)
                    parser.add_argument(
                        prefix * 2 + "items", nargs="+", env_var="ITEMS"
                    )
                    parser.add_argument(
                        prefix * 2 + "pair", nargs=2, action="append", env_var="PAIR"
                    )
                    parser.add_argument(prefix * 2 + "admin", action="store_true")
                    error_line = self.parse_args_expecting_exit_and_return_error_line(
                        parser, [], **kwargs
                    )
                    self.assertIn(
                        "can't have a list element that starts with '%s'" % prefix,
                        error_line,
                    )
                    # the error must not show the element, which may be a secret
                    self.assertNotIn("admin", error_line)
                    self.assertNotIn("%sc" % prefix, error_line)

    def test_list_element_cannot_name_a_file_of_args(self):
        # argparse would replace it with the args in that file, which could set
        # any option
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("--admin\n")
        try:
            for source, kwargs in (
                (
                    "environment",
                    {"env_vars": {"ITEMS": json.dumps(["b", "@" + f.name])}},
                ),
                (
                    "environment nested list",
                    {"env_vars": {"PAIR": json.dumps([["a", "@" + f.name]])}},
                ),
                (
                    "config",
                    {
                        "env_vars": {},
                        "config_file_contents": "items = "
                        + json.dumps(["b", "@" + f.name]),
                    },
                ),
            ):
                with self.subTest(source=source):
                    parser = configargparse.ArgumentParser(fromfile_prefix_chars="@")
                    parser.add_argument("--items", nargs="+", env_var="ITEMS")
                    parser.add_argument(
                        "--pair", nargs=2, action="append", env_var="PAIR"
                    )
                    parser.add_argument("--admin", action="store_true")
                    error_line = self.parse_args_expecting_exit_and_return_error_line(
                        parser, [], **kwargs
                    )
                    self.assertIn(
                        "can't have a list element that starts with '@', since it "
                        "would read command line args from a file",
                        error_line,
                    )
                    # the error must not show the element, which may be a secret
                    self.assertNotIn(os.path.basename(f.name), error_line)
        finally:
            os.remove(f.name)

        # a parser that doesn't read files of args takes such an element as is
        parser = configargparse.ArgumentParser()
        parser.add_argument("--items", nargs="+", env_var="ITEMS")
        result = parser.parse_args([], env_vars={"ITEMS": '["@oncall", "b"]'})
        self.assertEqual(result.items, ["@oncall", "b"])

    def test_list_element_can_be_a_negative_number_or_a_lone_dash(self):
        parser = configargparse.ArgumentParser()
        parser.add_argument("--numbers", nargs="+", type=float, env_var="NUMBERS")
        parser.add_argument("--files", nargs="+", env_var="FILES")
        result = parser.parse_args(
            [], env_vars={"NUMBERS": '["-5", "-1.5", "2"]', "FILES": '["-", "a"]'}
        )
        self.assertEqual(result.numbers, [-5.0, -1.5, 2.0])
        self.assertEqual(result.files, ["-", "a"])

    def test_list_element_cannot_be_a_negative_number_that_is_also_an_option(self):
        # with an option that looks like a negative number, argparse reads
        # "-1" as that option rather than as a value
        parser = configargparse.ArgumentParser()
        parser.add_argument("-1", dest="one", action="store_true")
        parser.add_argument("--numbers", nargs="+", env_var="NUMBERS")
        self.assertIn(
            "can't have a list element that starts with '-'",
            self.parse_args_expecting_exit_and_return_error_line(
                parser, [], env_vars={"NUMBERS": '["2", "-1"]'}
            ),
        )
