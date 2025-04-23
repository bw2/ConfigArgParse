""" Tests in this file all relate to specific issues listed at
    https://github.com/bw2/ConfigArgParse/issues/275

    No fixes without a test case!
"""
import os, sys, re
from textwrap import dedent
import unittest
from unittest.mock import patch, mock_open
from io import StringIO

import configargparse

from tests.test_base import TestCase, yaml

class TestIssues(TestCase):

    def test_issue_275_a(self):
        """With a subparser the env vars are tacked on the end,
           when any config used has nargs > 1.
        """
        p = configargparse.ArgParser(prog="TEST")
        p.add_argument('--foo', '-f', nargs='+', required=False)
        p.add_argument('--moo', '-m', action="store_true")
        sp = p.add_subparsers(dest='sp_name')
        spa = sp.add_parser('a')
        spa.add_argument('abar', type=int)
        spa = sp.add_parser('b')
        spa.add_argument('bbar', type=int)

        # This one always worked
        options = p.parse_args(args = ['-f', 'x', 'y', '-m',
                                       'a', '22'])
        self.assertEqual(vars(options), dict( foo = ['x', 'y'],
                                              moo = True,
                                              sp_name = 'a',
                                              abar = 22 ))

        # But this was broken and now works
        cfile = dedent('''\
            foo = ["j","k"]
            ''')

        options = p.parse_args(args = ['-m', 'a', '22'],
                               config_file_contents = cfile )
        self.assertEqual(vars(options), dict( foo = ['j', 'k'],
                                              moo = True,
                                              sp_name = 'a',
                                              abar = 22 ))

        # This one was broken by my fix but I re-fixed it.
        cfile = dedent('''\
            moo = true
            ''')
        options = p.parse_args(args = ['a', '22'],
                               config_file_contents = cfile )
        self.assertEqual(vars(options), dict( foo = None,
                                              moo = True,
                                              sp_name = 'a',
                                              abar = 22 ))


    @unittest.expectedFailure
    def test_issue_275_b(self):
        # This and related cases remain broken, and I don't know how to fix it :-(
        # Basically, don't use variable-length args with subparsers.

        self.add_arg('--foo', '-f', nargs='+', required=False)
        self.add_arg('--moo', '-m', action="store_true")
        sp = self.parser.add_subparsers(dest='sp_name')
        spa = sp.add_parser('a')
        spa.add_argument('abar', type=int)
        spa = sp.add_parser('b')
        spa.add_argument('bbar', type=int)

        # In this case the argument munger incorrectly produces:
        #  -f j k a 22
        # And --foo eats all four of the arguments
        # Or without the exception in insert_args() we could have it be:
        #  a 22 -f j k
        # But I can't see any way to arrange this command line to get the desired
        # result, never mind how to do it programatically.
        cfile = dedent('''\
            foo = ["j","k"]
            ''')

        options = self.parse(args = ['a', '22'],
                             config_file_contents = cfile )
        self.assertEqual(vars(options), dict( foo = ['j', 'k'],
                                              moo = False,
                                              sp_name = 'a',
                                              abar = 22 ))


    def test_issue_275_c(self):
        """Including -- to end processing of named args did not work as
           advertised.
        """
        p = configargparse.ArgParser()
        p.add('--foo', '-f', nargs='?', env_var='FOO', required=False)
        p.add('bar', nargs='*')

        options = p.parse_args( args = ['--', 'arg1', 'arg2'],
                                env_vars = dict(FOO = 'environ_value')  )

        self.assertEqual(vars(options), dict( foo = 'environ_value',
                                              bar = ['arg1', 'arg2'] ))

    # This currently seems to run into a bug within argparse itself,
    # or else I don't understand the expected behaviour
    @unittest.expectedFailure
    def test_issue_275_c_custom_char(self):
        """Including ++ should also work if prefix_chars is set to '+'
        """
        p = configargparse.ArgParser(prefix_chars="+")
        p.add('++foo', '+f', nargs='?', env_var='FOO', required=False)
        p.add('bar', nargs='*')

        options = p.parse_args( args = ['++', 'arg1', 'arg2'],
                                env_vars = dict(FOO = 'environ_value')  )

        self.assertEqual(vars(options), dict( foo = 'environ_value',
                                              bar = ['arg1', 'arg2'] ))

    def test_issue_pr_293(self):
        """Handle pathlib Path used as default_config_files"""
        from pathlib import Path

        self.initParser(default_config_files=[Path("~/.my_settings.ini")])
        self.add_arg("foo")
        self.add_arg("--flag", help="Flag that can be set in the config file")

        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            self.assertRaisesRegex(TypeError, "exit", self.parse_known, '--help')

            self.assertRegex(mock_stdout.getvalue(), r"usage:")

    def test_issue_265(self):

        # This can't be tested by using config_file_contents, so use a tmpFile
        with self.tmpFile() as tf:
            conf_filename = tf.name
            print("option=foo", file=tf)

        p = configargparse.ArgumentParser()
        p.add_argument("first_arg")
        p.add_argument("config", is_config_file=True)
        p.add_argument("--option")

        ns, rest = p.parse_known_args(["first", conf_filename])

        self.assertEqual(rest, [])
        self.assertEqual(vars(ns), dict( first_arg = "first",
                                         config = conf_filename,
                                         option = "foo" ))

    def test_issue_292(self):
        # Now we should be able to get at the command line by inspecting
        # parser.last_parsed_args
        with self.tmpFile() as tf:
            conf_filename = tf.name
            print("option=[foo]", file=tf)

        p = configargparse.ArgumentParser()
        p.add_argument("first_arg")
        p.add_argument("--option", nargs="*")
        p.add_argument("--config", is_config_file=True)
        p.add_argument("--another", env_var="ANOTHER")

        ns, rest = p.parse_known_args( ["first", "--config", conf_filename],
                                       env_vars=dict(ANOTHER="aaa") )

        self.assertEqual( p.last_parsed_args, ['first', '--option', 'foo', '--another=aaa',
                                               '--config', conf_filename] )

    @unittest.skipUnless(yaml, "PyYAML not installed")
    def test_issue_296(self):
        """User complains that empty YAML config items and empty env vars act differently,
           but that's not quite right - there is no way for an env var to distinguish
           between None and '' and <absent>.

           I do think there is a case that empty env vars and config lines should be considered
           as absent for list values, as it's very rare to want an actual empty string
           and if you really need one you can make one explicitly - [""]. However this would
           require making a special case for these.

           This test confirms the status quo, which is consistent if slightly counterintuitive.
        """
        self.initParser(config_file_parser_class=configargparse.YAMLConfigFileParser)

        # Tests for YAML config parser. We want to test all these possibilities:
        # <absent> (with and without an env var)
        # None     (for: YAML) (for cmdline this could be --no-arg but that's only for booleans)
        # ""       (for: YAML, cmdline, env)
        # []       (for: YAML, cmdline, env)
        # [""]     (for: YAML, env) (for cmdline this is the same as "")

        self.add_arg("--absent_no_env", nargs="*", env_var=False)
        self.add_arg("--absent_with_env",nargs="*", env_var="UNSET")

        self.add_arg("--null_in_yaml", nargs="*")
        self.add_arg("--blank_in_yaml", nargs="*") # Same as None/null

        self.add_arg("--empty_str_in_yaml", nargs="*")
        self.add_arg("--empty_str_cmdline", nargs="*")
        self.add_arg("--empty_str_in_env", nargs="*", env_var="EMPTY_STR")

        self.add_arg("--empty_list_in_yaml", nargs="*")
        self.add_arg("--empty_list_cmdline", nargs="*")
        self.add_arg("--empty_list_in_env", nargs="*", env_var="EMPTY_LIST")

        self.add_arg("--empty_str_in_list_in_yaml", nargs="*")
        self.add_arg("--empty_str_in_list_in_env", nargs="*", env_var="EMPTY_STR_LIST")

        env = dict( EMPTY_STR = '',
                    EMPTY_LIST = '[]',
                    EMPTY_STR_LIST = '[""]')
        config_lines = [ 'null_in_yaml: null',
                         'blank_in_yaml:',
                         'empty_str_in_yaml: ""',
                         'empty_list_in_yaml: []',
                         'empty_str_in_list_in_yaml: [""]' ]
        ns = self.parse(["--empty_list_cmdline",
                         "--empty_str_cmdline", ""],
                        config_file_contents=("\n".join(config_lines)),
                        env_vars=env)

        self.assertEqual(vars(ns), { 'absent_no_env': None,
                                     'absent_with_env': None,
                                     'null_in_yaml': None,
                                     'blank_in_yaml': None,
                                     'empty_str_in_yaml': [""],
                                     'empty_str_cmdline': [""],
                                     'empty_str_in_env': [""],
                                     'empty_list_in_yaml': [],
                                     'empty_list_cmdline': [],
                                     'empty_list_in_env': [],
                                     'empty_str_in_list_in_yaml': [""],
                                     'empty_str_in_list_in_env': [""] } )

        # What about with a regular config file?
        self.initParser()

        self.add_arg("--absent", nargs="*")
        self.add_arg("--blank_in_conf", nargs="*")
        self.add_arg("--null_in_conf", nargs="*")
        self.add_arg("--empty_str_in_conf", nargs="*")
        self.add_arg("--empty_list_in_conf", nargs="*")
        self.add_arg("--empty_str_in_list_in_conf", nargs="*")

        config_lines = [ 'blank_in_conf:',
                         'null_in_conf: null',
                         'empty_str_in_conf: ""',
                         'empty_list_in_conf: []',
                         'empty_str_in_list_in_conf: [""]' ]
        ns = self.parse([], config_file_contents=("\n".join(config_lines)),
                            env_vars=dict())

        self.assertEqual(vars(ns), { 'absent': None,
                                     'blank_in_conf': [""], # same as empty string
                                     'null_in_conf': ["null"], # null is not a special word!
                                     'empty_str_in_conf': [""],
                                     'empty_list_in_conf': [],
                                     'empty_str_in_list_in_conf': [""] })

    def test_issue_287(self):
        """
        Very similar to 296 above. Empty list in config file.
        """
        self.initParser()
        self.add_arg("--absent", nargs="*")
        self.add_arg("--empty_list_in_conf", nargs="*")

        config_lines = [ 'empty_list_in_conf = []' ]
        ns = self.parse([], config_file_contents=("\n".join(config_lines)),
                            env_vars=dict())

        self.assertEqual(vars(ns), { 'absent': None,
                                     'empty_list_in_conf': [] })

    @patch('configargparse.glob')
    def test_issue_142(self, patched_glob):
        """
        Adding -h as a short option is broken?
        """
        # This only manifested when the config is set via "default_config_files" or
        # a config_files option, not when fed in via config_file_contents, so a mock
        # open is used here to simulate reading the config, and we also need a mock glob
        # to pretend the file exists.
        config_lines = "host = host_from_config\nextra = extra_from_config\nport = 8080\n"
        mockopener = mock_open(read_data = config_lines)
        patched_glob.glob.return_value = ['test.conf']

        self.initParser(add_help=False, default_config_files=['test.conf'],
                                        config_file_open_func=mockopener)
        self.add_arg('--help', action="help", help="Show help message")
        self.add_arg('-h', '--host', default='localhost', help="hostname1")
        self.add_arg('-e', '--extra', default='localhost', help="hostname2")
        self.add_arg('-p', '--port', type=int, default=80, help="port")

        # This works fine
        res1 = self.parse([])
        self.assertEqual(vars(res1), dict(host = "host_from_config",
                                          extra = "extra_from_config",
                                          port = 8080))

        # This too
        res2 = self.parse(["--extra", "extra_from_cmdline"])
        self.assertEqual(vars(res2), dict(host = "host_from_config",
                                          extra = "extra_from_cmdline",
                                          port = 8080))

        # And this, setting config_file_contents directly
        res3 = self.parse(["-h", "host_from_cmdline"], config_file_contents=config_lines)
        self.assertEqual(vars(res3), dict(host = "host_from_cmdline",
                                          extra = "extra_from_config",
                                          port = 8080))

        # Here was the problem. Using '-h' on the command line puts the port
        # back to 8080 (and ignores the config file in general)
        res4 = self.parse(["-h", "host_from_cmdline"])
        self.assertEqual(vars(res4), dict(host = "host_from_cmdline",
                                          extra = "extra_from_config",
                                          port = 8080))

        # But using the long form works fine
        res5 = self.parse(["--host", "host_from_cmdline"])
        self.assertEqual(vars(res5), dict(host = "host_from_cmdline",
                                          extra = "extra_from_config",
                                          port = 8080))

