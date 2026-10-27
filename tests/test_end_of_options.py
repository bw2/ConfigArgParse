import contextlib
import io
import unittest

import configargparse


class TestEndOfOptions(unittest.TestCase):
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
            with self.subTest(source=source):
                parser = configargparse.ArgumentParser()
                parser.add_argument("--items", nargs="+", env_var="ITEMS")
                parser.add_argument("--pair", nargs=2, action="append", env_var="PAIR")
                parser.add_argument("--level")
                parser.add_argument("files", nargs="*")
                stderr = io.StringIO()
                with self.assertRaises(SystemExit), contextlib.redirect_stderr(stderr):
                    parser.parse_args(["--level", "debug"], **kwargs)
                self.assertIn("can't have '--' as a list element", stderr.getvalue())
