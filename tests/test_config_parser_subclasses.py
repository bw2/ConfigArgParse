"""
This file serves as a demonstration of how to implement custom behaviour by subclassing the
ConfigFileParser classes provided in the main module.

We'll demonstrate four scenarios, in increasing order of complexity.

 1) Making it so that a specific argument cannot be set within the config file.
 2) Detecting when a value is set both on the command line and in a config file.
 3) Allowing dictionaries to be used as item values in the config file.
 4) Allowing a config file to include other config files.

Note: This demo code is kept within the unit tests of ConfigArgParse so that it will always be
tested and guaranteed to work.
"""
import os
import configargparse

# This gets us the correct path to the directory where the sample config files are kept
from pathlib import Path
example_configs = Path(__file__).parent.absolute() / "example_configs"

"""
Scenario 1 - Making it so that a specific argument cannot be set within the config file.

For this we'll use the DefaultConfigFileParser class. We can subclass this and define our own
tweak_value() method.

This was inspired by https://github.com/bw2/ConfigArgParse/issues/301
"""

class ForbiddenArgConfigFileParser(configargparse.DefaultConfigFileParser):

    # Keys in the config file may be written with or without the "--" so we need to
    # catch both.
    forbidden_args = ["setting2", "--setting2"]

    def tweak_value(self, key, value, filename):
        if key in self.forbidden_args:
            # Set value to None, to completely ignore this setting
            value = None

        # tweak_value() should always return {key: value}
        return {key: value}

def demo_forbidden_arg():
    """This function demonstrates how to use the ForbiddenArgConfigFileParser class"""

    # We can create an ArgumentParser using the custom parser subclass
    # The file 'example_configs/example1.ini' contains these lines:
    #     setting1: foo
    #     setting2: bar
    ap = configargparse.ArgumentParser( config_file_parser_class = ForbiddenArgConfigFileParser,
                                        default_config_files = [example_configs / "example1.ini"] )
    ap.add_argument("--setting1")
    ap.add_argument("--setting2")

    # We should only be able to set setting1 on the command line; any config file value
    # will be ignored.
    # So here, res1.setting1 will be "foo", but res1.setting2 will be None even though it
    # appears in the config file.
    res1 = ap.parse_args([])
    assert res1.setting1 == "foo" and res1.setting2 == None

    # Here, res2.setting1 will be "foofoo" and res2.setting2 will be "barbar"
    res2 = ap.parse_args(["--setting1", "foofoo", "--setting2", "barbar"])
    assert res2.setting1 == "foofoo" and res2.setting2 == "barbar"

    return res1, res2

"""
Scenario 2 - Detecting when a value in a config file is overridden on the command line

We can ask configargparse to show the source of all the parsed arguments using format_values(),
but this does not reveal if any given argument was in two places. To get around this, we can
subclass the DefaultConfigFileParser to make a version that remembers all the items it sees in
the config file, whether they are on the command line or not.
"""

class LoggingConfigFileParser(configargparse.DefaultConfigFileParser):

    def __init__(self):
        # Make a list to store the values wee see
        self.all_values_seen = []
        super().__init__()

    def tweak_value(self, key, value, filename):
        # Remember everything that we see
        stripped_key = key.lstrip("-") # Remove any optional -- prefix
        self.all_values_seen.append((stripped_key, value, filename))

        # tweak_value() should always return {key: value}
        return {key: value}

def demo_logging_parser():

    myparser = LoggingConfigFileParser()
    ap = configargparse.ArgumentParser( config_file_parser_class = myparser )

    # The file 'example_configs/example1.ini' contains these lines:
    #     setting1: foo
    #     setting2: bar
    ap.add_argument("--config", type=Path, nargs="+", is_config_file=True)
    ap.add_argument("--setting1")
    ap.add_argument("--setting2")
    ap.add_argument("--setting3")

    # If we parse the following, all three items will be set
    #  res.setting2 will be "wibble", not "bar", as the command line overrides the config.
    res = ap.parse_args(["--config", str(example_configs / "example1.ini"),
                         "--setting2", "wibble", "--setting3", "bibble"])

    # We can now see if any values were overridden. Note that this is only going to work for
    # simple string values as it relies on a string comparison.
    for key, value, file in myparser.all_values_seen:
        val_as_str = str(getattr(res, key))
        if val_as_str != value:
            print(f"--{key} was set to {val_as_str}, overriding {value} in config file {file}")

