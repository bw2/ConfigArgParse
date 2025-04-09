import argparse
import configargparse
from unittest import mock
from io import StringIO

from tests.test_base import TestCase


class TestMisc(TestCase):
    # TODO test different action types with config file, env var

    """Test edge cases"""

    @mock.patch("argparse.ArgumentParser.__init__")
    def testKwrgsArePassedToArgParse(self, argparse_init):
        kwargs_for_argparse = {"allow_abbrev": False, "whatever_other_arg": "something"}

        parser = configargparse.ArgumentParser(
            add_config_file_help=False, **kwargs_for_argparse
        )

        argparse_init.assert_called_with(parser, **kwargs_for_argparse)

    def testGlobalInstances(self, name=None):
        p = configargparse.getArgumentParser(name, prog="prog", usage="test")
        self.assertEqual(p.usage, "test")
        self.assertEqual(p.prog, "prog")
        self.assertRaisesRegex(
            ValueError,
            "kwargs besides 'name' can only be " "passed in the first time",
            configargparse.getArgumentParser,
            name,
            prog="prog",
        )

        p2 = configargparse.getArgumentParser(name)
        self.assertEqual(p, p2)

    def testGlobalInstances_WithName(self):
        self.testGlobalInstances("name1")
        self.testGlobalInstances("name2")

    def testAddArguments_ArgValidation(self):
        self.assertRaises(ValueError, self.add_arg, "positional", env_var="bla")
        action = self.add_arg("positional")
        self.assertIsNotNone(action)
        self.assertEqual(action.dest, "positional")

    def testAddArguments_IsConfigFilePathArg(self):
        self.assertRaises(
            ValueError, self.add_arg, "c", action="store_false", is_config_file=True
        )

        self.add_arg("-c", "--config", is_config_file=True)
        self.add_arg("--x", required=True)

        # verify parsing from config file
        with self.tmpFile() as config_file:
            config_file.write("x=bla")
            config_file.flush()

            ns = self.parse(args="-c %s" % config_file.name)
            self.assertEqual(ns.x, "bla")

    def testConstructor_ConfigFileArgs(self):
        # Test constructor args:
        #   args_for_setting_config_path
        #   config_arg_is_required
        #   config_arg_help_message
        with self.tmpFile() as temp_cfg:
            temp_cfg.write("genome=hg19")
            temp_cfg.flush()

            self.initParser(
                args_for_setting_config_path=["-c", "--config"],
                config_arg_is_required=True,
                config_arg_help_message="my config file",
                default_config_files=[temp_cfg.name],
            )
            self.add_arg("--genome", help="Path to genome file", required=True)
            self.assertParseArgsRaises("arguments are required: -c/--config", args="")

            with self.tmpFile() as temp_cfg2:
                ns = self.parse("-c " + temp_cfg2.name)
                self.assertEqual(ns.genome, "hg19")

                # temp_cfg2 config file should override default config file values
                temp_cfg2.write("genome=hg20")
                temp_cfg2.flush()
                ns = self.parse("-c " + temp_cfg2.name)
                self.assertEqual(ns.genome, "hg20")

            self.assertRegex(
                self.format_help(),
                "usage: .* \\[-h\\] -c CONFIG_FILE --genome GENOME\n\n"
                "%s:\n"
                "  -h, --help\\s+ show this help message and exit\n"
                "  -c CONFIG_FILE, --config CONFIG_FILE\\s+ my config file\n"
                "  --genome GENOME\\s+ Path to genome file\n\n"
                % (self.OPTIONAL_ARGS_STRING)
                + 5 * r"(.+\s*)",
            )

            # just run print_values() to make sure it completes and returns None
            output = StringIO()
            self.assertIsNone(self.parser.print_values(file=output))
            self.assertIn("Command Line Args:", output.getvalue())

            # test ignore_unknown_config_file_keys=False
            self.initParser(ignore_unknown_config_file_keys=False)
            self.assertRaisesRegex(
                argparse.ArgumentError,
                "unrecognized arguments",
                self.parse,
                config_file_contents="arg1 = 3",
            )
            ns, args = self.parse_known(config_file_contents="arg1 = 3")
            self.assertEqual(getattr(ns, "arg1", ""), "")

            # test ignore_unknown_config_file_keys=True
            self.initParser(ignore_unknown_config_file_keys=True)
            ns = self.parse(args="", config_file_contents="arg1 = 3")
            self.assertEqual(getattr(ns, "arg1", ""), "")
            ns, args = self.parse_known(config_file_contents="arg1 = 3")
            self.assertEqual(getattr(ns, "arg1", ""), "")

    def test_AbbrevConfigFileArgs(self):
        """Tests that abbreviated values don't get pulled from config file."""
        with self.tmpFile() as temp_cfg:
            temp_cfg.write("a2a = 0.5\n")
            temp_cfg.write("a3a = 0.5\n")
            temp_cfg.flush()

            self.initParser()

            self.add_arg(
                "-c",
                "--config_file",
                required=False,
                is_config_file=True,
                help="config file path",
            )

            self.add_arg("--hello", type=int, required=False)

            command = "-c {} --hello 2".format(temp_cfg.name)

            known, unknown = self.parse_known(command)

            self.assertListEqual(unknown, ["--a2a=0.5", "--a3a=0.5"])

    def test_FormatHelp(self):
        self.initParser(
            args_for_setting_config_path=["-c", "--config"],
            config_arg_is_required=True,
            config_arg_help_message="my config file",
            default_config_files=["~/.myconfig"],
            args_for_writing_out_config_file=["-w", "--write-config"],
        )
        self.add_arg("--arg1", help="Arg1 help text", required=True)
        self.add_arg("--flag", help="Flag help text", action="store_true")

        self.assertRegex(
            self.format_help(),
            r"usage: .* \[-h\] -c CONFIG_FILE\s+"
            r"\[-w CONFIG_OUTPUT_PATH\]\s* --arg1\s+ARG1\s*\[--flag\]\s*"
            "%s:\\s*"
            "-h, --help \\s* show this help message and exit "
            r"-c CONFIG_FILE, --config CONFIG_FILE\s+my config file "
            r"-w CONFIG_OUTPUT_PATH, --write-config CONFIG_OUTPUT_PATH takes "
            r"the current command line args and writes them "
            r"out to a config file at the given path, then exits "
            r"--arg1 ARG1 Arg1 help text "
            r"--flag Flag help text "
            "Args that start with '--' can also be set in a "
            r"config file \(~/.myconfig or specified via -c\). "
            r"Config file syntax allows: key=value, flag=true, stuff=\[a,b,c\] "
            r"\(for details, see syntax at https://goo.gl/R74nmi\). "
            r"In general, command-line values override config file values "
            r"which override defaults. ".replace(" ", "\s*")
            % (self.OPTIONAL_ARGS_STRING),
        )

    def test_FormatHelpProg(self):
        self.initParser("format_help_prog")
        self.assertRegex(self.format_help(), "usage: format_help_prog .*")

    def test_FormatHelpProgLib(self):
        parser = argparse.ArgumentParser("format_help_prog")
        self.assertRegex(parser.format_help(), "usage: format_help_prog .*")

    class CustomClass(object):
        def __init__(self, name):
            self.name = name

        def __str__(self):
            return self.name

    @staticmethod
    def valid_custom(s):
        if s == "invalid":
            raise Exception("invalid name")
        return TestMisc.CustomClass(s)

    def testConstructor_WriteOutConfigFileArgs(self):
        # Test constructor args:
        #   args_for_writing_out_config_file
        #   write_out_config_file_arg_help_message
        with self.tmpFile() as cfg_f:
            self.initParser(
                args_for_writing_out_config_file=["-w"],
                write_out_config_file_arg_help_message="write config",
            )

            self.add_arg("-not-config-file-settable")
            self.add_arg("--config-file-settable-arg", type=int)
            self.add_arg("--config-file-settable-arg2", type=int, default=3)
            self.add_arg("--config-file-settable-flag", action="store_true")
            self.add_arg("--config-file-settable-custom", type=TestMisc.valid_custom)
            self.add_arg("-l", "--config-file-settable-list", action="append")

            # write out a config file
            command_line_args = "-w %s " % cfg_f.name
            command_line_args += "--config-file-settable-arg 1 "
            command_line_args += "--config-file-settable-flag "
            command_line_args += "--config-file-settable-custom custom_value "
            command_line_args += "-l a -l b -l c -l d "

            self.assertFalse(self.parser._exit_method_called)

            ns = self.parse(command_line_args)
            self.assertTrue(self.parser._exit_method_called)

            cfg_f.seek(0)
            expected_config_file_contents = "config-file-settable-arg = 1\n"
            expected_config_file_contents += "config-file-settable-flag = true\n"
            expected_config_file_contents += (
                "config-file-settable-custom = custom_value\n"
            )
            expected_config_file_contents += (
                "config-file-settable-list = [a, b, c, d]\n"
            )
            expected_config_file_contents += "config-file-settable-arg2 = 3\n"

            self.assertEqual(
                cfg_f.read().strip(), expected_config_file_contents.strip()
            )
            self.assertRaisesRegex(
                ValueError,
                "Couldn't open / for writing:",
                self.parse,
                args=command_line_args + " -w /",
            )

    def testConstructor_WriteOutConfigFileArgs2(self):
        # Test constructor args:
        #   args_for_writing_out_config_file
        #   write_out_config_file_arg_help_message
        with self.tmpFile() as cfg_f:
            self.initParser(
                args_for_writing_out_config_file=["-w"],
                write_out_config_file_arg_help_message="write config",
            )

            self.add_arg("-not-config-file-settable")
            self.add_arg("-a", "--arg1", type=int, env_var="ARG1")
            self.add_arg("-b", "--arg2", type=int, default=3)
            self.add_arg("-c", "--arg3")
            self.add_arg("-d", "--arg4")
            self.add_arg("-e", "--arg5")
            self.add_arg(
                "--config-file-settable-flag", action="store_true", env_var="FLAG_ARG"
            )
            self.add_arg("-l", "--config-file-settable-list", action="append")

            # write out a config file
            command_line_args = "-w %s " % cfg_f.name
            command_line_args += "-l a -l b -l c -l d "

            self.assertFalse(self.parser._exit_method_called)

            ns = self.parse(
                command_line_args,
                env_vars={"ARG1": "10", "FLAG_ARG": "true", "SOME_OTHER_ENV_VAR": "2"},
                config_file_contents="arg3 = bla3\narg4 = bla4",
            )
            self.assertTrue(self.parser._exit_method_called)

            cfg_f.seek(0)
            expected_config_file_contents = "config-file-settable-list = [a, b, c, d]\n"
            expected_config_file_contents += "arg1 = 10\n"
            expected_config_file_contents += "config-file-settable-flag = True\n"
            expected_config_file_contents += "arg3 = bla3\n"
            expected_config_file_contents += "arg4 = bla4\n"
            expected_config_file_contents += "arg2 = 3\n"

            self.assertEqual(
                cfg_f.read().strip(), expected_config_file_contents.strip()
            )
            self.assertRaisesRegex(
                ValueError,
                "Couldn't open / for writing:",
                self.parse,
                args=command_line_args + " -w /",
            )

    def testConstructor_WriteOutConfigFileArgsLong(self):
        """Test config writing with long version of arg

        There was a bug where the long version of the
        args_for_writing_out_config_file was being dumped into the resultant
        output config file
        """
        # Test constructor args:
        #   args_for_writing_out_config_file
        #   write_out_config_file_arg_help_message
        with self.tmpFile() as cfg_f:
            self.initParser(
                args_for_writing_out_config_file=["--write-config"],
                write_out_config_file_arg_help_message="write config",
            )

            self.add_arg("-not-config-file-settable")
            self.add_arg("--config-file-settable-arg", type=int)
            self.add_arg("--config-file-settable-arg2", type=int, default=3)
            self.add_arg("--config-file-settable-flag", action="store_true")
            self.add_arg("-l", "--config-file-settable-list", action="append")

            # write out a config file
            command_line_args = f"--write-config {cfg_f.name} "
            command_line_args += "--config-file-settable-arg 1 "
            command_line_args += "--config-file-settable-flag "
            command_line_args += "-l a -l b -l c -l d "

            self.assertFalse(self.parser._exit_method_called)

            ns = self.parse(command_line_args)
            self.assertTrue(self.parser._exit_method_called)

            cfg_f.seek(0)
            expected_config_file_contents = "config-file-settable-arg = 1\n"
            expected_config_file_contents += "config-file-settable-flag = true\n"
            expected_config_file_contents += (
                "config-file-settable-list = [a, b, c, d]\n"
            )
            expected_config_file_contents += "config-file-settable-arg2 = 3\n"

            self.assertEqual(
                cfg_f.read().strip(), expected_config_file_contents.strip()
            )
            self.assertRaisesRegex(
                ValueError,
                "Couldn't open / for writing:",
                self.parse,
                args=command_line_args + " --write-config /",
            )

    def testMethodAliases(self):
        p = self.parser
        p.add("-a", "--arg-a", default=3)
        p.add_arg("-b", "--arg-b", required=True)
        p.add_argument("-c")

        g1 = p.add_argument_group(title="group1", description="descr")
        g1.add("-d", "--arg-d", required=True)
        g1.add_arg("-e", "--arg-e", required=True)
        g1.add_argument("-f", "--arg-f", default=5)

        g2 = p.add_mutually_exclusive_group(required=True)
        g2.add("-x", "--arg-x")
        g2.add_arg("-y", "--arg-y")
        g2.add_argument("-z", "--arg-z", default=5)

        # verify that flags must be globally unique
        g2 = p.add_argument_group(title="group2", description="descr")
        self.assertRaises(argparse.ArgumentError, g1.add, "-c")
        self.assertRaises(argparse.ArgumentError, g2.add, "-f")

        self.initParser()
        p = self.parser
        options = p.parse(args=[])
        self.assertDictEqual(vars(options), {})

    def testConfigOpenFuncError(self):
        # test OSError
        def error_func(path):
            raise OSError(9, "some error")

        self.initParser(config_file_open_func=error_func)
        self.parser.add_argument("-g", is_config_file=True)
        self.assertParseArgsRaises(
            "Unable to open config file: 'file.txt'. Error: some error",
            args="-g file.txt",
        )

        # test other error
        def error_func(path):
            raise Exception("custom error")

        self.initParser(config_file_open_func=error_func)
        self.parser.add_argument("-g", is_config_file=True)
        self.assertParseArgsRaises(
            "Unable to open config file: 'file.txt'. Error: custom error",
            args="-g file.txt",
        )
