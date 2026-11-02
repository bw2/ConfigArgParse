import contextlib
import io
import json
import os
import tempfile
import unittest

import configargparse


class TestEndOfOptions(unittest.TestCase):
    def parse_args_expecting_exit_and_return_error_line(self, parser, args, **kwargs):
        """Runs parse_args, checks that it exits, and returns what it printed last.

        Args:
            parser: the parser to run.
            args: the command line args.
            **kwargs: passed on to parse_args.

        Returns:
            str: the last line written to stderr, which holds the error message.
        """
        stderr = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(stderr):
            parser.parse_args(args, **kwargs)
        return stderr.getvalue().strip().splitlines()[-1]

    def test_positional_option_names_do_not_override_other_sources(self):
        for source in ("environment", "config"):
            for prefix in ("-", "+"):
                long_option = prefix * 2 + "level"
                short_option = prefix + "l"
                for positionals in (
                    [long_option],
                    [long_option + "=positional"],
                    [short_option],
                    [short_option + "=positional"],
                    ["--", long_option],
                ):
                    with self.subTest(
                        source=source, prefix=prefix, positionals=positionals
                    ):
                        parser = configargparse.ArgumentParser(prefix_chars=prefix)
                        parser.add_argument(
                            short_option, long_option, env_var="LEVEL", required=True
                        )
                        parser.add_argument("files", nargs="+")
                        kwargs = (
                            {"env_vars": {"LEVEL": "info"}}
                            if source == "environment"
                            else {
                                "config_file_contents": "level = info",
                                "env_vars": {},
                            }
                        )
                        result = parser.parse_args(["--"] + positionals, **kwargs)
                        self.assertEqual(result.level, "info")
                        self.assertEqual(result.files, positionals)

                        # A real option before the marker still takes precedence.
                        result = parser.parse_args(
                            [long_option, "debug", "--"] + positionals, **kwargs
                        )
                        self.assertEqual(result.level, "debug")
                        self.assertEqual(result.files, positionals)

    def test_positional_group_member_does_not_override_other_sources(self):
        for source in ("environment", "config"):
            with self.subTest(source=source):
                parser = configargparse.ArgumentParser()
                group = parser.add_mutually_exclusive_group(required=True)
                group.add_argument("--json", action="store_true", env_var="JSON")
                group.add_argument("--text", action="store_true")
                parser.add_argument("files", nargs="+")
                kwargs = (
                    {"env_vars": {"JSON": "true"}}
                    if source == "environment"
                    else {"config_file_contents": "json = true", "env_vars": {}}
                )
                result = parser.parse_args(["--", "--text"], **kwargs)
                self.assertTrue(result.json)
                self.assertFalse(result.text)
                self.assertEqual(result.files, ["--text"])

    def test_positional_option_name_does_not_hide_unknown_config_key(self):
        parser = configargparse.ArgumentParser()
        parser.add_argument("files", nargs="+")
        result, unknown = parser.parse_known_args(
            ["--", "--extra"], config_file_contents="extra = config", env_vars={}
        )
        self.assertEqual(result.files, ["--extra"])
        self.assertEqual(unknown, ["--extra=config"])

    def test_positional_option_name_does_not_change_default_source(self):
        parser = configargparse.ArgumentParser()
        parser.add_argument("--level", default="warning")
        parser.add_argument("files", nargs="+")
        result = parser.parse_args(["--", "--level"], env_vars={})
        self.assertEqual(result.level, "warning")
        self.assertEqual(result.files, ["--level"])
        settings = parser.get_source_to_settings_dict()
        self.assertEqual(settings["defaults"]["--level"][1], "warning")
        self.assertEqual(
            parser.get_items_for_config_file_output(settings, result),
            {"level": "warning"},
        )

    def test_list_element_cannot_end_option_parsing(self):
        # otherwise the user's --level would become a positional arg, and the
        # config file's level would be used instead of it
        for source, kwargs in (
            (
                "environment",
                {
                    "env_vars": {"ITEMS": '["a", "--"]'},
                    "config_file_contents": "level = config",
                },
            ),
            (
                "config",
                {"env_vars": {}, "config_file_contents": "items = [a, --]"},
            ),
            (
                "environment nested list",
                {"env_vars": {"PAIR": '[["a", "--"]]'}},
            ),
        ):
            # argparse ends option parsing at '--' whatever the prefix chars are
            for prefix in ("-", "+"):
                with self.subTest(source=source, prefix=prefix):
                    parser = configargparse.ArgumentParser(prefix_chars=prefix)
                    parser.add_argument(
                        prefix * 2 + "items", nargs="+", env_var="ITEMS"
                    )
                    parser.add_argument(
                        prefix * 2 + "pair", nargs=2, action="append", env_var="PAIR"
                    )
                    parser.add_argument(prefix * 2 + "level")
                    parser.add_argument("files", nargs="*")
                    self.assertIn(
                        "can't have '--' as a list element",
                        self.parse_args_expecting_exit_and_return_error_line(
                            parser, [prefix * 2 + "level", "debug"], **kwargs
                        ),
                    )

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
