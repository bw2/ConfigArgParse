import argparse
import configargparse
import functools
import inspect
import logging
import sys
import tempfile
import types
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

    arg_parser.error = types.MethodType(error_method, arg_parser)
    return arg_parser


class TestCase(unittest.case.TestCase):

    def initParser(self, *args, **kwargs):
        p = configargparse.ArgParser(*args, **kwargs)
        self.parser = replace_error_method(p)
        self.add_arg = self.parser.add_argument
        self.parse = self.parser.parse_args
        self.format_values = self.parser.format_values
        self.format_help = self.parser.format_help
        self.assertAddArgRaises = functools.partial(self.assertRaisesRegexp,
                Exception, callable_obj = self.add_arg)
        self.assertParseArgsRaises = functools.partial(self.assertRaisesRegexp,
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
        self.assertEquals(ns.arg_x, True)
        self.assertEquals(ns.y1, 3)
        self.assertEquals(ns.arg_z, [10])

        self.assertRegexpMatches(self.format_values(),
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
        self.assertEquals(ns.arg_x, True)
        self.assertEquals(ns.y1, 10)
        self.assertEquals(ns.arg_z, [40])

        self.assertRegexpMatches(self.format_values(),
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
        self.assertEquals(ns.arg_x, True)
        self.assertEquals(ns.y1, 3)
        self.assertEquals(ns.arg_z, [100])

        self.assertRegexpMatches(self.format_values(),
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
        self.assertParseArgsRaises("argument -g/--my-cfg-file is required"
                                   if sys.version_info < (3,3) else
                                   "the following arguments are required: -g",
                                   args="--genome hg19")
        self.assertParseArgsRaises("not found: file.txt", args="-g file.txt")

        # check values after setting args on command line
        config_file2 = tempfile.NamedTemporaryFile(mode="w", delete=True)
        config_file2.flush()

        ns = self.parse(args="--genome hg19 -g %s bla.vcf " % config_file2.name)
        self.assertEquals(ns.genome, "hg19")
        self.assertEquals(ns.verbose, False)
        self.assertEquals(ns.dbsnp, None)
        self.assertEquals(ns.fmt, "BED")
        self.assertListEqual(ns.vcf, ["bla.vcf"])

        self.assertRegexpMatches(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ bla.vcf\n'
            'Defaults:\n'
            '  --format: \s+ BED\n')

        # check precedence: args > env > config > default using the --format arg
        default_config_file.write("--format MAF")
        default_config_file.flush()
        ns = self.parse(args="--genome hg19 -g %s f.vcf " % config_file2.name)
        self.assertEquals(ns.fmt, "MAF")
        self.assertRegexpMatches(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ f.vcf\n'
            'Config File \([^\s]+\):\n'
            '  --format: \s+ MAF\n')

        config_file2.write("--format VCF")
        config_file2.flush()
        ns = self.parse(args="--genome hg19 -g %s f.vcf " % config_file2.name)
        self.assertEquals(ns.fmt, "VCF")
        self.assertRegexpMatches(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ f.vcf\n'
            'Config File \([^\s]+\):\n'
            '  --format: \s+ VCF\n')

        ns = self.parse(env_vars={"OUTPUT_FORMAT":"R", "DBSNP_PATH":"/a/b.vcf"},
            args="--genome hg19 -g %s f.vcf " % config_file2.name)
        self.assertEquals(ns.fmt, "R")
        self.assertEquals(ns.dbsnp, "/a/b.vcf")
        self.assertRegexpMatches(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ f.vcf\n'
            'Environment Variables:\n'
            '  DBSNP_PATH: \s+ /a/b.vcf\n'
            '  OUTPUT_FORMAT: \s+ R\n')

        ns = self.parse(env_vars={"OUTPUT_FORMAT":"R", "DBSNP_PATH":"/a/b.vcf",
                                  "ANOTHER_VAR":"something"},
            args="--genome hg19 -g %s --format WIG f.vcf" % config_file2.name)
        self.assertEquals(ns.fmt, "WIG")
        self.assertEquals(ns.dbsnp, "/a/b.vcf")
        self.assertRegexpMatches(self.format_values(),
            'Command Line Args:   --genome hg19 -g [^\s]+ --format WIG f.vcf\n'
            'Environment Variables:\n'
            '  DBSNP_PATH: \s+ /a/b.vcf\n')

        if not use_groups:
            self.assertRegexpMatches(self.format_help(),
                'usage: .* \[-h\] --genome GENOME \[-v\] -g MY_CFG_FILE'
                ' \[-d DBSNP\]\s+\[-f FRMT\]\s+vcf \[vcf ...\]\n\n' +
                6*'.+\s+'+  # repeated 4 times because .+ matches atmost 1 line
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
            self.assertRegexpMatches(self.format_help(),
                'usage: .* \[-h\] --genome GENOME \[-v\] -g MY_CFG_FILE'
                ' \[-d DBSNP\]\s+\[-f FRMT\]\s+vcf \[vcf ...\]\n\n'+
                6*'.+\s+'+  # repeated 4 times because .+ matches atmost 1 line
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
        self.assertEquals(ns.genome, "hg19")
        self.assertEquals(ns.verbose, False)
        self.assertEquals(ns.fmt, "BAM")

        ns = self.parse(env_vars={"BAM_FORMAT" : "true"},
                        args="--genome hg19 -f1 %s" % config_file.name)
        self.assertEquals(ns.genome, "hg19")
        self.assertEquals(ns.verbose, False)
        self.assertEquals(ns.fmt, "BAM")
        self.assertRegexpMatches(self.format_values(),
            'Command Line Args:   --genome hg19 -f1 [^\s]+\n'
            'Environment Variables:\n'
            '  BAM_FORMAT: \s+ true\n'
            'Defaults:\n'
            '  --format: \s+ BED\n')

        self.assertRegexpMatches(self.format_help(),
            'usage: .* \[-h\] --genome GENOME \[-v\]\s+ \(-f1 TYPE1_CFG_FILE \|'
            ' \s*-f2 TYPE2_CFG_FILE\)\s+\(-f FRMT \| -b\)\n\n' +
            4*'.+\s+'+  # repeated 4 times because .+ matches atmost 1 line
            'optional arguments:\n'
            '  -h, --help            show this help message and exit\n'
            '  -f1 TYPE1_CFG_FILE, --type1-cfg-file TYPE1_CFG_FILE\n'
            '  -f2 TYPE2_CFG_FILE, --type2-cfg-file TYPE2_CFG_FILE\n'
            '  -f FRMT, --format FRMT\s+\[env var: OUTPUT_FORMAT\]\n'
            '  -b, --bam\s+\[env var: BAM_FORMAT\]\n\n'
            'group1:\n'
            '  --genome GENOME       Path to genome file\n'
            '  -v\n')

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
        self.assertRegexpMatches(self.format_values(),
            'Command Line Args: \s+ -x 1\n'
            'Config File \(method arg\):\n'
            '  y: \s+ 12.1\n'
            '  b: \s+ True\n'
            '  a: \s+ 33\n')

        # -x is not a long arg so can't be set via config file
        self.assertParseArgsRaises("contains unknown config key\(s\): -x",
                                   config_file_contents="-x 3")
        self.assertParseArgsRaises("contains unknown config key\(s\): bla",
                                   config_file_contents="bla: 3")
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
        self.assertParseArgsRaises("unknown config key\(s\): --bla",
                                   config_file_contents="--bla")



    def testConfigOrEnvValueErrors(self):
        # error should occur when a non-flag arg is set to True
        self.initParser()
        self.add_arg("--height", env_var = "HEIGHT", required=True)
        self.assertParseArgsRaises("HEIGHT set to 'True' rather than a value",
            env_vars={"HEIGHT" : "true"})
        self.assertParseArgsRaises("HEIGHT can't be set to a list '\[1,2,3\]'",
            env_vars={"HEIGHT" : "[1,2,3]"})
        ns = self.parse("", env_vars = {"HEIGHT" : "tall", "VERBOSE": ""})
        self.assertEqual(ns.height, "tall")

        # error should occur when flag arg is given a value
        self.initParser()
        self.add_arg("-v", "--verbose", env_var="VERBOSE", action="store_true")
        self.assertParseArgsRaises("VERBOSE is a flag but is being set to 'bla'",
            env_vars={"VERBOSE" : "bla"})
        self.assertParseArgsRaises("VERBOSE can't be set to a list '\[1,2,3\]'",
            env_vars={"VERBOSE" : "[1,2,3]"})
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
        ns = self.parse("", env_vars = {"FILES": "[1,2,3]", "VERBOSE": "true"})
        self.assertEqual(ns.file, [1,2,3])
        ns = self.parse("", config_file_contents="file=[1,2,3, 5]")
        self.assertEqual(ns.file, [1,2,3,5])


class TestMisc(TestCase):
    # TODO test different action types with config file, env var

    """Test edge cases"""
    def setUp(self):
        self.initParser(args_for_setting_config_path=[])

    def testGlobalInstances(self, name=None):
        p = configargparse.getArgumentParser(name, prog="prog", usage="test")
        self.assertEqual(p.usage, "test")
        self.assertEqual(p.prog, "prog")
        self.assertRaisesRegexp(ValueError, "kwargs besides 'name' can only be "
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
        #   allow_unknown_config_file_keys
        #   config_arg_is_required
        temp_cfg = tempfile.NamedTemporaryFile(mode="w", delete=True)
        temp_cfg.write("genome=hg19")
        temp_cfg.flush()

        # Test constructor args:
        #   args_for_setting_config_path
        #   allow_unknown_config_file_keys
        #   config_arg_is_required
        #   config_arg_help_message
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

        self.assertRegexpMatches(self.format_help(),
            'usage: .* \[-h\] -c CONFIG_FILE --genome GENOME\n\n'+
            4*'.+\s+'+  # repeated 4 times because .+ matches atmost 1 line
            'optional arguments:\n'
            '  -h, --help\s+ show this help message and exit\n'
            '  -c CONFIG_FILE, --config CONFIG_FILE\s+ my config file\n'
            '  --genome GENOME\s+ Path to genome file\n')


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



################################################################################
# since configargparse should work as a drop-in replacement for argparse
# in all situations, run argparse unittests on configargparse by modifying
# their source code to use configargparse.ArgumentParser

try:
    import test.test_argparse
    #Sig = test.test_argparse.Sig
    #NS = test.test_argparse.NS
except ImportError:
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
    #def print_source_code(source_code, line_numbers, context_lines=10):
    #     for n in line_numbers:
    #         logging.debug("##### Code around line %s #####" % n)
    #         lines_to_print = set(range(n - context_lines, n + context_lines))
    #         for n2, line in enumerate(source_code.split("\n"), 1):
    #             if n2 in lines_to_print:
    #                 logging.debug("%s %5d: %s" % (
    #                    "**" if n2 == n else "  ", n2, line))
    #     #sys.exit()
    #print_source_code(test_argparse_source_code, [4540, 4565])
