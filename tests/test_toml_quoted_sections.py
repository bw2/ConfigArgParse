"""Quoted TOML section names must identify the same tables as the reader."""

from io import StringIO
import unittest

import configargparse


class QuotedTomlSectionTests(unittest.TestCase):
    def test_quoted_dots_after_whitespace(self):
        self.assertEqual(
            configargparse.parse_toml_section_name(' tool . "a.b" . child '),
            ("tool", "a.b", "child"),
        )

    def test_literal_quoted_dots(self):
        self.assertEqual(
            configargparse.parse_toml_section_name("tool.'a.b'.child"),
            ("tool", "a.b", "child"),
        )

    def test_literal_backslash_is_not_an_escape(self):
        self.assertEqual(
            configargparse.parse_toml_section_name(r"tool.'a\b.c'"),
            ("tool", "a\\b.c"),
        )

    def test_escaped_quote_in_basic_key(self):
        self.assertEqual(
            configargparse.parse_toml_section_name(r'tool."a\"b.c"'),
            ("tool", 'a"b.c'),
        )

    def test_unicode_escape_in_basic_key(self):
        self.assertEqual(
            configargparse.parse_toml_section_name(r'tool."a\u002eb"'),
            ("tool", "a.b"),
        )

    def test_empty_quoted_key(self):
        self.assertEqual(
            configargparse.parse_toml_section_name('tool."".child'),
            ("tool", "", "child"),
        )

    def test_read_literal_quoted_section(self):
        parser = configargparse.TomlConfigParser(["tool.'a.b'"])
        self.assertEqual(
            parser.parse(StringIO('[tool."a.b"]\nvalue = "configured"\n')),
            {"value": "configured"},
        )

    def test_read_basic_quoted_section_after_space(self):
        parser = configargparse.TomlConfigParser(['tool . "a.b"'])
        self.assertEqual(
            parser.parse(StringIO('[tool."a.b"]\nvalue = "configured"\n')),
            {"value": "configured"},
        )

    def test_write_literal_quoted_section(self):
        parser = configargparse.TomlConfigParser(["tool.'a.b'"])
        text = parser.serialize({"value": "configured"})
        self.assertEqual(parser.parse(StringIO(text)), {"value": "configured"})
        self.assertIn('[tool."a.b"]', text)

    def test_unquoted_and_basic_quoted_controls(self):
        for name, expected in [
            ("tool.name", ("tool", "name")),
            ('tool."a.b"', ("tool", "a.b")),
            (' "quoted" . bare ', ("quoted", "bare")),
        ]:
            with self.subTest(name=name):
                self.assertEqual(configargparse.parse_toml_section_name(name), expected)
