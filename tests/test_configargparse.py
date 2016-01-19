import argparse
import configargparse
import functools
import inspect
import logging
import sys
import tempfile
import types

if sys.version_info < (2, 7):
    import unittest2 as unittest
else:
    import unittest

if sys.version_info >= (3, 0):
    from io import StringIO
else:
    from StringIO import StringIO

# enable logging to simplify debugging
logger = logging.getLogger()
logger.level = logging.DEBUG
stream_handler = logging.StreamHandler(sys.stdout)
logger.addHandler(stream_handler)

def replace_error_method(arg_parser):
    """Swap out arg_parser's error(..) method so that instead of calling
    sys.exit(..) it just raises an error.
    """
    def error_method(self, message):
        raise argparse.ArgumentError(None, message)

    def exit_method(self, status, message):
        self._exit_method_called = True

    arg_parser._exit_method_called = False
    arg_parser.error = types.MethodType(error_method, arg_parser)
    arg_parser.exit = types.MethodType(exit_method, arg_parser)

    return arg_parser


class TestCase(unittest.TestCase):

    def initParser(self, *args, **kwargs):
        p = configargparse.ArgParser(*args, **kwargs)
        self.parser = replace_error_method(p)
        self.add_arg = self.parser.add_argument
        self.parse = self.parser.parse_args
        self.parse_known = self.parser.parse_known_args
        self.format_values = self.parser.format_values
        self.format_help = self.parser.format_help

        if not hasattr(self, "assertRegex"):
            self.assertRegex = self.assertRegexpMatches
        if not hasattr(self, "assertRaisesRegex"):
            self.assertRaisesRegex = self.assertRaisesRegexp

        self.assertParseArgsRaises = functools.partial(self.assertRaisesRegex,
            argparse.ArgumentError, callable_obj = self.parse)

        return self.parser


