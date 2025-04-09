import sys
import argparse
import configargparse
from unittest import mock
from tempfile import NamedTemporaryFile
from io import StringIO

from tests.test_base import TestCase, captured_output

class TestBasicUseCases(TestCase):

    def testBasicCase1(self):
        ## Test command line and config file values
        self.add_arg("filenames", nargs="+", help="positional arg")
        self.add_arg("-x", "--arg-x", action="store_true")
        self.add_arg("-y", "--arg-y", dest="y1", type=int, required=True)
        self.add_arg("--arg-z", action="append", type=float, required=True)
        if sys.version_info >= (3, 9):
            self.add_arg('--foo', action=argparse.BooleanOptionalAction, default=False)
        else:
            self.add_arg('--foo', action="store_true", default=False)

        # make sure required args are enforced
        self.assertParseArgsRaises("the following arguments are required",  args="")
        self.assertParseArgsRaises("the following arguments are required: -y/--arg-y",
            args="-x --arg-z 11 file1.txt")
        self.assertParseArgsRaises("the following arguments are required: --arg-z",
            args="file1.txt file2.txt file3.txt -x -y 1")

        # check values after setting args on command line
        ns = self.parse(args="file1.txt --arg-x -y 3 --arg-z 10 --foo",
                        config_file_contents="")
        self.assertListEqual(ns.filenames, ["file1.txt"])
        self.assertEqual(ns.arg_x, True)
        self.assertEqual(ns.y1, 3)
        self.assertEqual(ns.arg_z, [10])
        self.assertEqual(ns.foo, True)

        self.assertRegex(self.format_values(),
            'Command Line Args:   file1.txt --arg-x -y 3 --arg-z 10')

        # check values after setting args in config file
        ns = self.parse(args="file1.txt file2.txt", config_file_contents="""
            # set all required args in config file
            arg-x = True
            arg-y = 10
            arg-z = 30
            arg-z = 40
            foo = True
            """)
        self.assertListEqual(ns.filenames, ["file1.txt", "file2.txt"])
        self.assertEqual(ns.arg_x, True)
        self.assertEqual(ns.y1, 10)
        self.assertEqual(ns.arg_z, [40])
        self.assertEqual(ns.foo, True)

        self.assertRegex(self.format_values(),
            'Command Line Args: \\s+ file1.txt file2.txt\n'
            'Config File \\(method arg\\):\n'
            '  arg-x: \\s+ True\n'
            '  arg-y: \\s+ 10\n'
            '  arg-z: \\s+ 40\n'
            '  foo: \\s+ True\n')

        # check values after setting args in both command line and config file
        ns = self.parse(args="file1.txt file2.txt --arg-x -y 3 --arg-z 100 ",
            config_file_contents="""arg-y = 31.5
                                    arg-z = 30
                                    foo = True
                                 """)
        self.format_help()
        self.format_values()
        self.assertListEqual(ns.filenames, ["file1.txt", "file2.txt"])
        self.assertEqual(ns.arg_x, True)
        self.assertEqual(ns.y1, 3)
        self.assertEqual(ns.arg_z, [100])
        self.assertEqual(ns.foo, True)

        self.assertRegex(self.format_values(),
            "Command Line Args:   file1.txt file2.txt --arg-x -y 3 --arg-z 100")

    def testBasicCase2(self, use_groups=False):

        ## Test command line, config file and env var values
        with NamedTemporaryFile(mode="w", delete=True) as default_config_file, \
             NamedTemporaryFile(mode="w", delete=True) as config_file2:
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
            self.assertParseArgsRaises("the following arguments are required: vcf, -g/--my-cfg-file",
                                       args="--genome hg19")
            self.assertParseArgsRaises("Unable to open config file: file.txt. Error: No such file or director", args="-g file.txt")

            # check values after setting args on command line
            config_file2.flush()

            ns = self.parse(args="--genome hg19 -g %s bla.vcf " % config_file2.name)
            self.assertEqual(ns.genome, "hg19")
            self.assertEqual(ns.verbose, False)
            self.assertIsNone(ns.dbsnp)
            self.assertEqual(ns.fmt, "BED")
            self.assertListEqual(ns.vcf, ["bla.vcf"])

            self.assertRegex(self.format_values(),
                'Command Line Args:   --genome hg19 -g [^\\s]+ bla.vcf\n'
                'Defaults:\n'
                '  --format: \\s+ BED\n')

            # check precedence: args > env > config > default using the --format arg
            default_config_file.write("--format MAF")
            default_config_file.flush()
            ns = self.parse(args="--genome hg19 -g %s f.vcf " % config_file2.name)
            self.assertEqual(ns.fmt, "MAF")
            self.assertRegex(self.format_values(),
                'Command Line Args:   --genome hg19 -g [^\\s]+ f.vcf\n'
                'Config File \\([^\\s]+\\):\n'
                '  --format: \\s+ MAF\n')

            config_file2.write("--format VCF")
            config_file2.flush()
            ns = self.parse(args="--genome hg19 -g %s f.vcf " % config_file2.name)
            self.assertEqual(ns.fmt, "VCF")
            self.assertRegex(self.format_values(),
                'Command Line Args:   --genome hg19 -g [^\\s]+ f.vcf\n'
                'Config File \\([^\\s]+\\):\n'
                '  --format: \\s+ VCF\n')

            ns = self.parse(env_vars={"OUTPUT_FORMAT":"R", "DBSNP_PATH":"/a/b.vcf"},
                args="--genome hg19 -g %s f.vcf " % config_file2.name)
            self.assertEqual(ns.fmt, "R")
            self.assertEqual(ns.dbsnp, "/a/b.vcf")
            self.assertRegex(self.format_values(),
                'Command Line Args:   --genome hg19 -g [^\\s]+ f.vcf\n'
                'Environment Variables:\n'
                '  DBSNP_PATH: \\s+ /a/b.vcf\n'
                '  OUTPUT_FORMAT: \\s+ R\n')

            ns = self.parse(env_vars={"OUTPUT_FORMAT":"R", "DBSNP_PATH":"/a/b.vcf",
                                      "ANOTHER_VAR":"something"},
                args="--genome hg19 -g %s --format WIG f.vcf" % config_file2.name)
            self.assertEqual(ns.fmt, "WIG")
            self.assertEqual(ns.dbsnp, "/a/b.vcf")
            self.assertRegex(self.format_values(),
                'Command Line Args:   --genome hg19 -g [^\\s]+ --format WIG f.vcf\n'
                'Environment Variables:\n'
                '  DBSNP_PATH: \\s+ /a/b.vcf\n')

            if not use_groups:
                self.assertRegex(self.format_help(),
                    'usage: .* \\[-h\\] --genome GENOME \\[-v\\] -g MY_CFG_FILE\n?'
                    '\\s+\\[-d DBSNP\\]\\s+\\[-f FRMT\\]\\s+vcf \\[vcf ...\\]\n\n'
                    'positional arguments:\n'
                    '  vcf \\s+ Variant file\\(s\\)\n\n'
                    '%s:\n'
                    '  -h, --help \\s+ show this help message and exit\n'
                    '  --genome GENOME \\s+ Path to genome file\n'
                    '  -v\n'
                    '  -g MY_CFG_FILE, --my-cfg-file MY_CFG_FILE\n'
                    '  -d DBSNP, --dbsnp DBSNP\\s+\\[env var: DBSNP_PATH\\]\n'
                    '  -f FRMT, --format FRMT\\s+\\[env var: OUTPUT_FORMAT\\]\n\n'%(
                       self.OPTIONAL_ARGS_STRING) +
                    7*r'(.+\s*)')
            else:
                self.assertRegex(self.format_help(),
                    'usage: .* \\[-h\\] --genome GENOME \\[-v\\] -g MY_CFG_FILE\n?'
                    '\\s+\\[-d DBSNP\\]\\s+\\[-f FRMT\\]\\s+vcf \\[vcf ...\\]\n\n'
                    'positional arguments:\n'
                    '  vcf \\s+ Variant file\\(s\\)\n\n'
                    '%s:\n'
                    '  -h, --help \\s+ show this help message and exit\n\n'
                    'g1:\n'
                    '  --genome GENOME \\s+ Path to genome file\n'
                    '  -v\n'
                    '  -g MY_CFG_FILE, --my-cfg-file MY_CFG_FILE\n\n'
                    'g2:\n'
                    '  -d DBSNP, --dbsnp DBSNP\\s+\\[env var: DBSNP_PATH\\]\n'
                    '  -f FRMT, --format FRMT\\s+\\[env var: OUTPUT_FORMAT\\]\n\n'%(
                        self.OPTIONAL_ARGS_STRING) +
                    7*r'(.+\s*)')

            self.assertParseArgsRaises("invalid choice: 'ZZZ'",
                args="--genome hg19 -g %s --format ZZZ f.vcf" % config_file2.name)
            self.assertParseArgsRaises("unrecognized arguments: --bla",
                args="--bla --genome hg19 -g %s f.vcf" % config_file2.name)


    def testBasicCase2_WithGroups(self):
        self.testBasicCase2(use_groups=True)

    def testCustomOpenFunction(self):
        expected_output = 'dummy open called'

        def dummy_open(p):
            print(expected_output)
            return open(p)

        self.initParser(config_file_open_func=dummy_open)
        self.add_arg('--config', is_config_file=True)
        self.add_arg('--arg1', default=1, type=int)

        with NamedTemporaryFile(mode='w', delete=False) as config_file:
            config_file.write('arg1 2')
            config_file_path = config_file.name

        with captured_output() as (out, _):
            args = self.parse('--config {}'.format(config_file_path))
            self.assertTrue(hasattr(args, 'arg1'))
            self.assertEqual(args.arg1, 2)
            output = out.getvalue().strip()
            self.assertEqual(output, expected_output)

    def testIgnoreHelpArgs(self):
        self.initParser()
        self.add_arg('--arg1')
        args, _ = self.parse_known('--arg2 --help', ignore_help_args=True)
        self.assertEqual(args.arg1, None)
        self.add_arg('--arg2')
        args, _ = self.parse_known('--arg2 3 --help', ignore_help_args=True)
        self.assertEqual(args.arg2, "3")

        with mock.patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            self.assertRaisesRegex(TypeError, "exit", self.parse_known, '--arg2 3 --help', ignore_help_args=False)

            self.assertTrue(mock_stdout.getvalue().startswith("usage"))

    def testPositionalAndConfigVarLists(self):
        self.initParser()
        self.add_arg("a")
        self.add_arg("-x", "--arg", nargs="+")

        ns = self.parse("positional_value", config_file_contents="""arg = [Shell, someword, anotherword]""")

        self.assertEqual(ns.arg, ['Shell', 'someword', 'anotherword'])
        self.assertEqual(ns.a, "positional_value")

    def testMutuallyExclusiveArgs(self):
        with NamedTemporaryFile(mode="w", delete=True) as config_file:

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
                'Command Line Args:   --genome hg19 -f1 [^\\s]+\n'
                'Environment Variables:\n'
                '  BAM_FORMAT: \\s+ true\n'
                'Defaults:\n'
                '  --format: \\s+ BED\n')

        self.assertRegex(self.format_help(),
            r'usage: .* \[-h\] --genome GENOME \[-v\]\s+ \(-f1 TYPE1_CFG_FILE \|'
            ' \\s*-f2 TYPE2_CFG_FILE\\)\\s+\\(-f FRMT \\| -b\\)\n\n'
            '%s:\n'
            '  -h, --help            show this help message and exit\n'
            '  -f1 TYPE1_CFG_FILE, --type1-cfg-file TYPE1_CFG_FILE\n'
            '  -f2 TYPE2_CFG_FILE, --type2-cfg-file TYPE2_CFG_FILE\n'
            '  -f FRMT, --format FRMT\\s+\\[env var: OUTPUT_FORMAT\\]\n'
            '  -b, --bam\\s+\\[env var: BAM_FORMAT\\]\n\n'
            'group1:\n'
            '  --genome GENOME       Path to genome file\n'
            '  -v\n\n'%(
                self.OPTIONAL_ARGS_STRING) +
            5*r'(.+\s*)')

    def testSubParsers(self):
        with NamedTemporaryFile(mode="w", delete=True) as config_file1, \
             NamedTemporaryFile(mode="w", delete=True) as config_file2:
            config_file1.write("--i = B")
            config_file1.flush()

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
        self.add_arg('--c')
        self.add_arg('--b', action="store_true")
        self.add_arg('--a', action="append", type=int)
        self.add_arg('--m', action="append", nargs=3, metavar=("<a1>", "<a2>", "<a3>"),)

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
        ---
        z z 1
        ---
        m = [[1, 2, 3], [4, 5, 6]]
        """)

        self.assertEqual(ns.x, 1)
        self.assertEqual(ns.y, 12.1)
        self.assertEqual(ns.z, 'z 1')
        self.assertIsNone(ns.c)
        self.assertEqual(ns.b, True)
        self.assertEqual(ns.a, [33])
        self.assertRegex(self.format_values(),
            'Command Line Args: \\s+ -x 1\n'
            'Config File \\(method arg\\):\n'
            '  y: \\s+ 12.1\n'
            '  b: \\s+ True\n'
            '  a: \\s+ 33\n'
            '  z: \\s+ z 1\n')
        self.assertEqual(ns.m, [['1', '2', '3'], ['4', '5', '6']])

        # -x is not a long arg so can't be set via config file
        self.assertParseArgsRaises("the following arguments are required: -x, --y",
                                   args="",
                                   config_file_contents="-x 3")
        self.assertParseArgsRaises("invalid float value: 'abc'",
                                   args="-x 5",
                                   config_file_contents="y: abc")
        self.assertParseArgsRaises("the following arguments are required: --y",
                                   args="-x 5",
                                   config_file_contents="z: 1")

        # test unknown config file args
        self.assertParseArgsRaises("bla",
            args="-x 1 --y 2.3",
            config_file_contents="bla=3")

        ns, args = self.parse_known("-x 10 --y 3.8",
                        config_file_contents="bla=3",
                        env_vars={"bla": "2"})
        self.assertListEqual(args, ["--bla=3"])

        self.initParser(ignore_unknown_config_file_keys=False)
        ns, args = self.parse_known(args="-x 1", config_file_contents="bla=3",
            env_vars={"bla": "2"})
        self.assertEqual(set(args), {"--bla=3", "-x", "1"})


    def testQuotedArgumentValues(self):
        self.initParser()
        self.add_arg("-a")
        self.add_arg("--b")
        self.add_arg("-c")
        self.add_arg("--d")
        self.add_arg("-e")
        self.add_arg("-q")
        self.add_arg("--quotes")

        # sys.argv equivalent of -a="1"  --b "1" -c= --d "" -e=: -q "\"'" --quotes "\"'"
        ns = self.parse(args=['-a=1', '--b', '1', '-c=', '--d', '', '-e=:',
                '-q', '"\'', '--quotes', '"\''],
                env_vars={}, config_file_contents="")

        self.assertEqual(ns.a, "1")
        self.assertEqual(ns.b, "1")
        self.assertEqual(ns.c, "")
        self.assertEqual(ns.d, "")
        self.assertEqual(ns.e, ":")
        self.assertEqual(ns.q, '"\'')
        self.assertEqual(ns.quotes, '"\'')

    def testQuotedConfigFileValues(self):
        self.initParser()
        self.add_arg("--a")
        self.add_arg("--b")
        self.add_arg("--c")

        ns = self.parse(args="", env_vars={}, config_file_contents="""
        a="1"
        b=:
        c=
        """)

        self.assertEqual(ns.a, "1")
        self.assertEqual(ns.b, ":")
        self.assertEqual(ns.c, "")

    def testBooleanValuesCanBeExpressedAsNumbers(self):
        self.initParser()
        store_true_env_var_name = "STORE_TRUE"
        self.add_arg("--boolean_store_true", action="store_true", env_var=store_true_env_var_name)

        result_namespace = self.parse("", config_file_contents="""boolean_store_true = 1""")
        self.assertTrue(result_namespace.boolean_store_true)

        result_namespace = self.parse("", config_file_contents="""boolean_store_true = 0""")
        self.assertFalse(result_namespace.boolean_store_true)

        result_namespace = self.parse("", env_vars={store_true_env_var_name: "1"})
        self.assertTrue(result_namespace.boolean_store_true)

        result_namespace = self.parse("", env_vars={store_true_env_var_name: "0"})
        self.assertFalse(result_namespace.boolean_store_true)

        self.initParser()
        store_false_env_var_name = "STORE_FALSE"
        self.add_arg("--boolean_store_false", action="store_false", env_var=store_false_env_var_name)

        result_namespace = self.parse("", config_file_contents="""boolean_store_false = 1""")
        self.assertFalse(result_namespace.boolean_store_false)

        result_namespace = self.parse("", config_file_contents="""boolean_store_false = 0""")
        self.assertTrue(result_namespace.boolean_store_false)

        result_namespace = self.parse("", env_vars={store_false_env_var_name: "1"})
        self.assertFalse(result_namespace.boolean_store_false)

        result_namespace = self.parse("", env_vars={store_false_env_var_name: "0"})
        self.assertTrue(result_namespace.boolean_store_false)

    def testConfigOrEnvValueErrors(self):
        # error should occur when a flag arg is set to something other than "true" or "false"
        self.initParser()
        self.add_arg("--height", env_var = "HEIGHT", required=True)
        self.add_arg("--do-it", dest="x", env_var = "FLAG1", action="store_true")
        self.add_arg("--dont-do-it", dest="y", env_var = "FLAG2", action="store_false")
        ns = self.parse("", env_vars = {"HEIGHT": "tall", "FLAG1": "yes"})
        self.assertEqual(ns.height, "tall")
        self.assertEqual(ns.x, True)
        ns = self.parse("", env_vars = {"HEIGHT": "tall", "FLAG2": "yes"})
        self.assertEqual(ns.y, False)
        ns = self.parse("", env_vars = {"HEIGHT": "tall", "FLAG2": "no"})
        self.assertEqual(ns.y, True)

        # error should occur when flag arg is given a value
        self.initParser()
        self.add_arg("-v", "--verbose", env_var="VERBOSE", action="store_true")
        self.assertParseArgsRaises("Unexpected value for VERBOSE: 'bla'. "
                                   "Expecting 'true', 'false', 'yes', 'no', 'on', 'off', '1', '0'",
            args="",
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
        ns = self.parse("", env_vars = {"HEIGHT": "true", "VERBOSE": "false"})
        self.assertEqual(ns.verbose, False)
        ns = self.parse("", config_file_contents="--verbose",
                        env_vars = {"HEIGHT": "true"})
        self.assertEqual(ns.verbose, True)

        # error should occur is non-append arg is given a list value
        self.initParser()
        self.add_arg("-f", "--file", env_var="FILES", action="append", type=int)
        ns = self.parse("", env_vars = {"file": "[1,2,3]", "VERBOSE": "true"})
        self.assertIsNone(ns.file)

    def testValuesStartingWithDash(self):
        self.initParser()
        self.add_arg("--arg0")
        self.add_arg("--arg1", env_var="ARG1")
        self.add_arg("--arg2")
        self.add_arg("--arg3", action='append')
        self.add_arg("--arg4", action='append', env_var="ARG4")
        self.add_arg("--arg5", action='append')
        self.add_arg("--arg6")

        ns = self.parse(
            "--arg0=-foo --arg3=-foo --arg3=-bar --arg6=-test-more-dashes",
            config_file_contents="arg2: -foo\narg5: [-foo, -bar]",
            env_vars={"ARG1": "-foo", "ARG4": "[-foo, -bar]"}
        )
        self.assertEqual(ns.arg0, "-foo")
        self.assertEqual(ns.arg1, "-foo")
        self.assertEqual(ns.arg2, "-foo")
        self.assertEqual(ns.arg3, ["-foo", "-bar"])
        self.assertEqual(ns.arg4, ["-foo", "-bar"])
        self.assertEqual(ns.arg5, ["-foo", "-bar"])
        self.assertEqual(ns.arg6, "-test-more-dashes")

    def testPriorityKnown(self):
        self.initParser()
        self.add_arg("--arg", env_var="ARG")

        ns = self.parse(
            "--arg command_line_val",
            config_file_contents="arg: config_val",
            env_vars={"ARG": "env_val"}
            )
        self.assertEqual(ns.arg, "command_line_val")

        ns = self.parse(
            "--arg=command_line_val",
            config_file_contents="arg: config_val",
            env_vars={"ARG": "env_val"}
            )
        self.assertEqual(ns.arg, "command_line_val")

        ns = self.parse(
            "",
            config_file_contents="arg: config_val",
            env_vars={"ARG": "env_val"}
            )
        self.assertEqual(ns.arg, "env_val")

    def testPriorityUnknown(self):
        self.initParser()

        ns, args = self.parse_known(
            "--arg command_line_val",
            config_file_contents="arg: config_val",
            env_vars={"arg": "env_val"}
            )
        self.assertListEqual(args, ["--arg", "command_line_val"])

        ns, args = self.parse_known(
            "--arg=command_line_val",
            config_file_contents="arg: config_val",
            )
        self.assertListEqual(args, ["--arg=command_line_val"])

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
        self.assertIsNone(ns.arg0)
        self.assertIsNone(ns.arg1)
        self.assertEqual(ns.arg2, 22)
        self.assertEqual(ns.arg4, "arg4_value")
        self.assertEqual(ns.arg4_more, "magic")

    def testEnvVarLists(self):
        self.initParser()
        self.add_arg("-x", "--arg2", env_var="TEST2")
        self.add_arg("-y", "--arg3", env_var="TEST3", type=int)
        self.add_arg("-z", "--arg4", env_var="TEST4", nargs="+")
        self.add_arg("-u", "--arg5", env_var="TEST5", nargs="+", type=int)
        self.add_arg("--arg6", env_var="TEST6")
        self.add_arg("--arg7", env_var="TEST7", action="append")
        ns = self.parse("", env_vars={"TEST2": "22",
                                      "TEST3": "22",
                                      "TEST4": "[Shell, someword, anotherword]",
                                      "TEST5": "[22, 99, 33]",
                                      "TEST6": "[value6.1, value6.2, value6.3]",
                                      "TEST7": "[value7.1, value7.2, value7.3]",
                                      })
        self.assertEqual(ns.arg2, "22")
        self.assertEqual(ns.arg3, 22)
        self.assertEqual(ns.arg4, ['Shell', 'someword', 'anotherword'])
        self.assertEqual(ns.arg5, [22, 99, 33])
        self.assertEqual(ns.arg6, "[value6.1, value6.2, value6.3]")
        self.assertEqual(ns.arg7, ["value7.1", "value7.2", "value7.3"])

    def testPositionalAndEnvVarLists(self):
        self.initParser()
        self.add_arg("a")
        self.add_arg("-x", "--arg", env_var="TEST", nargs="+")

        ns = self.parse("positional_value", env_vars={"TEST": "[Shell, someword, anotherword]"})

        self.assertEqual(ns.arg, ['Shell', 'someword', 'anotherword'])
        self.assertEqual(ns.a, "positional_value")

    def testCounterCommandLine(self):
        self.initParser()
        self.add_arg("--verbose", "-v", action="count", default=0)

        ns = self.parse(args="-v -v -v", env_vars={})
        self.assertEqual(ns.verbose, 3)

        ns = self.parse(args="-vvv", env_vars={})
        self.assertEqual(ns.verbose, 3)

    def testCounterConfigFile(self):
        self.initParser()
        self.add_arg("--verbose", "-v", action="count", default=0)

        ns = self.parse(args="", env_vars={}, config_file_contents="""
        verbose""")
        self.assertEqual(ns.verbose, 1)

        ns = self.parse(args="", env_vars={}, config_file_contents="""
        verbose=3""")
        self.assertEqual(ns.verbose, 3)

