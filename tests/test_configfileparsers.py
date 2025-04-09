import argparse
import tempfile
import configargparse
from io import StringIO
import logging

from tests.test_base import TestCase


class TestConfigFileParsers(TestCase):
    """Test ConfigFileParser subclasses in isolation"""

    def setUp(self):
        # No setup for this one
        pass

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
            "\n".join(l.replace(": ", " = ") for l in config_lines if l.startswith("_"))
            + "\n",
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
        p = configargparse.DefaultConfigFileParser()

        # test the all syntax case
        config_lines = [
            {
                "line": "key = value # comment # comment",
                "expected": ("key", "value", "comment # comment"),
            },
            {"line": "key=value#comment ", "expected": ("key", "value#comment", None)},
            {"line": "key=value", "expected": ("key", "value", None)},
            {"line": "key =value", "expected": ("key", "value", None)},
            {"line": "key= value", "expected": ("key", "value", None)},
            {"line": "key = value", "expected": ("key", "value", None)},
            {"line": "key  =  value", "expected": ("key", "value", None)},
            {"line": " key  =  value ", "expected": ("key", "value", None)},
            {"line": "key:value", "expected": ("key", "value", None)},
            {"line": "key :value", "expected": ("key", "value", None)},
            {"line": "key: value", "expected": ("key", "value", None)},
            {"line": "key : value", "expected": ("key", "value", None)},
            {"line": "key  :  value", "expected": ("key", "value", None)},
            {"line": " key  :  value ", "expected": ("key", "value", None)},
            {"line": "key value", "expected": ("key", "value", None)},
            {"line": "key  value", "expected": ("key", "value", None)},
            {"line": " key    value ", "expected": ("key", "value", None)},
        ]

        for test in config_lines:
            parsed_obj = p.parse(StringIO(test["line"]))
            parsed_obj = dict(parsed_obj)
            expected = {test["expected"][0]: test["expected"][1]}
            self.assertDictEqual(parsed_obj, expected, msg="Line %r" % (test["line"]))

    def testDefaultConfigFileParser_QuotedValues(self):
        p = configargparse.DefaultConfigFileParser()

        # test the all syntax case
        config_lines = [
            {"line": 'key="value"', "expected": ("key", "value", None)},
            {"line": 'key  =  "value"', "expected": ("key", "value", None)},
            {"line": ' key  =  "value" ', "expected": ("key", "value", None)},
            {"line": 'key=" value "', "expected": ("key", " value ", None)},
            {"line": 'key  =  " value "', "expected": ("key", " value ", None)},
            {"line": ' key  =  " value " ', "expected": ("key", " value ", None)},
            {"line": "key='value'", "expected": ("key", "value", None)},
            {"line": "key  =  'value'", "expected": ("key", "value", None)},
            {"line": " key  =  'value' ", "expected": ("key", "value", None)},
            {"line": "key=' value '", "expected": ("key", " value ", None)},
            {"line": "key  =  ' value '", "expected": ("key", " value ", None)},
            {"line": " key  =  ' value ' ", "expected": ("key", " value ", None)},
            {"line": 'key="', "expected": ("key", '"', None)},
            {"line": 'key  =  "', "expected": ("key", '"', None)},
            {"line": ' key  =  " ', "expected": ("key", '"', None)},
            {"line": "key = '\"value\"'", "expected": ("key", '"value"', None)},
            {"line": "key = \"'value'\"", "expected": ("key", "'value'", None)},
            {"line": 'key = ""value""', "expected": ("key", '"value"', None)},
            {"line": "key = ''value''", "expected": ("key", "'value'", None)},
            {"line": 'key="value', "expected": ("key", '"value', None)},
            {"line": 'key  =  "value', "expected": ("key", '"value', None)},
            {"line": ' key  =  "value ', "expected": ("key", '"value', None)},
            {"line": 'key=value"', "expected": ("key", 'value"', None)},
            {"line": 'key  =  value"', "expected": ("key", 'value"', None)},
            {"line": ' key  =  value " ', "expected": ("key", 'value "', None)},
            {"line": "key='value", "expected": ("key", "'value", None)},
            {"line": "key  =  'value", "expected": ("key", "'value", None)},
            {"line": " key  =  'value ", "expected": ("key", "'value", None)},
            {"line": "key=value'", "expected": ("key", "value'", None)},
            {"line": "key  =  value'", "expected": ("key", "value'", None)},
            {"line": " key  =  value ' ", "expected": ("key", "value '", None)},
        ]

        for test in config_lines:
            parsed_obj = p.parse(StringIO(test["line"]))
            parsed_obj = dict(parsed_obj)
            expected = {test["expected"][0]: test["expected"][1]}
            self.assertDictEqual(parsed_obj, expected, msg="Line %r" % (test["line"]))

    def testDefaultConfigFileParser_BlankValues(self):
        p = configargparse.DefaultConfigFileParser()

        # test the all syntax case
        config_lines = [
            {"line": "key=", "expected": ("key", "", None)},
            {"line": "key =", "expected": ("key", "", None)},
            {"line": "key= ", "expected": ("key", "", None)},
            {"line": "key = ", "expected": ("key", "", None)},
            {"line": "key  =  ", "expected": ("key", "", None)},
            {"line": " key  =   ", "expected": ("key", "", None)},
            {"line": "key:", "expected": ("key", "", None)},
            {"line": "key :", "expected": ("key", "", None)},
            {"line": "key: ", "expected": ("key", "", None)},
            {"line": "key : ", "expected": ("key", "", None)},
            {"line": "key  :  ", "expected": ("key", "", None)},
            {"line": " key  :   ", "expected": ("key", "", None)},
        ]

        for test in config_lines:
            parsed_obj = p.parse(StringIO(test["line"]))
            parsed_obj = dict(parsed_obj)
            expected = {test["expected"][0]: test["expected"][1]}
            self.assertDictEqual(parsed_obj, expected, msg="Line %r" % (test["line"]))

    def testDefaultConfigFileParser_UnspecifiedValues(self):
        p = configargparse.DefaultConfigFileParser()

        # test the all syntax case
        config_lines = [
            {"line": "key ", "expected": ("key", "true", None)},
            {"line": "key", "expected": ("key", "true", None)},
            {"line": "key  ", "expected": ("key", "true", None)},
            {"line": " key     ", "expected": ("key", "true", None)},
        ]

        for test in config_lines:
            parsed_obj = p.parse(StringIO(test["line"]))
            parsed_obj = dict(parsed_obj)
            expected = {test["expected"][0]: test["expected"][1]}
            self.assertDictEqual(parsed_obj, expected, msg="Line %r" % (test["line"]))

    def testDefaultConfigFileParser_ColonEqualSignValue(self):
        p = configargparse.DefaultConfigFileParser()

        # test the all syntax case
        config_lines = [
            {"line": "key=:", "expected": ("key", ":", None)},
            {"line": "key =:", "expected": ("key", ":", None)},
            {"line": "key= :", "expected": ("key", ":", None)},
            {"line": "key = :", "expected": ("key", ":", None)},
            {"line": "key  =  :", "expected": ("key", ":", None)},
            {"line": " key  =  : ", "expected": ("key", ":", None)},
            {"line": "key:=", "expected": ("key", "=", None)},
            {"line": "key :=", "expected": ("key", "=", None)},
            {"line": "key: =", "expected": ("key", "=", None)},
            {"line": "key : =", "expected": ("key", "=", None)},
            {"line": "key  :  =", "expected": ("key", "=", None)},
            {"line": " key  :  = ", "expected": ("key", "=", None)},
            {"line": "key==", "expected": ("key", "=", None)},
            {"line": "key ==", "expected": ("key", "=", None)},
            {"line": "key= =", "expected": ("key", "=", None)},
            {"line": "key = =", "expected": ("key", "=", None)},
            {"line": "key  =  =", "expected": ("key", "=", None)},
            {"line": " key  =  = ", "expected": ("key", "=", None)},
            {"line": "key::", "expected": ("key", ":", None)},
            {"line": "key ::", "expected": ("key", ":", None)},
            {"line": "key: :", "expected": ("key", ":", None)},
            {"line": "key : :", "expected": ("key", ":", None)},
            {"line": "key  :  :", "expected": ("key", ":", None)},
            {"line": " key  :  : ", "expected": ("key", ":", None)},
        ]

        for test in config_lines:
            parsed_obj = p.parse(StringIO(test["line"]))
            parsed_obj = dict(parsed_obj)
            expected = {test["expected"][0]: test["expected"][1]}
            self.assertDictEqual(parsed_obj, expected, msg="Line %r" % (test["line"]))

    def testDefaultConfigFileParser_ValuesWithComments(self):
        p = configargparse.DefaultConfigFileParser()

        # test the all syntax case
        config_lines = [
            {"line": "key=value#comment ", "expected": ("key", "value#comment", None)},
            {"line": "key=value #comment", "expected": ("key", "value", "comment")},
            {
                "line": " key  =  value  #  comment",
                "expected": ("key", "value", "comment"),
            },
            {"line": "key:value#comment", "expected": ("key", "value#comment", None)},
            {"line": "key:value #comment", "expected": ("key", "value", "comment")},
            {
                "line": " key  :  value  #  comment",
                "expected": ("key", "value", "comment"),
            },
            {"line": "key=value;comment ", "expected": ("key", "value;comment", None)},
            {"line": "key=value ;comment", "expected": ("key", "value", "comment")},
            {
                "line": " key  =  value  ;  comment",
                "expected": ("key", "value", "comment"),
            },
            {"line": "key:value;comment", "expected": ("key", "value;comment", None)},
            {"line": "key:value ;comment", "expected": ("key", "value", "comment")},
            {
                "line": " key  :  value  ;  comment",
                "expected": ("key", "value", "comment"),
            },
            {
                "line": "key = value # comment # comment",
                "expected": ("key", "value", "comment # comment"),
            },
            {
                "line": 'key = "value # comment" # comment',
                "expected": ("key", "value # comment", "comment"),
            },
            {"line": 'key = "#" ; comment', "expected": ("key", "#", "comment")},
            {"line": 'key = ";" # comment', "expected": ("key", ";", "comment")},
        ]

        for test in config_lines:
            parsed_obj = p.parse(StringIO(test["line"]))
            parsed_obj = dict(parsed_obj)
            expected = {test["expected"][0]: test["expected"][1]}
            self.assertDictEqual(parsed_obj, expected, msg="Line %r" % (test["line"]))

    def testDefaultConfigFileParser_NegativeValues(self):
        p = configargparse.DefaultConfigFileParser()

        # test the all syntax case
        config_lines = [
            {"line": "key = -10", "expected": ("key", "-10", None)},
            {"line": "key : -10", "expected": ("key", "-10", None)},
            {"line": "key -10", "expected": ("key", "-10", None)},
            {"line": 'key = "-10"', "expected": ("key", "-10", None)},
            {"line": "key  =  '-10'", "expected": ("key", "-10", None)},
            {"line": "key=-10", "expected": ("key", "-10", None)},
        ]

        for test in config_lines:
            parsed_obj = p.parse(StringIO(test["line"]))
            parsed_obj = dict(parsed_obj)
            expected = {test["expected"][0]: test["expected"][1]}
            self.assertDictEqual(parsed_obj, expected, msg="Line %r" % (test["line"]))

    def testDefaultConfigFileParser_KeySyntax(self):
        p = configargparse.DefaultConfigFileParser()

        # test the all syntax case
        config_lines = [
            {
                "line": "key_underscore = value",
                "expected": ("key_underscore", "value", None),
            },
            {"line": "key_underscore=", "expected": ("key_underscore", "", None)},
            {"line": "key_underscore", "expected": ("key_underscore", "true", None)},
            {
                "line": "_key_underscore = value",
                "expected": ("_key_underscore", "value", None),
            },
            {"line": "_key_underscore=", "expected": ("_key_underscore", "", None)},
            {"line": "_key_underscore", "expected": ("_key_underscore", "true", None)},
            {
                "line": "key_underscore_ = value",
                "expected": ("key_underscore_", "value", None),
            },
            {"line": "key_underscore_=", "expected": ("key_underscore_", "", None)},
            {"line": "key_underscore_", "expected": ("key_underscore_", "true", None)},
            {"line": "key-dash = value", "expected": ("key-dash", "value", None)},
            {"line": "key-dash=", "expected": ("key-dash", "", None)},
            {"line": "key-dash", "expected": ("key-dash", "true", None)},
            {"line": "key@word = value", "expected": ("key@word", "value", None)},
            {"line": "key@word=", "expected": ("key@word", "", None)},
            {"line": "key@word", "expected": ("key@word", "true", None)},
            {"line": "key$word = value", "expected": ("key$word", "value", None)},
            {"line": "key$word=", "expected": ("key$word", "", None)},
            {"line": "key$word", "expected": ("key$word", "true", None)},
            {"line": "key.word = value", "expected": ("key.word", "value", None)},
            {"line": "key.word=", "expected": ("key.word", "", None)},
            {"line": "key.word", "expected": ("key.word", "true", None)},
        ]

        for test in config_lines:
            parsed_obj = p.parse(StringIO(test["line"]))
            parsed_obj = dict(parsed_obj)
            expected = {test["expected"][0]: test["expected"][1]}
            self.assertDictEqual(parsed_obj, expected, msg="Line %r" % (test["line"]))

    def testYAMLConfigFileParser_Basic(self):
        try:
            import yaml
        except:
            logging.warning(
                "WARNING: PyYAML not installed. " "Couldn't test YAMLConfigFileParser"
            )
            return

        p = configargparse.YAMLConfigFileParser()
        self.assertGreater(len(p.get_syntax_description()), 0)

        input_config_str = StringIO("""a: '3'\n""")
        parsed_obj = p.parse(input_config_str)
        output_config_str = p.serialize(dict(parsed_obj))

        self.assertEqual(input_config_str.getvalue(), output_config_str)

        self.assertDictEqual(parsed_obj, {"a": "3"})

    def testYAMLConfigFileParser_All(self):
        try:
            import yaml
        except:
            logging.warning(
                "WARNING: PyYAML not installed. " "Couldn't test YAMLConfigFileParser"
            )
            return

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

    def testYAMLConfigFileParser_w_ArgumentParser_parsed_values(self):
        try:
            import yaml
        except:
            raise AssertionError(
                "WARNING: PyYAML not installed. " "Couldn't test YAMLConfigFileParser"
            )
            return

        parser = configargparse.ArgumentParser(
            config_file_parser_class=configargparse.YAMLConfigFileParser
        )
        parser.add_argument("-c", "--config", is_config_file=True)
        parser.add_argument("--verbosity", action="count")
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--level", type=int)

        config_lines = ["verbosity: 3", "verbose: true", "level: 35"]
        config_str = "\n".join(config_lines) + "\n"
        config_file = tempfile.gettempdir() + "/temp_YAMLConfigFileParser.cfg"
        with open(config_file, "w") as f:
            f.write(config_str)
        args = parser.parse_args(["--config=%s" % config_file])
        assert args.verbosity == 3
        assert args.verbose == True
        assert args.level == 35