class TestBasicUseCases(TestCase):
    def setUp(self):
        self.initParser(args_for_setting_config_path=[])

    def testBasicCase1(self):
        ## Test command line and config file values
        self.add_arg("filenames", nargs="+", help="positional arg")
        self.add_arg("-x", "--arg-x", action="store_true")
        self.add_arg("-y", "--arg-y", dest="y1", type=int, required=True)
        self.add_arg("--arg-z", action="append", type=float, required=True)

        # make sure required args are enforced
        self.assertParseArgsRaises("too few arg"
            if sys.version_info < (3,3) else
            "the following arguments are required",  args="")
        self.assertParseArgsRaises("argument -y/--arg-y is required"
            if sys.version_info < (3,3) else
            "the following arguments are required: -y/--arg-y",
            args="-x --arg-z 11 file1.txt")
        self.assertParseArgsRaises("argument --arg-z is required"
            if sys.version_info < (3,3) else
            "the following arguments are required: --arg-z",
            args="file1.txt file2.txt file3.txt -x -y 1")

        # check values after setting args on command line
        ns = self.parse(args="file1.txt --arg-x -y 3 --arg-z 10",
                        config_file_contents="")
        self.assertListEqual(ns.filenames, ["file1.txt"])
        self.assertEqual(ns.arg_x, True)
        self.assertEqual(ns.y1, 3)
        self.assertEqual(ns.arg_z, [10])

        self.assertRegex(self.format_values(),
            'Command Line Args:   file1.txt --arg-x -y 3 --arg-z 10')

        # check values after setting args in config file
        ns = self.parse(args="file1.txt file2.txt", config_file_contents="""
            # set all required args in config file
            arg-x = True
            arg-y = 10
            arg-z = 30
            arg-z = 40
            """)
        self.assertListEqual(ns.filenames, ["file1.txt", "file2.txt"])
        self.assertEqual(ns.arg_x, True)
        self.assertEqual(ns.y1, 10)
        self.assertEqual(ns.arg_z, [40])

        self.assertRegex(self.format_values(),
            'Command Line Args: \s+ file1.txt file2.txt\n'
            'Config File \(method arg\):\n'
            '  arg-x: \s+ True\n'
            '  arg-y: \s+ 10\n'
            '  arg-z: \s+ 40\n')

        # check values after setting args in both command line and config file
        ns = self.parse(args="file1.txt file2.txt --arg-x -y 3 --arg-z 100 ",
            config_file_contents="""arg-y = 31.5
                                    arg-z = 30
                                 """)
        self.format_help()
        self.format_values()
        self.assertListEqual(ns.filenames, ["file1.txt", "file2.txt"])
        self.assertEqual(ns.arg_x, True)
        self.assertEqual(ns.y1, 3)
        self.assertEqual(ns.arg_z, [100])

        self.assertRegex(self.format_values(),
            "Command Line Args:   file1.txt file2.txt --arg-x -y 3 --arg-z 100")

    def testBasicCase2(self, use_groups=False):

        ## Test command line, config file and env var values
        default_config_file = tempfile.NamedTemporaryFile(mode="w", delete=True)
        default_config_file.flush()

        p = self.initParser(default_config_files=['/etc/settings.ini',
                '/home/jeff/.user_settings', default_config_file.name])
        p.add_arg('vcf', nargs='+', help='Variant file(s)')
        if not use_groups:
            self.add_arg('--genome', help='Path to genome file', required=True)
            self.add_arg('-v', dest='verbose', action='store_true')
            self.add_arg('-g', '--my-cfg-file', required=True,
                         is_config_file=True)
            self.add_arg('-d', '--dbsnp', env_var='DBSNP_PATH')
            self.add_arg('-f', '--format',
                         choices=["BED", "MAF", "VCF", "WIG", "R"],
                         dest="fmt", metavar="FRMT", env_var="OUTPUT_FORMAT",
                         default="BED")
        else:
            g = p.add_argument_group(title="g1")
            g.add_arg('--genome', help='Path to genome file', required=True)
            g.add_arg('-v', dest='verbose', action='store_true')
            g.add_arg('-g', '--my-cfg-file', required=True,
                      is_config_file=True)
            g = p.add_argument_group(title="g2")
            g.add_arg('-d', '--dbsnp', env_var='DBSNP_PATH')
            g.add_arg('-f', '--format',
                      choices=["BED", "MAF", "VCF", "WIG", "R"],
                      dest="fmt", metavar="FRMT", env_var="OUTPUT_FORMAT",
                      default="BED")

        # make sure required args are enforced
        self.assertParseArgsRaises("too few arg"
                                   if sys.version_info < (3,3) else
                                   "the following arguments are required: vcf, -g/--my-cfg-file",
                                   args="--genome hg19")
        self.assertParseArgsRaises("not found: file.txt", args="-g file.txt")

        # check values after setting args on command line
        config_file2 = tempfile.NamedTemporaryFile(mode="w", delete=True)
        config_file2.flush()

        ns = self.parse(args="--genome hg19 -g %s bla.vcf " % config_file2.name)
        self.assertEqual(ns.genome, "hg19")
        self.assertEqual(ns.verbose, False)
        self.assertEqual(ns.dbsnp, None)
        self.assertEqual(ns.fmt, "BED")
        self.assertListEqual(ns.vcf, ["bla.vcf"])

        self.assertRegex(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ bla.vcf\n'
            'Defaults:\n'
            '  --format: \s+ BED\n')

        # check precedence: args > env > config > default using the --format arg
        default_config_file.write("--format MAF")
        default_config_file.flush()
        ns = self.parse(args="--genome hg19 -g %s f.vcf " % config_file2.name)
        self.assertEqual(ns.fmt, "MAF")
        self.assertRegex(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ f.vcf\n'
            'Config File \([^\s]+\):\n'
            '  --format: \s+ MAF\n')

        config_file2.write("--format VCF")
        config_file2.flush()
        ns = self.parse(args="--genome hg19 -g %s f.vcf " % config_file2.name)
        self.assertEqual(ns.fmt, "VCF")
        self.assertRegex(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ f.vcf\n'
            'Config File \([^\s]+\):\n'
            '  --format: \s+ VCF\n')

        ns = self.parse(env_vars={"OUTPUT_FORMAT":"R", "DBSNP_PATH":"/a/b.vcf"},
            args="--genome hg19 -g %s f.vcf " % config_file2.name)
        self.assertEqual(ns.fmt, "R")
        self.assertEqual(ns.dbsnp, "/a/b.vcf")
        self.assertRegex(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ f.vcf\n'
            'Environment Variables:\n'
            '  DBSNP_PATH: \s+ /a/b.vcf\n'
            '  OUTPUT_FORMAT: \s+ R\n')

        ns = self.parse(env_vars={"OUTPUT_FORMAT":"R", "DBSNP_PATH":"/a/b.vcf",
                                  "ANOTHER_VAR":"something"},
            args="--genome hg19 -g %s --format WIG f.vcf" % config_file2.name)
        self.assertEqual(ns.fmt, "WIG")
        self.assertEqual(ns.dbsnp, "/a/b.vcf")
        self.assertRegex(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ --format WIG f.vcf\n'
            'Environment Variables:\n'
            '  DBSNP_PATH: \s+ /a/b.vcf\n')

        if not use_groups:
            self.assertRegex(self.format_help(),
                'usage: .* \[-h\] --genome GENOME \[-v\] -g MY_CFG_FILE'
                ' \[-d DBSNP\]\s+\[-f FRMT\]\s+vcf \[vcf ...\]\n\n' +
                9*'(.+\s+)'+  # repeated 8 times because .+ matches atmost 1 line
                'positional arguments:\n'
                '  vcf \s+ Variant file\(s\)\n\n'
                'optional arguments:\n'
                '  -h, --help \s+ show this help message and exit\n'
                '  --genome GENOME \s+ Path to genome file\n'
                '  -v\n'
                '  -g MY_CFG_FILE, --my-cfg-file MY_CFG_FILE\n'
                '  -d DBSNP, --dbsnp DBSNP\s+\[env var: DBSNP_PATH\]\n'
                '  -f FRMT, --format FRMT\s+\[env var: OUTPUT_FORMAT\]\n')
        else:
            self.assertRegex(self.format_help(),
                'usage: .* \[-h\] --genome GENOME \[-v\] -g MY_CFG_FILE'
                ' \[-d DBSNP\]\s+\[-f FRMT\]\s+vcf \[vcf ...\]\n\n'+
                9*'.+\s+'+  # repeated 8 times because .+ matches atmost 1 line
                'positional arguments:\n'
                '  vcf \s+ Variant file\(s\)\n\n'
                'optional arguments:\n'
                '  -h, --help \s+ show this help message and exit\n\n'
                'g1:\n'
                '  --genome GENOME \s+ Path to genome file\n'
                '  -v\n'
                '  -g MY_CFG_FILE, --my-cfg-file MY_CFG_FILE\n\n'
                'g2:\n'
                '  -d DBSNP, --dbsnp DBSNP\s+\[env var: DBSNP_PATH\]\n'
                '  -f FRMT, --format FRMT\s+\[env var: OUTPUT_FORMAT\]\n')

        self.assertParseArgsRaises("invalid choice: 'ZZZ'",
            args="--genome hg19 -g %s --format ZZZ f.vcf" % config_file2.name)
        self.assertParseArgsRaises("unrecognized arguments: --bla",
            args="--bla --genome hg19 -g %s f.vcf" % config_file2.name)

        default_config_file.close()
        config_file2.close()


    def testBasicCase2_WithGroups(self):
        self.testBasicCase2(use_groups=True)


    def testMutuallyExclusiveArgs(self):
        config_file = tempfile.NamedTemporaryFile(mode="w", delete=True)

        p = self.parser
        g = p.add_argument_group(title="group1")
        g.add_arg('--genome', help='Path to genome file', required=True)
        g.add_arg('-v', dest='verbose', action='store_true')

        g = p.add_mutually_exclusive_group(required=True)
        g.add_arg('-f1', '--type1-cfg-file', is_config_file=True)
        g.add_arg('-f2', '--type2-cfg-file', is_config_file=True)

        g = p.add_mutually_exclusive_group(required=True)
        g.add_arg('-f', '--format', choices=["BED", "MAF", "VCF", "WIG", "R"],
                     dest="fmt", metavar="FRMT", env_var="OUTPUT_FORMAT",
                     default="BED")
        g.add_arg('-b', '--bam', dest='fmt', action="store_const", const="BAM",
                  env_var='BAM_FORMAT')

        ns = self.parse(args="--genome hg19 -f1 %s --bam" % config_file.name)
        self.assertEqual(ns.genome, "hg19")
        self.assertEqual(ns.verbose, False)
        self.assertEqual(ns.fmt, "BAM")

        ns = self.parse(env_vars={"BAM_FORMAT" : "true"},
                        args="--genome hg19 -f1 %s" % config_file.name)
        self.assertEqual(ns.genome, "hg19")
        self.assertEqual(ns.verbose, False)
        self.assertEqual(ns.fmt, "BAM")
        self.assertRegex(self.format_values(),
            'Command Line Args:   --genome hg19 -f1 [^\s]+\n'
            'Environment Variables:\n'
            '  BAM_FORMAT: \s+ true\n'
            'Defaults:\n'
            '  --format: \s+ BED\n')

        self.assertRegex(self.format_help(),
            'usage: .* \[-h\] --genome GENOME \[-v\]\s+ \(-f1 TYPE1_CFG_FILE \|'
            ' \s*-f2 TYPE2_CFG_FILE\)\s+\(-f FRMT \| -b\)\n\n' +
            7*'.+\s+'+  # repeated 7 times because .+ matches atmost 1 line
            'optional arguments:\n'
            '  -h, --help            show this help message and exit\n'
            '  -f1 TYPE1_CFG_FILE, --type1-cfg-file TYPE1_CFG_FILE\n'
            '  -f2 TYPE2_CFG_FILE, --type2-cfg-file TYPE2_CFG_FILE\n'
            '  -f FRMT, --format FRMT\s+\[env var: OUTPUT_FORMAT\]\n'
            '  -b, --bam\s+\[env var: BAM_FORMAT\]\n\n'
            'group1:\n'
            '  --genome GENOME       Path to genome file\n'
            '  -v\n')
        config_file.close()

    def testSubParsers(self):
        config_file1 = tempfile.NamedTemporaryFile(mode="w", delete=True)
        config_file1.write("--i = B")
        config_file1.flush()

        config_file2 = tempfile.NamedTemporaryFile(mode="w", delete=True)
        config_file2.write("p = 10")
        config_file2.flush()

        parser = configargparse.ArgumentParser(prog="myProg")
        subparsers = parser.add_subparsers(title="actions")

        parent_parser = configargparse.ArgumentParser(add_help=False)
        parent_parser.add_argument("-p", "--p", type=int, required=True,
                                   help="set db parameter")

        create_p = subparsers.add_parser("create", parents=[parent_parser],
                                         help="create the orbix environment")
        create_p.add_argument("--i", env_var="INIT", choices=["A","B"],
                              default="A")
        create_p.add_argument("-config", is_config_file=True)


        update_p = subparsers.add_parser("update", parents=[parent_parser],
                                         help="update the orbix environment")
        update_p.add_argument("-config2", is_config_file=True, required=True)

        ns = parser.parse_args(args = "create -p 2 -config "+config_file1.name)
        self.assertEqual(ns.p, 2)
        self.assertEqual(ns.i, "B")

        ns = parser.parse_args(args = "update -config2 " + config_file2.name)
        self.assertEqual(ns.p, 10)
        config_file1.close()
        config_file2.close()

    def testAddArgsErrors(self):
        self.assertRaisesRegex(ValueError, "arg with "
            "is_write_out_config_file_arg=True can't also have "
            "is_config_file_arg=True", self.add_arg, "-x", "--X",
            is_config_file=True, is_write_out_config_file_arg=True)
        self.assertRaisesRegex(ValueError, "arg with "
            "is_write_out_config_file_arg=True must have action='store'",
            self.add_arg, "-y", "--Y", action="append",
            is_write_out_config_file_arg=True)


    def testConfigFileSyntax(self):
        self.add_arg('-x', required=True, type=int)
        self.add_arg('--y', required=True, type=float)
        self.add_arg('--z')
        self.add_arg('--b', action="store_true")
        self.add_arg('--a', action="append", type=int)

        ns = self.parse(args="-x 1", env_vars={}, config_file_contents="""

        #inline comment 1
        # inline comment 2
          # inline comment 3
        ;inline comment 4
        ; inline comment 5
          ;inline comment 6

        ---   # separator 1
        -------------  # separator 2

        y=1.1
          y = 2.1
        y= 3.1  # with comment
        y= 4.1  ; with comment
        ---
        y:5.1
          y : 6.1
        y: 7.1  # with comment
        y: 8.1  ; with comment
        ---
        y  \t 9.1
          y 10.1
        y 11.1  # with comment
        y 12.1  ; with comment
        ---
        b
        b = True
        b: True
        ----
        a = 33
        """)

        self.assertEqual(ns.x, 1)
        self.assertEqual(ns.y, 12.1)
        self.assertEqual(ns.z, None)
        self.assertEqual(ns.b, True)
        self.assertEqual(ns.a, [33])
        self.assertRegex(self.format_values(),
            'Command Line Args: \s+ -x 1\n'
            'Config File \(method arg\):\n'
            '  y: \s+ 12.1\n'
            '  b: \s+ True\n'
            '  a: \s+ 33\n')

        # -x is not a long arg so can't be set via config file
        self.assertParseArgsRaises("argument -x is required"
                                   if sys.version_info < (3,3) else
                                   "the following arguments are required: -x, --y",
                                   config_file_contents="-x 3")
        self.assertParseArgsRaises("invalid float value: 'abc'",
                                   args="-x 5",
                                   config_file_contents="y: abc")
        self.assertParseArgsRaises("argument --y is required"
                                   if sys.version_info < (3,3) else
                                   "the following arguments are required: --y",
                                   args="-x 5",
                                   config_file_contents="z: 1")
        self.assertParseArgsRaises("Unexpected line 0",
                                   config_file_contents="z z 1")

        # test unknown config file args
        self.assertParseArgsRaises("bla",
            args="-x 1 --y 2.3",
            config_file_contents="bla=3")

        ns, args = self.parse_known("-x 10 --y 3.8",
                        config_file_contents="bla=3",
                        env_vars={"bla": "2"})
        self.assertListEqual(args, ["--bla", "3"])

        self.initParser(ignore_unknown_config_file_keys=False)
        ns, args = self.parse_known(args="-x 1", config_file_contents="bla=3",
            env_vars={"bla": "2"})
        self.assertListEqual(args, ["--bla", "3", "-x", "1"])

    def testConfigOrEnvValueErrors(self):
        # error should occur when a flag arg is set to something other than "true" or "false"
        self.initParser()
        self.add_arg("--height", env_var = "HEIGHT", required=True)
        self.add_arg("--do-it", dest="x", env_var = "FLAG1", action="store_true")
        self.add_arg("--dont-do-it", dest="x", env_var = "FLAG2", action="store_false")
        ns = self.parse("", env_vars = {"HEIGHT": "tall", "FLAG1": "yes"})
        self.assertEqual(ns.height, "tall")
        self.assertEqual(ns.x, True)
        ns = self.parse("", env_vars = {"HEIGHT": "tall", "FLAG2": "no"})
        self.assertEqual(ns.x, False)

        # error should occur when flag arg is given a value
        self.initParser()
        self.add_arg("-v", "--verbose", env_var="VERBOSE", action="store_true")
        self.assertParseArgsRaises("Unexpected value for VERBOSE: 'bla'. "
                                   "Expecting 'true', 'false', 'yes', or 'no'",
            env_vars={"VERBOSE" : "bla"})
        ns = self.parse("",
                        config_file_contents="verbose=true",
                        env_vars={"HEIGHT": "true"})
        self.assertEqual(ns.verbose, True)
        ns = self.parse("",
                        config_file_contents="verbose",
                        env_vars={"HEIGHT": "true"})
        self.assertEqual(ns.verbose, True)
        ns = self.parse("", env_vars = {"HEIGHT": "true", "VERBOSE": "true"})
        self.assertEqual(ns.verbose, True)
        ns = self.parse("", config_file_contents="--verbose",
                        env_vars = {"HEIGHT": "true"})
        self.assertEqual(ns.verbose, True)

        # error should occur is non-append arg is given a list value
        self.initParser()
        self.add_arg("-f", "--file", env_var="FILES", action="append", type=int)
        ns = self.parse("", env_vars = {"file": "[1,2,3]", "VERBOSE": "true"})
        self.assertEqual(ns.file, None)

    def testAutoEnvVarPrefix(self):
        self.initParser(auto_env_var_prefix="TEST_")
        self.add_arg("-a", "--arg0", is_config_file_arg=True)
        self.add_arg("-b", "--arg1", is_write_out_config_file_arg=True)
        self.add_arg("-x", "--arg2", env_var="TEST2", type=int)
        self.add_arg("-y", "--arg3", action="append", type=int)
        self.add_arg("-z", "--arg4", required=True)
        self.add_arg("-w", "--arg4-more", required=True)
        ns = self.parse("", env_vars = {
            "TEST_ARG0": "0",
            "TEST_ARG1": "1",
            "TEST_ARG2": "2",
            "TEST2": "22",
            "TEST_ARG4": "arg4_value",
            "TEST_ARG4_MORE": "magic"})
        self.assertEqual(ns.arg0, None)
        self.assertEqual(ns.arg1, None)
        self.assertEqual(ns.arg2, 22)
        self.assertEqual(ns.arg4, "arg4_value")
        self.assertEqual(ns.arg4_more, "magic")

