import os
from pdb import set_trace
import sys
import unittest
from configargparse import ArgumentParser


def get_abs_path(file):
    return os.path.abspath(os.path.join(os.path.dirname(__file__), file))

class TestMulticonf(unittest.TestCase):
    def setUp(self):
        self.parser = ArgumentParser()
        self.parser.add_argument('-config', type=get_abs_path, nargs='*', is_config_file=True)
        self.parser.add_argument('-config_2', type=get_abs_path, nargs='*', is_config_file=True)
        self.parser.add_argument('--arg_0', type=str)
        self.parser.add_argument('--arg_1', type=str)
        self.parser.add_argument('--arg_2', type=str)

        self.ref = self.parse_command_line('-config test_conf_4.ini')

    def parse_command_line(self, cmd):
        sys.argv = [sys.argv[0]] + cmd.split(' ')
        args = self.parser.parse_args()
        return args

    def test_multiconf(self):
        args = self.parse_command_line('-config test_conf_3.ini test_conf_5.ini')
        self.assertEqual(self.ref, args)

    def test_multiconf_2(self):
        args = self.parse_command_line('-config test_conf_3.ini -config_2 test_conf_5.ini')
        self.assertEqual(self.ref, args)

    def test_conf_inheritance(self):
        args = self.parse_command_line('-config test_conf_0.ini')
        self.assertEqual(self.ref, args)

if __name__ == '__main__':
    unittest.main()