"""
Scenario 3 - Allowing for dictionaries to be put into an argument via the config file

There is no direct support for dict-type args within the regular Python argparse module, but it
can be achieved by using a custom action class. For example, running:

$ mycmd.py --mydict foo=A bar=B baz=C

We would like args.mydict to then be set to {'foo': "A", 'bar': "B", 'baz': "C"}

This is demonstrated with the custom DictAction class below.

To extend this idea to work with ConfigArgParse, we add a corresponding custom ConfigFileParser
to correctly back-translate these dicts to lists of strings. This makes most sense with the
YAMLConfigFileParser, because YAML allows for structures like nested dicts.

This was inspired by https://github.com/bw2/ConfigArgParse/issues/258
"""
import argparse

class DictAction(argparse.Action):
    """
    Custom action modelled after the example at
    https://docs.python.org/3/library/argparse.html#action
    """
    def __init__(self, option_strings, dest, nargs=None, **kwargs):
        if nargs not in ["*", "+"]:
            # Any arg using this action may allow the dict to be empty or not, but those
            # are the only possibilities.
            raise ValueError("nargs must be set to either '*' or '+'")
        super().__init__(option_strings, dest, nargs=nargs, **kwargs)

    # Whan making a custom argparse.Action we override the __call__() method
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, dict(arg.split("=",1) for arg in values))

def demo_dict_arg():
    """This shows the DictAction above being used in a regular ArgumentParser"""
    ap = argparse.ArgumentParser()

    ap.add_argument("--mydict", action=DictAction, nargs="+")

    res = ap.parse_args("--mydict foo=A bar=B baz=C".split())

    # And now, as stated above, res.mydict will be a dict with keys "foo", "bar" and "baz".
    return res

# Now to make this work with configargparse...

class DictYAMLConfigFileParser(configargparse.YAMLConfigFileParser):
    """
    A custom parser to match with the custom action above
    """
    def tweak_value(self, key, value, filename):
        if isinstance(value, dict):
            # Check that no keys contain "="
            if any("=" in k for k in value):
                raise ValueError("dict keys containing '=' cannot be encoded")
            value = [f"{k}={v}" for k, v in value.items()]

        # We always need to return {key: value}
        return {key: value}

def demo_dict_config():
    """This shows the two custom classes working together"""

    # We create an ArgumentParser using the custom parser subclass, and an action using
    # the custom action subclass.
    # The file 'example_configs/example2.yaml' contains these lines:
    #     mydict:
    #       foo: A
    #       bar: B
    #       baz: C
    ap = configargparse.ArgumentParser( config_file_parser_class = DictYAMLConfigFileParser,
                                        default_config_files = [example_configs / "example2.yaml"] )
    ap.add_argument("--mydict", action=DictAction, nargs="+")

    # Getting the values from the config file, res1.mydict will contain
    # {'foo': "A", 'bar': "B", 'baz': "C"}
    res1 = ap.parse_args([])

    # Note that if we specify --mydict on the command line, the config values will
    # be entirely replaced. That is, res2.mydict will contain
    # {'beep': "X", 'meep': "Y"}
    res2 = ap.parse_args("--mydict beep=X meep=Y".split())

    return res1, res2

"""
Scenario 4 - Allowing a config file to include other config files.

The regular configargparse module does not allow you to set arguments that are flagged
is "is_config_file=True" in the config files themselves. That is, you can't specify a
second config file within the main config file. However, we can add this behaviour to a
custom subclass.

This was inspired by https://github.com/bw2/ConfigArgParse/pull/261
"""