class TestMisc(TestCase):
    # TODO test different action types with config file, env var

    """Test edge cases"""
    def setUp(self):
        self.initParser(args_for_setting_config_path=[])

    def testGlobalInstances(self, name=None):
        p = configargparse.getArgumentParser(name, prog="prog", usage="test")
        self.assertEqual(p.usage, "test")
        self.assertEqual(p.prog, "prog")
        self.assertRaisesRegex(ValueError, "kwargs besides 'name' can only be "
            "passed in the first time", configargparse.getArgumentParser, name,
            prog="prog")

        p2 = configargparse.getArgumentParser(name)
        self.assertEqual(p, p2)

    def testGlobalInstances_WithName(self):
        self.testGlobalInstances("name1")
        self.testGlobalInstances("name2")

    def testAddArguments_ArgValidation(self):
        self.assertRaises(ValueError, self.add_arg, 'positional', env_var="bla")
        action = self.add_arg('positional')
        self.assertIsNotNone(action)
        self.assertEqual(action.dest, "positional")

    def testAddArguments_IsConfigFilePathArg(self):
        self.assertRaises(ValueError, self.add_arg, 'c', action="store_false",
                          is_config_file=True)

        self.add_arg("-c", "--config", is_config_file=True)
        self.add_arg("--x", required=True)

        # verify parsing from config file
        config_file = tempfile.NamedTemporaryFile(mode="w", delete=True)
        config_file.write("x=bla")
        config_file.flush()

        ns = self.parse(args="-c %s" % config_file.name)
        self.assertEqual(ns.x, "bla")

    def testConstructor_ConfigFileArgs(self):
        # Test constructor args:
        #   args_for_setting_config_path
        #   config_arg_is_required
        #   config_arg_help_message
        temp_cfg = tempfile.NamedTemporaryFile(mode="w", delete=True)
        temp_cfg.write("genome=hg19")
        temp_cfg.flush()

        self.initParser(args_for_setting_config_path=["-c", "--config"],
                        config_arg_is_required = True,
                        config_arg_help_message = "my config file",
                        default_config_files=[temp_cfg.name])
        self.add_arg('--genome', help='Path to genome file', required=True)
        self.assertParseArgsRaises("argument -c/--config is required"
                                   if sys.version_info < (3,3) else
                                   "arguments are required: -c/--config",)

        temp_cfg2 = tempfile.NamedTemporaryFile(mode="w", delete=True)
        ns = self.parse("-c " + temp_cfg2.name)
        self.assertEqual(ns.genome, "hg19")

        # temp_cfg2 config file should override default config file values
        temp_cfg2.write("genome=hg20")
        temp_cfg2.flush()
        ns = self.parse("-c " + temp_cfg2.name)
        self.assertEqual(ns.genome, "hg20")

        self.assertRegex(self.format_help(),
            'usage: .* \[-h\] -c CONFIG_FILE --genome GENOME\n\n'+
            7*'.+\s+'+  # repeated 7 times because .+ matches atmost 1 line
            'optional arguments:\n'
            '  -h, --help\s+ show this help message and exit\n'
            '  -c CONFIG_FILE, --config CONFIG_FILE\s+ my config file\n'
            '  --genome GENOME\s+ Path to genome file\n')

        # just run print_values() to make sure it completes and returns None
        self.assertEqual(self.parser.print_values(file=sys.stderr), None)

        # test ignore_unknown_config_file_keys=False
        self.initParser(ignore_unknown_config_file_keys=False)
        self.assertRaisesRegex(argparse.ArgumentError, "unrecognized arguments",
            self.parse, config_file_contents="arg1 = 3")
        ns, args = self.parse_known(config_file_contents="arg1 = 3")
        self.assertEqual(getattr(ns, "arg1", ""), "")

        # test ignore_unknown_config_file_keys=True
        self.initParser(ignore_unknown_config_file_keys=True)
        ns = self.parse(args="", config_file_contents="arg1 = 3")
        self.assertEqual(getattr(ns, "arg1", ""), "")
        ns, args = self.parse_known(config_file_contents="arg1 = 3")
        self.assertEqual(getattr(ns, "arg1", ""), "")


    def test_FormatHelp(self):
        self.initParser(args_for_setting_config_path=["-c", "--config"],
                        config_arg_is_required = True,
                        config_arg_help_message = "my config file",
                        default_config_files=["~/.myconfig"],
                        args_for_writing_out_config_file=["-w", "--write-config"],
                        )
        self.add_arg('--arg1', help='Arg1 help text', required=True)
        self.add_arg('--flag', help='Flag help text', action="store_true")

        self.assertRegex(self.format_help(),
            'usage: .* \[-h\] -c CONFIG_FILE\s+'
            '\[-w CONFIG_OUTPUT_PATH\]\s* --arg1 ARG1\s*\[--flag\]\s*'
            'Args that start with \'--\' \(eg. --arg1\) can also be set in a '
            'config file\s*\(~/.myconfig or specified via -c\).\s*'
            'Config file syntax allows: key=value,\s*flag=true, stuff=\[a,b,c\] '
            '\(for details, see syntax at https://goo.gl/R74nmi\).\s*'
            'If an arg is specified in more than\s*one place, then '
            'commandline values\s*override config file values which override\s*'
            'defaults.\s*'
            'optional arguments:\s*'
            '-h, --help \s* show this help message and exit\n\s*'
            '-c CONFIG_FILE, --config CONFIG_FILE\s+my config file\s*'
            '-w CONFIG_OUTPUT_PATH, --write-config CONFIG_OUTPUT_PATH\s*takes\s*'
            'the current command line args and writes them\s*'
            'out to a config file at the given path, then exits\s*'
            '--arg1 ARG1\s*Arg1 help text\s*'
            '--flag \s*Flag help text'
        )

    def testConstructor_WriteOutConfigFileArgs(self):
        # Test constructor args:
        #   args_for_writing_out_config_file
        #   write_out_config_file_arg_help_message
        cfg_f = tempfile.NamedTemporaryFile(mode="w+", delete=True)
        self.initParser(args_for_writing_out_config_file=["-w"],
                        write_out_config_file_arg_help_message="write config")


        self.add_arg("-not-config-file-settable")
        self.add_arg("--config-file-settable-arg", type=int)
        self.add_arg("--config-file-settable-arg2", type=int, default=3)
        self.add_arg("--config-file-settable-flag", action="store_true")
        self.add_arg("-l", "--config-file-settable-list", action="append")

        # write out a config file
        command_line_args = "-w %s " % cfg_f.name
        command_line_args += "--config-file-settable-arg 1 "
        command_line_args += "--config-file-settable-flag "
        command_line_args += "-l a -l b -l c -l d "

        self.assertFalse(self.parser._exit_method_called)

        ns = self.parse(command_line_args)
        self.assertTrue(self.parser._exit_method_called)

        cfg_f.seek(0)
        expected_config_file_contents = "config-file-settable-arg = 1\n"
        expected_config_file_contents += "config-file-settable-flag = true\n"
        expected_config_file_contents += "config-file-settable-list = [a, b, c, d]\n"
        expected_config_file_contents += "config-file-settable-arg2 = 3\n"

        self.assertEqual(cfg_f.read().strip(),
            expected_config_file_contents.strip())
        self.assertRaisesRegex(ValueError, "Couldn't open / for writing:",
            self.parse, args = command_line_args + " -w /")

    def testConstructor_WriteOutConfigFileArgs2(self):
        # Test constructor args:
        #   args_for_writing_out_config_file
        #   write_out_config_file_arg_help_message
        cfg_f = tempfile.NamedTemporaryFile(mode="w+", delete=True)
        self.initParser(args_for_writing_out_config_file=["-w"],
                        write_out_config_file_arg_help_message="write config")


        self.add_arg("-not-config-file-settable")
        self.add_arg("-a", "--arg1", type=int, env_var="ARG1")
        self.add_arg("-b", "--arg2", type=int, default=3)
        self.add_arg("-c", "--arg3")
        self.add_arg("-d", "--arg4")
        self.add_arg("-e", "--arg5")
        self.add_arg("--config-file-settable-flag", action="store_true",
                     env_var="FLAG_ARG")
        self.add_arg("-l", "--config-file-settable-list", action="append")

        # write out a config file
        command_line_args = "-w %s " % cfg_f.name
        command_line_args += "-l a -l b -l c -l d "

        self.assertFalse(self.parser._exit_method_called)

        ns = self.parse(command_line_args,
                        env_vars={"ARG1": "10", "FLAG_ARG": "true",
                                "SOME_OTHER_ENV_VAR": "2"},
                        config_file_contents="arg3 = bla3\narg4 = bla4")
        self.assertTrue(self.parser._exit_method_called)

        cfg_f.seek(0)
        expected_config_file_contents = "config-file-settable-list = [a, b, c, d]\n"
        expected_config_file_contents += "arg1 = 10\n"
        expected_config_file_contents += "config-file-settable-flag = True\n"
        expected_config_file_contents += "arg3 = bla3\n"
        expected_config_file_contents += "arg4 = bla4\n"
        expected_config_file_contents += "arg2 = 3\n"

        self.assertEqual(cfg_f.read().strip(),
                         expected_config_file_contents.strip())
        self.assertRaisesRegex(ValueError, "Couldn't open / for writing:",
                                self.parse, args = command_line_args + " -w /")

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


