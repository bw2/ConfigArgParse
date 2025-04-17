"""
This code serves as a demonstration of how to implement custom behaviour by subclassing the
ConfigFileParser classes provided in the main module.

We'll demonstrate three scenarios, in increasing order of complexity.

 1) Making it so that an argument cannot be set within the config file.
 2) Allowing dictionaries to be used as item values in the config file.
 3) Allowing a config file to include other config files.

Note: This demo code is kept within the unit tests of ConfigArgParse so that it will always be
tested and guaranteed to work.
"""

import configargparse

# This finds the correct path to the directory where the sample config files are kept
from pathlib import Path

example_configs = Path(__file__).parent.absolute() / "example_configs"

"""
Scenario 1 - Making it so that a specific argument cannot be set within the config file.

For this we'll use the DefaultConfigFileParser class. We can subclass this and define a
tweak_value() method.
"""


class ForbiddenArgConfigFileParser(configargparse.DefaultConfigFileParser):

    # Keys in the config file may be written with or without the "--" so we need to
    # catch both.
    forbidden_args = ["setting2", "--setting2"]

    def tweak_value(self, key, value):
        if key in self.forbidden_args:
            # Return None, to completely ignore this setting
            return None

        # For other cases, make sure you return the original value or else all the values
        # will be ignored.
        return value


def demo_forbidden_arg():
    """This function demonstrates how to use the CustomConfigFileParser1 class"""

    # We can create an ArgumentParser using the custom parser subclass
    # The file 'example_configs/example1.ini' contains these lines:
    #     setting1: foo
    #     setting2: bar
    ap = configargparse.ArgumentParser(
        config_file_parser_class=ForbiddenArgConfigFileParser,
        default_config_files=[example_configs / "example1.ini"],
    )
    ap.add_argument("--setting1")
    ap.add_argument("--setting2")

    # We should only be able to set setting1 on the command line; any config value will be
    # ignored.
    # So here, res1.setting1 will be "foo", but res1.setting2 will be None even though it
    # is in the config file.
    res1 = ap.parse_args([])

    # Here, res2.setting1 will be "foofoo" and res2.setting2 will be "barbar"
    res2 = ap.parse_args(["--setting1", "foofoo", "--setting2", "barbar"])

    return res1, res2


"""
Scenario 2 - Allowing for dictionaries to be put into an argument via the config file

There is no direct support for dict-type args within the regular Python argparse module, but it
can be achieved by using a custom action class. For example:

$ mycmd.py --mydict foo=A bar=B baz=C

And we expect args.mydict to then be set to {'foo': "A", 'bar': "B", 'baz': "C"}

This is demonstrated with the custom DictAction class below.

To extend this idea to work with ConfigArgParse, we add a corresponding custom ConfigFileParser
to correctly handle these dicts. This makes most sense with the YAMLConfigFileParser, because YAML
allows for structures like nested dicts.
"""
import argparse


class DictAction(argparse.Action):
    """
    Custom action modelled after the example at
    https://docs.python.org/3/library/argparse.html#action
    """

    def __init__(self, option_strings, dest, nargs=None, **kwargs):
        if nargs not in ["*", "+"]:
            # Any arg using this action may allow the dict to be empty or not
            raise ValueError("nargs must be set to either '*' or '+'")
        super().__init__(option_strings, dest, nargs=nargs, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, dict(arg.split("=", 1) for arg in values))


def demo_dict_arg():
    """This shows the DictAction above being used in a regular ArgumentParser"""
    ap = argparse.ArgumentParser()

    ap.add_argument("--mydict", action=DictAction, nargs="+")

    res = ap.parse_args("--mydict foo=A bar=B baz=C".split())

    # And now, as stated above, res.mydict will be a dict with keys "foo", "bar" and "baz".
    return res


class DictYAMLConfigFileParser(configargparse.YAMLConfigFileParser):
    """
    A custom parser to match with the custom action above
    """

    def tweak_value(self, key, value):
        if isinstance(value, dict):
            # Check than no keys contain "="
            if any("=" in k for k in value):
                raise ValueError("dict keys containing '=' cannot be encoded")
            return [f"{k}={v}" for k, v in value.items()]
        else:
            return value


def demo_dict_config():
    """This shows the two custom classes working together"""

    # We create an ArgumentParser using the custom parser subclass, and an action using
    # the custom action subclass.
    # The file 'example_configs/example2.yaml' contains these lines:
    #     mydict:
    #       foo: A
    #       bar: B
    #       baz: C
    ap = configargparse.ArgumentParser(
        config_file_parser_class=DictYAMLConfigFileParser,
        default_config_files=[example_configs / "example2.yaml"],
    )
    ap.add_argument("--mydict", action=DictAction, nargs="+")

    # Getting the values from the config file, res1.mydict will contain
    # {'foo': "A", 'bar': "B", 'baz': "C"}
    res1 = ap.parse_args([])

    # Note that if we specify --mydict on the command line, the config values will
    # be entirely replaced. That is, res2.mydict will contain
    # {'beep': "X", 'meep': "Y"}
    res2 = ap.parse_args("--mydict beep=X meep=Y".split())

    return res1, res2


### Unit tests below this line for use with "python -munittest"

from tests.test_base import TestCase


class TestConfigParserSubclasses(TestCase):

    def test_forbidden_arg(self):

        res1, res2 = demo_forbidden_arg()

        self.assertEqual(vars(res1), dict(setting1="foo", setting2=None))
        self.assertEqual(vars(res2), dict(setting1="foofoo", setting2="barbar"))

    def test_dict_arg(self):

        res = demo_dict_arg()

        self.assertEqual(list(vars(res)), ["mydict"])
        self.assertEqual(res.mydict, dict(foo="A", bar="B", baz="C"))

        # And with the config file
        res1, res2 = demo_dict_config()

        self.assertEqual(res1.mydict, dict(foo="A", bar="B", baz="C"))
        self.assertEqual(res2.mydict, dict(beep="X", meep="Y"))
