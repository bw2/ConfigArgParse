"""
This code serves as a demonstration of how to implement custom behaviour by subclassing the
ConfigFileParser classes provided in the main module.

We'll demonstrate three scenarios, in increasing order of complexity.

 1) Making it so that an argument cannot be set within the config file.
 2) Allowing dictionaries to be used as item values in the config file.
 3) Allowing a config file to include other config files.

This demo code is kept within the unit tests of ConfigArgParse so that it will always be tested
and guaranteed to work.
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


class CustomConfigFileParser1(configargparse.DefaultConfigFileParser):

    def tweak_value(self, key, value):
        if key == "setting2":
            # Return None, to completely ignore this setting
            return None

        # For other cases, make sure you return the original value or else all the values
        # will be ignored.
        return value


def demo_forbidden_arg():
    """This function demonstrates how to use the CustomConfigFileParser1 class"""

    # We can create an ArgumentParser using the custom parser subclass
    # The file 'example_configs/example1.ini' contains:
    #     setting1: foo
    #     setting2: bar
    ap = configargparse.ArgumentParser(
        config_file_parser_class=CustomConfigFileParser1,
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


### Unit tests below this line

from tests.test_base import TestCase


class TestConfigParserSubclasses(TestCase):

    def test_forbidden_arg(self):

        res1, res2 = demo_forbidden_arg()

        self.assertEqual(vars(res1), dict(setting1="foo", setting2=None))
        self.assertEqual(vars(res2), dict(setting1="foofoo", setting2="barbar"))