class TestConfigFileParsers(TestCase):
    """Test ConfigFileParser subclasses in isolation"""

    def testDefaultConfigFileParser_Basic(self):
        p = configargparse.DefaultConfigFileParser()
        self.assertTrue(len(p.get_syntax_description()) > 0)

        # test the simplest case
        input_config_str = StringIO("""a: 3\n""")
        parsed_obj = p.parse(input_config_str)
        output_config_str = p.serialize(parsed_obj)

        self.assertEqual(input_config_str.getvalue().replace(": ", " = "),
                         output_config_str)

        self.assertDictEqual(parsed_obj, dict([('a', '3')]))

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
        input_config_str = StringIO("\n".join(config_lines)+"\n")
        parsed_obj = p.parse(input_config_str)

        # test serialize
        output_config_str = p.serialize(parsed_obj)
        self.assertEqual("\n".join(
            l.replace(': ', ' = ') for l in config_lines if l.startswith('_'))+"\n",
            output_config_str)

        self.assertDictEqual(parsed_obj, dict([
            ('_a', '3'),
            ('_b', 'c'),
            ('_list_arg1', ['a', 'b', 'c']),
            ('_str_arg', 'true'),
            ('_list_arg2', ['1', '2', '3']),
        ]))

        self.assertListEqual(parsed_obj['_list_arg1'], ['a', 'b', 'c'])
        self.assertListEqual(parsed_obj['_list_arg2'], ['1', '2', '3'])

    def testYAMLConfigFileParser_Basic(self):
        try:
            import yaml
        except:
            logging.warning("WARNING: PyYAML not installed. "
                            "Couldn't test YAMLConfigFileParser")
            return

        p = configargparse.YAMLConfigFileParser()
        self.assertTrue(len(p.get_syntax_description()) > 0)

        input_config_str = StringIO("""a: '3'\n""")
        parsed_obj = p.parse(input_config_str)
        output_config_str = p.serialize(dict(parsed_obj))

        self.assertEqual(input_config_str.getvalue(), output_config_str)

        self.assertDictEqual(parsed_obj, dict([('a', '3')]))

    def testYAMLConfigFileParser_All(self):
        try:
            import yaml
        except:
            logging.warning("WARNING: PyYAML not installed. "
                            "Couldn't test YAMLConfigFileParser")
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
        input_config_str = StringIO("\n".join(config_lines)+"\n")
        parsed_obj = p.parse(input_config_str)

        # test serialize
        output_config_str = p.serialize(parsed_obj)
        self.assertEqual(input_config_str.getvalue(), output_config_str)

        self.assertDictEqual(parsed_obj, dict([
            ('a', '3'),
            ('list_arg', [1,2,3]),
        ]))



################################################################################
# since configargparse should work as a drop-in replacement for argparse
# in all situations, run argparse unittests on configargparse by modifying
# their source code to use configargparse.ArgumentParser

try:
    import test.test_argparse
    #Sig = test.test_argparse.Sig
    #NS = test.test_argparse.NS
except ImportError:
    if sys.version_info < (2, 7):
        logging.info("\n\n" + ("=" * 30) +
                     "\nINFO: Skipping tests for argparse (Python < 2.7)\n"
                     + ("=" * 30) + "\n")
    else:
        logging.error("\n\n"
            "============================\n"
            "ERROR: Many tests couldn't be run because 'import test.test_argparse' "
            "failed. Try building/installing python from source rather than through"
            " a package manager.\n"
            "============================\n")
else:
    test_argparse_source_code = inspect.getsource(test.test_argparse)
    test_argparse_source_code = test_argparse_source_code.replace(
        'argparse.ArgumentParser', 'configargparse.ArgumentParser')

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