class RecursiveConfigFileParser(configargparse.DefaultConfigFileParser):
    """
    A custom parser that recognises the special parameter name 'config' and
    loads the corresponding config file recursively.
    """
    def __init__(self, config_key,  already_seen_files=None):
        # What is the name of the special config item that loads a new file? eg. "config"
        self.config_key = config_key

        # We'll keep a list of files already processed, to allow detection of recursive
        # includes and prevent an infinite loop.
        self.already_seen_files = set()

        if already_seen_files:
            self.already_seen_files.update(already_seen_files)

        # Must call the suporclass constructor - DefaultConfigFileParser takes no args
        super().__init__()

    def tweak_value(self, key, value, filename):
        if key == self.config_key:
            # Found the config key. Create a new parser and parse the included file(s)
            newdict = dict()
            # Allow for lists of files or single names
            if isinstance(value, str):
                value = [value]
            for conf_file in value:
                # Get the path of this config file relative to the parent file
                conf_file = os.path.join(os.path.dirname(filename), conf_file)

                if conf_file in self.already_seen_files:
                    # Do not visit the same file again
                    continue

                self.already_seen_files.add(conf_file)

                new_parser = RecursiveConfigFileParser(self.config_key, self.already_seen_files)
                with open(conf_file) as stream:
                    newdict.update(new_parser.parse(stream))

            # We now return all the values
            return newdict
        else:
            return {key: value}

def demo_recursive_config():
    """
    Loading tests/example_configs/multiconf_0.ini should get values from all the linked
    config files, and avoid an infinite recursion.
    """
    ap = configargparse.ArgumentParser( config_file_parser_class =
                                            RecursiveConfigFileParser("config") )

    ap.add_argument("--config", type=Path, nargs="*", is_config_file=True)
    ap.add_argument('--arg_0', type=str)
    ap.add_argument('--arg_1', type=str)
    ap.add_argument('--arg_2', type=str)

    # Here, res should be: NameSpace(arg_0="conf_0", arg_1="conf_1", arg_2="conf_3")
    res = ap.parse_args(["--config", str(example_configs / "multiconf_0.ini")])

    return res

### Unit tests below this line for use with "python -munittest"

from tests.test_base import TestCase, yaml
import unittest
from unittest.mock import patch
import logging

class TestConfigParserSubclasses(TestCase):

    def test_forbidden_arg(self):

        res1, res2 = demo_forbidden_arg()

        self.assertEqual( vars(res1), dict( setting1 = "foo",
                                            setting2 = None ) )
        self.assertEqual( vars(res2), dict( setting1 = "foofoo",
                                            setting2 = "barbar" ) )

    def test_arg_logging(self):

        with patch('builtins.print') as patched_print:
            res = demo_logging_parser()

            pp_calls = patched_print.mock_calls

        self.assertEqual(len(pp_calls), 1)
        self.assertRegex(" ".join(pp_calls[0].args),
                         r"--setting2 was set to wibble, overriding bar in config file .*example1\.ini")

    @unittest.skipUnless(yaml, "PyYAML not installed")
    def test_dict_arg(self):

        res = demo_dict_arg()

        self.assertCountEqual( vars(res), ["mydict"] )
        self.assertEqual( res.mydict, dict(foo="A", bar="B", baz="C") )

        # And with the config file
        res1, res2 = demo_dict_config()

        self.assertEqual( res1.mydict, dict(foo="A", bar="B", baz="C") )
        self.assertEqual( res2.mydict, dict(beep="X", meep="Y") )

    def test_recursive_parse(self):

        res = demo_recursive_config()

        self.assertCountEqual( vars(res), ["config", "arg_0", "arg_1", "arg_2"] )
        self.assertEqual( vars(res), dict( config=res.config,  # This path will vary
                                           arg_0="conf_0",
                                           arg_1="conf_1",
                                           arg_2="conf_3" ) )

