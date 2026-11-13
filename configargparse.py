"""
A drop-in replacement for `argparse` that allows options to also be set via config files and/or environment variables.

:see: `configargparse.ArgumentParser`, `configargparse.add_argument`
"""

import argparse
import ast
import configparser
import copy
import csv
import enum
import functools
import json
import glob
import os
import re
import sys
import types
from collections import OrderedDict
import textwrap
import warnings
from io import StringIO

ACTION_TYPES_THAT_DONT_NEED_A_VALUE = [
    argparse._StoreTrueAction,
    argparse._StoreFalseAction,
    argparse._CountAction,
    argparse._StoreConstAction,
    argparse._AppendConstAction,
]

if sys.version_info >= (3, 9):
    ACTION_TYPES_THAT_DONT_NEED_A_VALUE.append(argparse.BooleanOptionalAction)
    is_boolean_optional_action = lambda action: isinstance(
        action, argparse.BooleanOptionalAction
    )
else:
    is_boolean_optional_action = lambda action: False

ACTION_TYPES_THAT_DONT_NEED_A_VALUE = tuple(ACTION_TYPES_THAT_DONT_NEED_A_VALUE)


# global ArgumentParser instances
_parsers = {}


def init_argument_parser(name=None, **kwargs):
    """Creates a global ArgumentParser instance with the given name,
    passing any args other than "name" to the ArgumentParser constructor.
    This instance can then be retrieved using get_argument_parser(..)
    """

    if name is None:
        name = "default"

    if name in _parsers:
        raise ValueError(
            (
                "kwargs besides 'name' can only be passed in the"
                " first time. '%s' ArgumentParser already exists: %s"
            )
            % (name, _parsers[name])
        )

    kwargs.setdefault("formatter_class", argparse.ArgumentDefaultsHelpFormatter)
    kwargs.setdefault("conflict_handler", "resolve")
    _parsers[name] = ArgumentParser(**kwargs)


def get_argument_parser(name=None, **kwargs):
    """Returns the global ArgumentParser instance with the given name. The 1st
    time this function is called, a new ArgumentParser instance will be created
    for the given name, and any args other than "name" will be passed on to the
    ArgumentParser constructor.
    """
    if name is None:
        name = "default"

    if len(kwargs) > 0 or name not in _parsers:
        init_argument_parser(name, **kwargs)

    return _parsers[name]


class ArgumentDefaultsRawHelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawTextHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """HelpFormatter that adds default values AND doesn't do line-wrapping"""

    pass


class ConfigFileParser(object):
    """This abstract class can be extended to add support for new config file
    formats"""

    def get_syntax_description(self):
        """Returns a string describing the config file syntax."""
        raise NotImplementedError("get_syntax_description(..) not implemented")

    def parse(self, stream):
        """Parses the keys and values from a config file.

        NOTE: For keys that were specified to configargparse as
        action="store_true" or "store_false", the config file value must be
        one of: "yes", "no", "on", "off", "true", "false". Otherwise an error will be raised.

        Args:
            stream (io.IOBase): A config file input stream (such as an open file object).

        Returns:
            OrderedDict: Items where the keys are strings and the
            values are either strings or lists (eg. to support config file
            formats like YAML which allow lists).
        """
        raise NotImplementedError("parse(..) not implemented")

    def serialize(self, items):
        """Does the inverse of config parsing by taking parsed values and
        converting them back to a string representing config file contents.

        Args:
            items: an OrderedDict of items to be converted to the config file
                format. Keys are strings, and values are a str, int, float or
                bool, or a list whose elements are any of these or lists of
                them.

        Returns:
            Contents of config file as a string
        """
        raise NotImplementedError("serialize(..) not implemented")


class _WriteOutConfigFileActionMixin(object):
    """Remembers the path this arg was given, on the parser that parsed it.

    The path can't just be read back out of the parsed namespace once parsing
    finishes: another arg sharing this one's dest could have put a value there,
    from a config file or anywhere else, and writing a config file overwrites
    the path it is given. Recording it as the arg is parsed means only this
    arg decides where that write goes.

    add_argument() puts this over whichever store action the program asked for,
    so an arg that does something of its own with the path still records it.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        super(_WriteOutConfigFileActionMixin, self).__call__(
            parser, namespace, values, option_string
        )
        # take the value back off the namespace: the action just put it there,
        # and a store action of the program's own may have changed it on the
        # way (expanding a '~', say). Keyed by the action so that repeating the
        # arg replaces the path rather than adding a second one.
        # a parser that isn't a ConfigArgParse one has nowhere to record it,
        # and doesn't write config files either, so there is nothing to record
        if hasattr(parser, "_write_out_config_file_paths"):
            parser._write_out_config_file_paths[id(self)] = getattr(
                namespace, self.dest, values
            )


class ConfigFileParserException(Exception):
    """Raised when config file parsing failed."""


class ConfigFileParserMissingDependency(ConfigFileParserException):
    """Raised when an optional dependency is missing."""


class DefaultConfigFileParser(ConfigFileParser):
    """
    Based on a simplified subset of INI and YAML formats. Here is the
    supported syntax

    .. code::

        # this is a comment
        ; this is also a comment (.ini style)
        ---            # lines that start with --- are ignored (yaml style)
        -------------------
        [section]      # .ini-style section names are treated as comments

        # how to specify a key-value pair (all of these are equivalent):
        name value     # key is case sensitive: "Name" isn't "name"
        name = value   # (.ini style)  (white space is ignored, so name = value same as name=value)
        name: value    # (yaml style)
        --name value   # (argparse style)

        # how to set a flag arg (eg. arg which has action="store_true")
        --name
        name
        name = True    # "True" and "true" are the same

        # how to specify a list arg (eg. arg which has action="append")
        fruit = [apple, orange, lemon]
        indexes = [1, 12, 35 , 40]

    """

    def get_syntax_description(self):
        msg = (
            "Config file syntax allows: key=value, flag=true, stuff=[a,b,c] "
            "(for details, see syntax at https://goo.gl/R74nmi)."
        )
        return msg

    def parse(self, stream):
        # see ConfigFileParser.parse docstring

        items = OrderedDict()
        for i, line in enumerate(stream, 1):
            line = line.strip()
            if not line or line[0] in ["#", ";", "["] or line.startswith("---"):
                continue

            # whitespace only separates the key from a value when no comment
            # follows it, so a flag with a comment after it (eg. "verbose  #
            # be loud") stays a flag
            match = re.match(
                r"^(?P<key>[^:=;#\s]+)\s*"
                r'(?:(?P<equal>[:=]|\s(?!\s*[;#]))\s*([\'"]?)(?P<value>.+?)?\3)?'
                r"\s*(?:\s[;#]\s*(?P<comment>.*?)\s*)?$",
                line,
            )
            if match:
                key = match.group("key")
                equal = match.group("equal")
                value = match.group("value")
                if value is None and equal is not None and equal != " ":
                    value = ""
                elif value is None:
                    value = "true"
                items[key] = _parse_bracketed_list_value(value)
            else:
                raise ConfigFileParserException(
                    "Unexpected line {} in {}: {}".format(
                        i, getattr(stream, "name", "stream"), line
                    )
                )
        return items

    def serialize(self, items):
        # see ConfigFileParser.serialize docstring
        r = StringIO()
        for key, value in items.items():
            if isinstance(value, list):
                # write lists as JSON, which parse() reads back unchanged: it
                # quotes each string, so a comma in one doesn't split it in two,
                # and it escapes newlines. ensure_ascii=False writes non-ASCII
                # text as is, the way other values are written, so the file stays
                # readable. default=str writes an element that JSON can't
                # represent, such as a Path, as its str(). parse() reads
                # whitespace followed by '#' or ';' as the start of a comment,
                # even inside a JSON string, so those two chars are written as
                # JSON escape codes there, which json.loads reads back as is.
                value = re.sub(
                    r"(?<=\s)[#;]",
                    lambda match: "\\u%04x" % ord(match.group()),
                    json.dumps(value, default=str, ensure_ascii=False),
                )
            else:
                # render it the way this has always rendered it: str() differs
                # for an Enum on Python before 3.11
                value = "{}".format(value)
            # this format puts one key on each line, so a newline in a key or a
            # value would silently turn into extra keys when the file is read
            # back in (see parse() above). Only a list can be quoted, so say so
            # instead of writing a file that means something else.
            for field in (str(key), value):
                if "\n" in field or "\r" in field:
                    raise ValueError(
                        "Config file values can't contain newlines, so {} = {} "
                        "can't be written out in this config file format. Use a "
                        "config_file_parser_class that supports multi-line "
                        "values, such as YAMLConfigFileParser.".format(key, value)
                    )
            if not isinstance(items[key], list):
                value = self._quote_value_if_needed_to_read_back_unchanged(key, value)
            r.write("{} = {}\n".format(key, value))
        return r.getvalue()

    def _quote_value_if_needed_to_read_back_unchanged(self, key, value):
        """Write a value the way parse() reads back as that same value.

        parse() strips the whitespace around a value and one pair of quotes
        around it, and reads whitespace followed by '#' or ';' as the start of a
        comment, unless the value is quoted. So a value is written as is when
        that reads back unchanged, and otherwise in double or single quotes.

        Args:
            key: the config file key the value is written under.
            value: the value as a string.

        Returns:
            str: the value as it should be written after "key = "

        Raises:
            ValueError: if no form of the value reads back unchanged, eg. one
                that looks like a [list] or that holds both kinds of quotes.
        """
        for written_value in (value, '"%s"' % value, "'%s'" % value):
            try:
                read_back = self.parse(
                    StringIO("{} = {}\n".format(key, written_value))
                ).get(str(key))
            except ConfigFileParserException:
                continue
            if read_back == value:
                return written_value
        raise ValueError(
            "{} = {!r} can't be written out in this config file format so that it "
            "reads back unchanged. Use another config_file_parser_class, such as "
            "YAMLConfigFileParser.".format(key, value)
        )


def _keep_literal_or_convert_to_str(value):
    """Return a list element as it should be written into a list repr.

    Args:
        value: an element of a list value.

    Returns:
        object: the value unchanged if ``ast.literal_eval`` of its repr gives
        it back, otherwise ``str(value)`` (e.g. for a Path, or inf).
    """
    try:
        if ast.literal_eval(repr(value)) == value:
            return value
    except (ValueError, SyntaxError):
        pass
    return str(value)


def _list_literal_that_reads_back_as_the_list(value):
    """Write a list as a Python list literal that ast.literal_eval reads back.

    Args:
        value: a list value, whose elements may be lists themselves.

    Returns:
        str: the literal, with each element (and each element of an inner
        list) that a literal can't hold, such as a Path or inf, as its str()
    """
    return str(
        [
            (
                [_keep_literal_or_convert_to_str(e) for e in elem]
                if isinstance(elem, list)
                else _keep_literal_or_convert_to_str(elem)
            )
            for elem in value
        ]
    )


def _add_config_values_as_str_except_lists(values_by_key, result):
    """Add a config file's values to result the way argparse will read them.

    Anything that is not a list is converted to a string, since config values
    are passed to argparse as command line args, and argparse converts them
    back to a boolean, int or whatever the arg's type is. Keys set to None are
    left out.

    Args:
        values_by_key: the keys and values a YAML or TOML parser read.
        result: the OrderedDict to add them to.
    """
    for key, value in values_by_key.items():
        if isinstance(value, list):
            result[key] = value
        elif value is not None:
            result[key] = str(value)


def _configparser_value_is_evaluated_as_list(value):
    """Check whether ConfigparserConfigFileParser.parse() reads a value as a list.

    Args:
        value: the value as written, with newlines replaced by spaces.

    Returns:
        bool: whether it has square brackets that aren't inside a {...} dict
    """
    return "[" in value and "]" in value and "{" not in value.split("[")[0]


def _evaluate_list_literal(text):
    """Evaluate a config file value written as a Python list literal.

    Args:
        text: the value, eg. "['a', 'b']".

    Returns:
        the evaluated value

    Raises:
        ConfigFileParserException: if the text isn't a valid literal.
    """
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise ConfigFileParserException(
            "Error evaluating list: "
            + str(e)
            + ". Put quotes around your text if it's meant to be a string."
        ) from e


def _read_configparser_section_items(config, section):
    """Read one section's keys and values from a configparser.ConfigParser.

    configparser expands %(name)s references while reading values, so a stray
    % fails here rather than when the file is read.

    Args:
        config: the configparser.ConfigParser the file was read into.
        section: the name of the section to read.

    Returns:
        list: the section's (key, value) pairs

    Raises:
        ConfigFileParserException: if a value's % or %(name)s can't be expanded.
    """
    try:
        return list(config[section].items())
    except configparser.InterpolationError as e:
        # name only the key, since configparser's own message shows the
        # value, which may be a secret ("from None" keeps that message
        # out of tracebacks too). e.option is the key being read, and the
        # problem may be in a value its %(name)s references pull in.
        if isinstance(e, configparser.InterpolationDepthError):
            problem = (
                "its %(name)s references refer back to it or are nested too deeply"
            )
        elif isinstance(e, configparser.InterpolationMissingOptionError):
            problem = (
                "it, or a value it references, has a %(name)s reference "
                "to a key that isn't in the same section or in [DEFAULT]. "
                "Write %% for a literal %"
            )
        else:
            problem = (
                "it, or a value it references, has a '%' that isn't '%%' "
                "or a valid %(name)s reference. Write %% for a literal %"
            )
        raise ConfigFileParserException(
            "Couldn't parse config file value for {}: {}.".format(e.option, problem)
        ) from None


class ConfigparserConfigFileParser(ConfigFileParser):
    """parses INI files using pythons configparser."""

    def get_syntax_description(self):
        msg = """Uses configparser module to parse an INI file which allows multi-line
        values.

        Allowed syntax is that for a ConfigParser with the following options:

            delimiters = ("=", ":")
            allow_no_value = False,
            comment_prefixes = ("#", ";")
            inline_comment_prefixes = ("#", ";")
            strict = True
            empty_lines_in_values = True

        See https://docs.python.org/3/library/configparser.html for details.

        Note: INI file sections names are still treated as comments.
        Settings in a [DEFAULT] section are also read. Keys are lowercased, so
        an option whose name has uppercase letters can't be set this way, and a
        saved config file that contains one fails to load unless
        ignore_unknown_config_file_keys=True.
        """
        return msg

    def parse(self, stream):
        # see ConfigFileParser.parse docstring
        # parse with configparser to allow multi-line values
        config = configparser.ConfigParser(
            delimiters=("=", ":"),
            allow_no_value=False,
            comment_prefixes=("#", ";"),
            inline_comment_prefixes=("#", ";"),
            strict=True,
            # a line indented more than the key above it continues that key's
            # value even after a blank or comment line, rather than starting a
            # new key, so a value saved with a blank line in it by an earlier
            # version of serialize() can't turn into extra keys
            empty_lines_in_values=True,
        )
        try:
            config.read_string(stream.read())
        except Exception as e:
            raise ConfigFileParserException("Couldn't parse config file: %s" % e)

        # convert to dict and remove INI section names
        result = OrderedDict()
        for section in config.sections() or [configparser.DEFAULTSECT]:
            for k, v in _read_configparser_section_items(config, section):
                multiLine2SingleLine = v.replace("\n", " ").replace("\r", " ")
                # handle special case for lists
                if _configparser_value_is_evaluated_as_list(multiLine2SingleLine):
                    result[k] = _evaluate_list_literal(multiLine2SingleLine)
                else:
                    result[k] = multiLine2SingleLine
        return result

    def serialize(self, items):
        # see ConfigFileParser.serialize docstring
        config = configparser.ConfigParser()
        # write each value so that parse() reads back the same value, except
        # that configparser strips whitespace from the start and end of a value
        values = OrderedDict()
        for key, value in items.items():
            if isinstance(value, list):
                # parse() can only evaluate the repr of literals, so write an
                # element that isn't one (e.g. a Path) as its str(). Keep the
                # other elements as they are, because
                # convert_item_to_command_line_arg() passes some inner lists on
                # whole, as str() of the list (e.g. for nargs="+" with
                # type=json.loads, or for an unknown key). A tuple that is the
                # whole value, rather than an element of a list, is written as
                # its str() below, as DefaultConfigFileParser writes it, so it
                # reads back as that string, which e.g. type=ast.literal_eval
                # turns back into the tuple.
                value = _list_literal_that_reads_back_as_the_list(value)
                is_python_literal = True
            else:
                # parse() turns newlines into spaces anyway, and a lone \r in a
                # value would be read back as the start of a new key, so write
                # the spaces here
                value = str(value).replace("\n", " ").replace("\r", " ")
                # parse() evaluates a value that looks like a list, so quote such
                # a string to read it back as a string
                is_python_literal = _configparser_value_is_evaluated_as_list(value)
                if is_python_literal:
                    value = repr(value)
            # parse() reads a "#" or ";" after a space as the start of a comment.
            if is_python_literal:
                # In a list repr, or a string quoted with repr(), they can only
                # be inside a string literal, so write them as escape codes,
                # which parse()'s ast.literal_eval decodes back.
                value = value.replace("#", "\\x23").replace(";", "\\x3b")
            elif re.search(r"\s[#;]", value):
                # configparser can't quote a plain string, so warn that the
                # value will read back cut short. The warning names only the
                # key, since the value may be a secret. One at the start of a
                # value is fine, since the value is written right after the "="
                # (see below).
                warnings.warn(
                    "The value of {} contains a '#' or ';' after a space, which "
                    "this config file format reads back as the start of a "
                    "comment, so the saved value will be cut short there. Use a "
                    "config_file_parser_class that can quote values, such as "
                    "YAMLConfigFileParser, to save it in full.".format(key)
                )
            # parse() expands %(name)s references, so escape every %
            values[key] = value.replace("%", "%%")
        config.read_dict({"DEFAULT": values})
        stream = StringIO()
        # "key=value" rather than "key = value", so that a value starting with
        # "#" or ";" isn't read back as a comment
        config.write(stream, space_around_delimiters=False)
        stream.seek(0)
        return stream.read()


class YAMLConfigFileParser(ConfigFileParser):
    """Parses YAML config files. Depends on the PyYAML module.
    https://pypi.python.org/pypi/PyYAML
    """

    def get_syntax_description(self):
        msg = (
            "The config file uses YAML syntax and must represent a YAML "
            "'mapping' (for details, see http://learn.getgrav.org/advanced/yaml)."
        )
        return msg

    def _load_yaml(self):
        """lazy-import PyYAML so that configargparse doesn't have to depend
        on it unless this parser is used."""
        try:
            import yaml
        except ImportError:
            raise ConfigFileParserMissingDependency(
                "Could not import yaml. "
                "It can be installed by running 'pip install PyYAML'"
            )

        try:
            from yaml import CSafeLoader as SafeLoader
            from yaml import CSafeDumper as SafeDumper
        except ImportError:
            from yaml import SafeLoader
            from yaml import SafeDumper

        # the safe dumper writes only what the safe loader can read back
        return yaml, SafeLoader, SafeDumper

    def parse(self, stream):
        # see ConfigFileParser.parse docstring
        yaml, SafeLoader, _ = self._load_yaml()

        try:
            parsed_obj = yaml.load(stream, Loader=SafeLoader)
        except Exception as e:
            raise ConfigFileParserException("Couldn't parse config file: %s" % e)

        if parsed_obj is None:
            # an empty file, or one that holds only comments, has no settings
            return OrderedDict()
        if not isinstance(parsed_obj, dict):
            raise ConfigFileParserException(
                "The config file doesn't appear to "
                "contain 'key: value' pairs (aka. a YAML mapping). "
                "yaml.load('%s') returned type '%s' instead of 'dict'."
                % (getattr(stream, "name", "stream"), type(parsed_obj).__name__)
            )

        result = OrderedDict()
        _add_config_values_as_str_except_lists(parsed_obj, result)
        return result

    def serialize(self, items, default_flow_style=False):
        # see ConfigFileParser.serialize docstring

        # lazy-import so there's no dependency on yaml unless this class is used
        yaml, _, SafeDumper = self._load_yaml()

        # it looks like ordering can't be preserved: http://pyyaml.org/ticket/29
        items = dict(items)
        return yaml.dump(
            items, default_flow_style=default_flow_style, Dumper=SafeDumper
        )


# I did not invented these regex, just put together some stuff from:
# - https://stackoverflow.com/questions/11859442/how-to-match-string-in-quotes-using-regex
# - and https://stackoverflow.com/a/41005190

_QUOTED_STR_REGEX = re.compile(r"(^\"(?:\\.|[^\"\\])*\"$)|" r"(^\'(?:\\.|[^\'\\])*\'$)")

_TRIPLE_QUOTED_STR_REGEX = re.compile(
    r"(^\"\"\"(\s+)?(([^\"]|\"([^\"]|\"[^\"]))*(\"\"?)?)?(\s+)?(?:\\.|[^\"\\])?\"\"\"$)|"
    # Unescaped quotes at the end of a string generates
    # "SyntaxError: EOL while scanning string literal",
    # so we don't account for those kind of strings as quoted.
    r"(^\'\'\'(\s+)?(([^\']|\'([^\']|\'[^\']))*(\'\'?)?)?(\s+)?(?:\\.|[^\'\\])?\'\'\'$)",
    flags=re.DOTALL,
)


def _quote_ini_value_if_needed_to_read_back_unchanged(value):
    """Write a string the way IniConfigParser.parse() reads back unchanged.

    parse() strips the whitespace around a value, skips an empty one, reads a
    value in square brackets as a list and one on several lines as a list (if
    split_ml_text_to_list is set), and unquotes a quoted one. A list element
    written on a line of its own that starts with '#' or ';' would be read as
    a comment. A value any of that would change is written as a Python string
    literal, which parse() unquotes back to the same string.

    Args:
        value: the string to write.

    Returns:
        str: the value, or its Python string literal
    """
    if (
        value != value.strip()
        or value == ""
        or "\n" in value
        or "\r" in value
        or (value.startswith("[") and value.endswith("]"))
        or value.startswith(("#", ";"))
        or is_quoted(value)
    ):
        return repr(value)
    return value


@functools.lru_cache(maxsize=256, typed=True)
def is_quoted(text, triple=True):
    """
    Detect whether a string is a quoted representation.

    :param triple: Also match tripple quoted strings.
    """
    return bool(_QUOTED_STR_REGEX.match(text)) or (
        triple and bool(_TRIPLE_QUOTED_STR_REGEX.match(text))
    )


def unquote_str(text, triple=True):
    """
    Unquote a maybe quoted string representation.
    If the string is not detected as being a quoted representation, it returns the same string as passed.
    It supports all kinds of python quotes: ``\"\"\"``, ``'''``, ``"`` and ``'``.

    :param triple: Also unquote tripple quoted strings.
    :raises ValueError: If the string is detected as beeing quoted but literal_eval() fails to evaluate it as string.
        This would be a bug in the regex.
    """
    if is_quoted(text, triple=triple):
        try:
            s = ast.literal_eval(text)
            assert isinstance(s, str)
        except Exception as e:
            raise ValueError(
                f"Error trying to unquote the quoted string: {text}: {e}"
            ) from e
        return s
    return text


def parse_toml_section_name(section_name):
    """
    Parse a TOML section name to a sequence of strings.

    The following names are all valid:

    .. python::

        "a.b.c"            # this is best practice -> returns ("a", "b", "c")
        " d.e.f "          # same as [d.e.f] -> returns ("d", "e", "f")
        " g .  h  . i "    # same as [g.h.i] -> returns ("g", "h", "i")
        ' j . "ʞ" . "l" '  # same as [j."ʞ"."l"], double or simple quotes here are supported. -> returns ("j", "ʞ", "l")
    """
    section = []
    for row in csv.reader([section_name], delimiter="."):
        for a in row:
            section.append(unquote_str(a.strip(), triple=False))
    return tuple(section)


def get_toml_section(data, section):
    """
    Given some TOML data (as loaded with toml.load()), returns the requested section of the data.
    Returns ``None`` if the section is not found.
    """
    sections = parse_toml_section_name(section) if isinstance(section, str) else section
    if not isinstance(data, dict):
        # a value along the section's path isn't a table, eg. my_tool = "off"
        return None
    itemdata = data.get(sections[0])
    if not itemdata:
        return None
    sections = sections[1:]
    if sections:
        return get_toml_section(itemdata, sections)
    else:
        if not isinstance(itemdata, dict):
            return None
        return itemdata


class TomlConfigParser(ConfigFileParser):
    """
    Create a TOML parser bounded to the list of provided sections. Useful to
    read settings from a project file such as ``pyproject.toml``.

    Usage::

        parser = ArgumentParser(
            default_config_files=['./pyproject.toml'],
            config_file_parser_class=TomlConfigParser(['tool.my_super_tool']),
        )

    `IniConfigParser` works the same way, for files such as ``setup.cfg``.

    Example::

        # this is a comment
        [tool.my_super_tool] # TOML section table.
        # how to specify a key-value pair
        format-string = "restructuredtext" # strings must be quoted
        # how to set an arg which has action="store_true"
        warnings-as-errors = true
        # how to set an arg which has action="count" or type=int
        verbosity = 1
        # how to specify a list arg (eg. arg which has action="append")
        repeatable-option = ["https://docs.python.org/3/objects.inv",
                             "https://twistedmatrix.com/documents/current/api/objects.inv"]
        # how to specify a multiline text:
        multi-line-text = '''
            Lorem ipsum dolor sit amet, consectetur adipiscing elit.
            Vivamus tortor odio, dignissim non ornare non, laoreet quis nunc.
            Maecenas quis dapibus leo, a pellentesque leo.
            '''

    `IniConfigParser` reads a similar INI file, but not this one as is: it
    doesn't support a '# comment' after a value, and it keeps the quotes
    around a value that has other text after them.

    See the `TOML specification <https://toml.io/en/>`_ for details.
    """

    def __init__(self, sections):
        """
        :param sections: The section names bounded to the new parser.
        """
        super().__init__()
        self.sections = sections

    def __call__(self):
        return self

    def parse(self, stream):
        """Parses the keys and values from a TOML config file."""
        # Use tomllib (Python 3.11+) if available, otherwise fall back to toml package
        try:
            import tomllib as toml
        except ImportError:
            try:
                import toml
            except ImportError as e:
                raise ConfigFileParserMissingDependency(
                    "Could not import toml or tomllib. "
                    "toml can be installed by running 'pip install toml'"
                ) from e

        # tomllib.load() requires binary mode, so use loads() for stream compatibility
        try:
            content = stream.read()
            # If content is bytes, decode it; if string, use as-is
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            config = toml.loads(content)
        except Exception as e:
            raise ConfigFileParserException("Couldn't parse TOML file: %s" % e)

        # convert to dict and filter based on section names
        result = OrderedDict()

        for section in self.sections:
            data = get_toml_section(config, section)
            if data:
                _add_config_values_as_str_except_lists(data, result)

        return result

    def get_syntax_description(self):
        return (
            "Config file syntax is Tom's Obvious, Minimal Language. "
            "See https://github.com/toml-lang/toml/blob/v0.5.0/README.md for details."
        )

    def serialize(self, items):
        """Serialize items to TOML format with section support.

        Note: Requires the 'toml' package for serialization (pip install toml).
        Python 3.11's tomllib only supports reading, not writing.
        """
        # lazy-import to avoid dependency unless class is used
        try:
            import toml
        except ImportError:
            raise ConfigFileParserMissingDependency(
                "The 'toml' package is required for TOML serialization. "
                "Install it with: pip install toml"
            )

        # Put items in the first configured section
        if not self.sections:
            raise ValueError(
                "A TomlConfigParser with no sections reads no settings back, so "
                "it can't write a config file. Give it the name of the section "
                "to use, eg. TomlConfigParser(['tool.my_tool'])."
            )

        # Create nested dict structure for section
        section_name = self.sections[0]
        sections = parse_toml_section_name(section_name)

        # Build nested dict from section path
        result = {}
        current = result
        for section in sections[:-1]:
            current[section] = {}
            current = current[section]
        current[sections[-1]] = dict(items)

        # the toml package doesn't escape every string correctly (eg. one with
        # a backslash followed by an x), so check that the text reads back as
        # the same values rather than write a file that means something else
        text = toml.dumps(result)
        try:
            values_read_back = self.parse(StringIO(text))
        except ConfigFileParserException as e:
            raise ValueError(
                "The settings can't be written out as TOML so that they read back "
                "unchanged: %s. Use another config_file_parser_class, such as "
                "YAMLConfigFileParser." % e
            ) from None
        for key, value in items.items():
            if values_read_back.get(key) != (
                value if isinstance(value, list) else str(value)
            ):
                raise ValueError(
                    "%s = %r can't be written out as TOML so that it reads back "
                    "unchanged. Use another config_file_parser_class, such as "
                    "YAMLConfigFileParser." % (key, value)
                )
        return text


class IniConfigParser(ConfigFileParser):
    """
    Create a INI parser bounded to the list of provided sections.
    Optionaly convert multiline strings to list.

    Keys are lowercased, as configparser does, so an option whose name has
    uppercase letters (eg. --outputDir) can't be set from this kind of config
    file, and a saved config file that contains one fails to load unless
    ignore_unknown_config_file_keys=True.

    Example (if split_ml_text_to_list=False)::

        # this is a comment
        ; also a comment
        [my-software]
        # how to specify a key-value pair
        format-string: restructuredtext
        # white space are ignored, so name = value same as name=value
        # this is why you can quote strings
        quoted-string = '\thello\tmom...  '
        # how to set an arg which has action="store_true"
        warnings-as-errors = true
        # how to set an arg which has action="count" or type=int
        verbosity = 1
        # how to specify a list arg (eg. arg which has action="append")
        repeatable-option = ["https://docs.python.org/3/objects.inv",
                       "https://twistedmatrix.com/documents/current/api/objects.inv"]
        # how to specify a multiline text:
        multi-line-text =
            Lorem ipsum dolor sit amet, consectetur adipiscing elit.
            Vivamus tortor odio, dignissim non ornare non, laoreet quis nunc.
            Maecenas quis dapibus leo, a pellentesque leo.

    Example (if split_ml_text_to_list=True)::

        # the same rules are applicable with the following changes:
        [my-software]
        # how to specify a list arg (eg. arg which has action="append")
        # Just enter one value per line (the list literal format can also be used)
        repeatable-option =
            https://docs.python.org/3/objects.inv
            https://twistedmatrix.com/documents/current/api/objects.inv
        # how to specify a multiline text (you have to quote it):
        multi-line-text = '''
            Lorem ipsum dolor sit amet, consectetur adipiscing elit.
            Vivamus tortor odio, dignissim non ornare non, laoreet quis nunc.
            Maecenas quis dapibus leo, a pellentesque leo.
            '''
    """

    def __init__(self, sections, split_ml_text_to_list):
        """
        :param sections: The section names bounded to the new parser.
        :param split_ml_text_to_list: Whether to convert multiline strings to list
        """
        super().__init__()
        self.sections = sections
        self.split_ml_text_to_list = split_ml_text_to_list

    def __call__(self):
        return self

    def parse(self, stream):
        """Parses the keys and values from an INI config file."""
        # parse with configparser to allow multi-line values
        config = configparser.ConfigParser()
        try:
            config.read_string(stream.read())
        except Exception as e:
            raise ConfigFileParserException("Couldn't parse INI file: %s" % e)

        # convert to dict and filter based on INI section names
        result = OrderedDict()
        # [DEFAULT] first, so that a named section's own values take precedence
        for section in [configparser.DEFAULTSECT] + config.sections():
            if section not in self.sections:
                continue
            for k, v in _read_configparser_section_items(config, section):
                strip_v = v.strip()
                if not strip_v:
                    # skip a key written with nothing after it (key =), the
                    # way YAML treats a key with no value
                    continue
                # evaluate lists
                if strip_v.startswith("[") and strip_v.endswith("]"):
                    result[k] = _evaluate_list_literal(strip_v)
                else:
                    try:
                        if is_quoted(strip_v):
                            # evaluate quoted string
                            result[k] = unquote_str(strip_v)
                        # split multi-line text into list of strings if split_ml_text_to_list is enabled.
                        elif self.split_ml_text_to_list and "\n" in v.rstrip("\n"):
                            result[k] = [
                                unquote_str(i) for i in strip_v.split("\n") if i
                            ]
                        else:
                            # stripped, since a value written on the lines after
                            # 'key =' comes back from configparser with a
                            # leading newline
                            result[k] = strip_v
                    except ValueError as e:
                        # error unquoting string
                        raise ConfigFileParserException(str(e)) from e
        return result

    def get_syntax_description(self):
        msg = (
            "Uses configparser module to parse an INI file which allows multi-line values. "
            "See https://docs.python.org/3/library/configparser.html for details. "
            "This parser includes support for quoting strings literal as well as python list syntax evaluation. "
            "Keys are lowercased, so an option whose name has uppercase letters can't be set this way. "
        )
        if self.split_ml_text_to_list:
            msg += (
                "Alternatively lists can be constructed with a plain multiline string, "
                "each non-empty line will be converted to a list item."
            )
        return msg

    def serialize(self, items):
        """Serialize items to INI format with section support."""
        config = configparser.ConfigParser()

        if not self.sections:
            raise ValueError(
                "An IniConfigParser with no sections reads no settings back, so "
                "it can't write a config file. Give it the name of the section "
                "to use, eg. IniConfigParser(['my_tool'], False)."
            )
        # Use the first configured section
        section_name = self.sections[0]

        # Add section if not DEFAULT
        if section_name != configparser.DEFAULTSECT:
            config.add_section(section_name)

        # Add items to the section
        for key, value in items.items():
            if isinstance(value, list):
                # Handle lists
                # an empty list would be written as an empty value, which
                # parse() skips, so it goes through the list literal below
                multi_line_text = (
                    "\n".join(
                        _quote_ini_value_if_needed_to_read_back_unchanged(str(v))
                        for v in value
                    )
                    if self.split_ml_text_to_list
                    and value
                    and not any(isinstance(v, list) for v in value)
                    else None
                )
                # parse() reads the whole value as a list literal when it's in
                # [...], and unquotes it when it's quoted, before it splits it
                # into lines, so text that looks like either can't be used
                if multi_line_text is not None and not (
                    (multi_line_text.startswith("[") and multi_line_text.endswith("]"))
                    or is_quoted(multi_line_text)
                ):
                    # Multi-line format
                    value = "\n" + multi_line_text
                else:
                    # Python list syntax, which parse() evaluates whether or not
                    # split_ml_text_to_list is set, and the only way to write
                    # inner lists
                    value = _list_literal_that_reads_back_as_the_list(value)
            else:
                value = _quote_ini_value_if_needed_to_read_back_unchanged(str(value))
            # parse() expands %(name)s references, so escape every %
            config.set(section_name, key, value.replace("%", "%%"))

        stream = StringIO()
        config.write(stream)
        stream.seek(0)
        return stream.read()


class CompositeConfigParser(ConfigFileParser):
    """
    Create a config parser composed of other config file parsers, each given as
    a ConfigFileParser instance or class (a class is instantiated with no args).

    The composite parser will successively try to parse the file with each parser,
    until it succeeds, else raise exception with all encountered errors.

    Usage, to read settings from either TOML or INI files::

        sections = ['tool.my_super_tool', 'tool:my_super_tool', 'my_super_tool']
        parser = ArgumentParser(
            default_config_files=['./pyproject.toml', 'setup.cfg', 'my_super_tool.ini'],
            config_file_parser_class=CompositeConfigParser([
                TomlConfigParser(sections),
                IniConfigParser(sections, split_ml_text_to_list=True),
            ]),
        )
    """

    def __init__(self, config_parser_types):
        super().__init__()
        self.parsers = [
            p if isinstance(p, ConfigFileParser) else p() for p in config_parser_types
        ]

        seen_ini = False
        for parser in self.parsers:
            if not seen_ini and isinstance(parser, IniConfigParser):
                seen_ini = True
                continue
            if seen_ini and isinstance(parser, TomlConfigParser):
                warnings.warn(
                    "IniConfigParser was found before TomlConfigParser in parsers for "
                    "CompositeConfigParser. This might lead to a TOML file being "
                    "parsed as an INI file. Reorder the parsers.",
                    category=SyntaxWarning,
                )

    def __call__(self):
        return self

    def parse(self, stream):
        errors = []
        for i, p in enumerate(self.parsers):
            try:
                return p.parse(stream)  # type: ignore[no-any-return]
            except Exception as e:
                if isinstance(e, ConfigFileParserMissingDependency):
                    # don't skip it silently, but don't take the rest of the
                    # chain down with it either
                    warnings.warn(
                        f"Cannot use parser {p.__class__.__name__} without "
                        f"optional dependency: {e}"
                    )
                errors.append(e)
                # Try to seek back to beginning for next parser
                # If this is not the last parser and seek fails, we can't continue
                if i < len(self.parsers) - 1:
                    try:
                        stream.seek(0)
                    except (AttributeError, OSError):
                        # Stream doesn't support seeking
                        raise ConfigFileParserException(
                            f"Error parsing config with {p.__class__.__name__}: {e}. "
                            f"Cannot try additional parsers because stream is not seekable."
                        ) from e
        raise ConfigFileParserException(
            f"Error parsing config: {', '.join(repr(str(e)) for e in errors)}"
        )

    def get_syntax_description(self):
        def guess_format_name(classname):
            # only the suffix goes, since "ConfigparserConfigFileParser" has
            # "config" and "parser" in the format's own name too
            name = re.sub(r"(ConfigFileParser|ConfigParser|Parser)$", "", classname)
            return name.strip("_").upper() or "??"

        msg = "Uses multiple config parser settings (in order): \n"
        for i, parser in enumerate(self.parsers):
            msg += f"[{i + 1}] {guess_format_name(parser.__class__.__name__)}: {parser.get_syntax_description()} \n"
        return msg

    def serialize(self, items):
        """Serialize items using the first parser in the composite."""
        if not self.parsers:
            raise ConfigFileParserException(
                "No parsers configured in CompositeConfigParser"
            )
        # Use the first parser to serialize
        return self.parsers[0].serialize(items)  # type: ignore[no-any-return]


# used while parsing args to keep track of where they came from
_COMMAND_LINE_SOURCE_KEY = "command_line"
_ENV_VAR_SOURCE_KEY = "environment_variables"
_CONFIG_FILE_SOURCE_KEY = "config_file"
_DEFAULTS_SOURCE_KEY = "defaults"


class ArgumentParser(argparse.ArgumentParser):
    """Drop-in replacement for `argparse.ArgumentParser` that adds support for
    environment variables and ``.ini`` or ``.yaml-style`` config files.
    """

    def __init__(self, *args, **kwargs):
        r"""Supports args of the `argparse.ArgumentParser` constructor
        as \*\*kwargs, as well as the following additional args.

        Keyword Arguments:
            add_config_file_help: Whether to add a description of config file
                syntax to the help message.
            add_env_var_help: Whether to add something to the help message for
                args that can be set through environment variables.
            auto_env_var_prefix: If set to a string instead of None, all config-
                file-settable options will become also settable via environment
                variables whose names are this prefix followed by the config
                file key, all in upper case. (eg. setting this to ``foo_`` will
                allow an arg like ``--my-arg`` to also be set via the FOO_MY_ARG
                environment variable)
            default_config_files: When specified, this list of config files will
                be parsed in order, with the values from each config file
                taking precedence over previous ones. This allows an application
                to look for config files in multiple standard locations such as
                the install directory, home directory, and/or current directory.
                Also, shell \* syntax can be used to specify all conf files in a
                directory, which are then parsed in alphabetical order. For
                example::

                    ["/etc/conf/app_config.ini",
                    "/etc/conf/conf-enabled/*.ini",
                    "~/.my_app_config.ini",
                    "./app_config.txt"]

                Path entries may be strings, ``os.PathLike`` objects (e.g.
                ``pathlib.Path``), or zero-argument callable functions that
                return an open file-like object containing config file
                contents. Any provided callable is invoked each time the parser opens
                config files, and the returned stream is closed by the parser
                after parsing. The callable must return a stream. This is useful
                for sourcing config from non-filesystem locations such as in-memory
                buffers, secrets managers, or HTTP responses.
            ignore_unknown_config_file_keys: If true, settings that are found
                in a config file but don't correspond to any defined
                configargparse args will be ignored. If false, they will be
                processed and appended to the commandline like other args, and
                can be retrieved using parse_known_args() instead of parse_args()
            config_file_open_func: function used to open a config file for reading
                or writing. Needs to return a file-like object.
            config_file_parser_class: a configargparse.ConfigFileParser
                subclass, or an instance of one, which determines the config
                file format. configargparse comes with DefaultConfigFileParser,
                YAMLConfigFileParser, ConfigparserConfigFileParser,
                IniConfigParser, TomlConfigParser and CompositeConfigParser. The
                last three take constructor args, so pass them as instances,
                eg. TomlConfigParser(['tool.my_tool']).
            args_for_setting_config_path: A list of one or more command line
                args to be used for specifying the config file path
                (eg. ["-c", "--config-file"]). Default: []
            config_arg_is_required: When args_for_setting_config_path is set,
                set this to True to always require users to provide a config path.
            config_arg_help_message: the help message to use for the
                args listed in args_for_setting_config_path.
            args_for_writing_out_config_file: A list of one or more command line
                args to use for specifying a config file output path. If
                provided, these args cause configargparse to write out a config
                file with settings based on the other provided commandline args,
                environment variants and defaults, and then to exit.
                (eg. ["-w", "--write-out-config-file"]). Default: []
                These args can only be set on the command line. A config file
                key that names one is an error, and they can't have an env_var,
                since either would let whoever controls those overwrite an
                arbitrary file.
            write_out_config_file_arg_help_message: The help message to use for
                the args in args_for_writing_out_config_file.
        """
        # take this class's own keyword args out of kwargs before the rest are
        # passed on to argparse.ArgumentParser.__init__()
        add_config_file_help = kwargs.pop("add_config_file_help", True)
        add_env_var_help = kwargs.pop("add_env_var_help", True)
        auto_env_var_prefix = kwargs.pop("auto_env_var_prefix", None)
        default_config_files = kwargs.pop("default_config_files", [])
        ignore_unknown_config_file_keys = kwargs.pop(
            "ignore_unknown_config_file_keys", False
        )
        config_file_parser_class = kwargs.pop(
            "config_file_parser_class", DefaultConfigFileParser
        )
        args_for_setting_config_path = kwargs.pop("args_for_setting_config_path", [])
        config_arg_is_required = kwargs.pop("config_arg_is_required", False)
        config_arg_help_message = kwargs.pop(
            "config_arg_help_message", "config file path"
        )
        args_for_writing_out_config_file = kwargs.pop(
            "args_for_writing_out_config_file", []
        )
        write_out_config_file_arg_help_message = kwargs.pop(
            "write_out_config_file_arg_help_message",
            "takes the current "
            "command line args and writes them out to a config file at the "
            "given path, then exits",
        )

        config_file_open_func = kwargs.pop("config_file_open_func", open)

        # Validate args before proceeding
        for name, value in [
            ("default_config_files", default_config_files),
            ("args_for_setting_config_path", args_for_setting_config_path),
            ("args_for_writing_out_config_file", args_for_writing_out_config_file),
        ]:
            if not isinstance(value, (list, tuple)):
                hint = " (e.g. ['%s'])" % value if isinstance(value, str) else ""
                raise TypeError("%s must be a list%s. Got: %r" % (name, hint, value))

        for i, entry in enumerate(default_config_files):
            if not (isinstance(entry, (str, bytes, os.PathLike)) or callable(entry)):
                raise TypeError(
                    "default_config_files[%d] must be a string, bytes, or "
                    "os.PathLike path, or a callable that returns an open "
                    "file-like object. Got: %r" % (i, entry)
                )

        if not callable(config_file_open_func):
            raise TypeError(
                "config_file_open_func must be callable. Got: %r"
                % (config_file_open_func,)
            )

        self._config_file_open_func = config_file_open_func

        self._add_config_file_help = add_config_file_help
        self._add_env_var_help = add_env_var_help
        self._auto_env_var_prefix = auto_env_var_prefix
        # set while parse_known_intermixed_args() runs, and while argparse's
        # own version of it runs inside parse_known_args()
        self._parsing_intermixed_args = False
        self._argparse_is_parsing_intermixed_args = False
        # (action, value, string) triples recorded by _get_value(), and the
        # actions _get_values() was given args for, during the last parse
        self._parsed_values_and_the_strings_they_came_from = []
        self._actions_given_values = set()

        if "formatter_class" in kwargs:
            fc = kwargs["formatter_class"]
            if isinstance(fc, type) and not issubclass(fc, argparse.HelpFormatter):
                msg = (
                    "formatter_class must be a subclass of "
                    "argparse.HelpFormatter. Got: %r." % (fc,)
                )
                if issubclass(fc, ConfigFileParser):
                    msg += " Perhaps you meant to use config_file_parser_class?"
                raise TypeError(msg)

        argparse.ArgumentParser.__init__(self, *args, **kwargs)

        # parse the additional args
        if config_file_parser_class is None:
            self._config_file_parser = DefaultConfigFileParser()
        elif isinstance(config_file_parser_class, ConfigFileParser):
            self._config_file_parser = config_file_parser_class
        elif isinstance(config_file_parser_class, type) and issubclass(
            config_file_parser_class, ConfigFileParser
        ):
            self._config_file_parser = config_file_parser_class()
        else:
            raise TypeError(
                "config_file_parser_class must be a subclass of "
                "ConfigFileParser (such as DefaultConfigFileParser, "
                "YAMLConfigFileParser, etc.). "
                "Got: %r. Perhaps you meant to use formatter_class?"
                % (config_file_parser_class,)
            )

        self._default_config_files = default_config_files
        self._ignore_unknown_config_file_keys = ignore_unknown_config_file_keys
        if args_for_setting_config_path:
            self.add_argument(
                *args_for_setting_config_path,
                dest="config_file",
                required=config_arg_is_required,
                help=config_arg_help_message,
                is_config_file_arg=True,
            )

        if args_for_writing_out_config_file:
            self.add_argument(
                *args_for_writing_out_config_file,
                dest="write_out_config_file_to_this_path",
                metavar="CONFIG_OUTPUT_PATH",
                help=write_out_config_file_arg_help_message,
                is_write_out_config_file_arg=True,
            )

        # Workaround for Python < 3.9: exit_on_error parameter was added in 3.9
        # This can be removed when minimum Python version is raised to 3.9+
        if sys.version_info < (3, 9):
            self.exit_on_error = True

    def _add_args_from_source(
        self,
        args,
        actions_keys_and_values,
        source_description,
        variable_length_list_ends,
        flag_actions_set_by_higher_priority_sources,
    ):
        """Turn the settings from one config file or the env vars into args.

        Args:
            args: the command line args so far.
            actions_keys_and_values: (action, key, value) for each setting from
                the source that isn't set by a higher-priority source. action is
                None for a config file key that no arg has.
            source_description: where the settings came from, for error
                messages, eg. "in an environment variable".
            variable_length_list_ends: see
                _insert_args_from_config_file_or_env_vars(), updated in place.
            flag_actions_set_by_higher_priority_sources: the flag actions set
                by sources that take precedence over the ones still to come,
                updated in place with this source's flags.

        Returns:
            tuple: the new command line args, and whether this source's args
            were added to them (they aren't when one would set a
            write-out-config-file arg)
        """
        # args for options that take any number of values, set to a list, go
        # before the source's other args
        variable_length_list_args = []
        other_args = []
        last_variable_length_list_key = None
        flag_actions = set()
        for action, key, value in actions_keys_and_values:
            item_args = self.convert_item_to_command_line_arg(action, key, value)
            if _is_variable_length_list(action, value):
                variable_length_list_args += item_args
                last_variable_length_list_key = key
            else:
                other_args += item_args
            if isinstance(action, ACTION_TYPES_THAT_DONT_NEED_A_VALUE):
                flag_actions.add(action)

        if not self._synthesized_args_dont_set_a_write_out_config_file_arg(
            variable_length_list_args + other_args,
            source_description,
            _subparsers_of(self),
        ):
            return args, False
        flag_actions_set_by_higher_priority_sources.update(flag_actions)
        return (
            self._insert_args_from_config_file_or_env_vars(
                args,
                variable_length_list_args,
                other_args,
                last_variable_length_list_key,
                variable_length_list_ends,
            ),
            True,
        )

    def _insert_args_from_config_file_or_env_vars(
        self,
        args,
        variable_length_list_args,
        other_args,
        last_variable_length_list_key,
        variable_length_list_ends,
    ):
        """Insert the args that one config file or the env vars set.

        argparse reads every arg after the values of an option that takes any
        number of values as one more value, until the next option. So those
        lists go first, where the source's other args end them. When nothing
        from the source follows them and the command line goes on with a
        subcommand or positional, they go before the first option on the
        command line instead, if there is one. Where a block that ends with
        such a list ends is recorded, so that parse_known_args() can check what
        follows it once every source is in.

        Args:
            args: the command line args so far.
            variable_length_list_args: the args that set options taking any
                number of values to a list.
            other_args: the source's other args.
            last_variable_length_list_key: the config file key or env var that
                set the last of those lists, for an error message.
            variable_length_list_ends: (index, key) pairs, updated in place.

        Returns:
            list: the args with these inserted
        """
        inserted_args = variable_length_list_args + other_args
        idx = self._find_insertion_index(args)
        ends_with_variable_length_list = bool(variable_length_list_args) and not (
            other_args
        )
        if (
            ends_with_variable_length_list
            and idx < len(args)
            and not self._arg_is_read_as_an_option(args[idx])
        ):
            option_indexes = [
                i
                for i, arg in enumerate(args[:idx])
                if self._arg_is_read_as_an_option(arg)
            ]
            if option_indexes:
                idx = option_indexes[0]

        variable_length_list_ends[:] = [
            (end + len(inserted_args) if end > idx else end, key)
            for end, key in variable_length_list_ends
        ]
        if ends_with_variable_length_list:
            variable_length_list_ends.append(
                (idx + len(inserted_args), last_variable_length_list_key)
            )
        return args[:idx] + inserted_args + args[idx:]

    def _find_insertion_index(self, args):
        """Find the right index to insert config/env var args into the command line.

        Inserts at position 0 when a REMAINDER positional exists, otherwise
        before a subparser command (one before any ``--``) so the parent parser
        sees injected args first, otherwise before an option with
        nargs=REMAINDER or the ``--`` separator if present, or before the first
        optional arg. Falls back to appending.
        """
        # a REMAINDER positional takes every arg from its first one on, options
        # and '--' included, so the inserted args go before anything it could
        # take
        if self._has_a_remainder_positional():
            return 0

        end_of_this_parsers_args = self._find_end_of_this_parsers_args(args)
        if end_of_this_parsers_args is not None:
            # an option with nargs=REMAINDER would take the inserted args as its
            # values, so they go before it
            if end_of_this_parsers_args > 0 and (
                self._arg_is_an_option_that_takes_every_later_arg(
                    args[end_of_this_parsers_args - 1]
                )
            ):
                return end_of_this_parsers_args - 1
            return end_of_this_parsers_args

        for i, arg in enumerate(args):
            if self._arg_is_read_as_an_option(arg):
                return i
        return len(args)

    def _arg_is_read_as_an_option(self, arg_string):
        """Check whether argparse reads a command line arg as an option.

        A lone prefix char (eg. '-' for stdin) is a value, and so is a negative
        number when the parser has no option that looks like one. This uses
        argparse's own pattern for a negative number, which differs by Python
        version (from 3.14 it also matches eg. "-1e5").

        Args:
            arg_string: a single command line arg.

        Returns:
            bool: whether it is an option (or the '--' separator) rather than
            a value
        """
        return (
            len(arg_string) > 1
            and arg_string[0] in self.prefix_chars
            and not (
                self._negative_number_matcher.match(arg_string)
                and not self._has_negative_number_optionals
            )
        )

    def _find_subcommand_index(self, args):
        """Find where the subcommand is on the command line, if there is one.

        An arg that is the value of the option before it, like the first
        'build' in '--target build build', isn't the subcommand, so the values
        of an option named exactly, and taking a set number of them, are
        skipped the way argparse reads them.

        Args:
            args: the command line args.

        Returns:
            int or None: the index of the first arg naming a subcommand
        """
        # including a positional that dispatches with a dict of choices of the
        # program's own, which takes the rest of the args the same way
        subcommand_names = next(
            (
                action.choices
                for action in self._actions
                if _dispatches_to_subparsers(action) and action.choices
            ),
            None,
        )
        if subcommand_names is None:
            return None
        return next(
            (
                i
                for i in self._indexes_of_positional_args(args)
                if args[i] in subcommand_names
            ),
            None,
        )

    def _indexes_of_positional_args(self, args):
        """Find the args argparse reads as positional args.

        Options aren't, and neither are an option's values, which are skipped
        the way argparse reads them: nargs=None or '?' takes the next arg, an
        int n takes up to n, and '*' or '+' take every arg up to the next
        option, as long as they aren't options themselves. A value attached
        with '=' takes nothing more, and an option with nargs=REMAINDER takes
        everything after it.

        Args:
            args: the command line args.

        Yields:
            int: the index of each positional arg, in order
        """
        i = 0
        while i < len(args):
            if not self._arg_is_read_as_an_option(args[i]):
                yield i
                i += 1
                continue
            option_action = self._find_option_that_takes_its_values_from_later_args(
                args[i]
            )
            i += 1
            if option_action is None:
                continue
            nargs = option_action.nargs
            if nargs == argparse.REMAINDER:
                return
            if nargs in (argparse.ZERO_OR_MORE, argparse.ONE_OR_MORE):
                max_number_of_values = len(args)
            elif nargs is None or nargs == argparse.OPTIONAL:
                max_number_of_values = 1
            elif isinstance(nargs, int):
                max_number_of_values = nargs
            else:
                max_number_of_values = 0
            while (
                max_number_of_values > 0
                and i < len(args)
                and not self._arg_is_read_as_an_option(args[i])
            ):
                i += 1
                max_number_of_values -= 1

    def _find_option_that_takes_its_values_from_later_args(self, arg_string):
        """Find the option an arg names, if its values are the args after it.

        Args:
            arg_string: a single command line arg that is read as an option.

        Returns:
            argparse.Action or None: the option named exactly, or by an
            unambiguous abbreviation of a long option, or None if the arg
            names none or has its value attached with '='
        """
        if arg_string in self._option_string_actions:
            return self._option_string_actions[arg_string]
        if (
            "=" in arg_string
            or not self.allow_abbrev
            or len(arg_string) < 3
            or arg_string[1] not in self.prefix_chars
        ):
            return None
        matching_actions = {
            action
            for option_string, action in self._option_string_actions.items()
            if option_string.startswith(arg_string)
        }
        return matching_actions.pop() if len(matching_actions) == 1 else None

    def _has_a_remainder_positional(self):
        """Check whether this parser has a positional arg with nargs=REMAINDER.

        Returns:
            bool: whether it has one
        """
        return any(
            not a.option_strings and a.nargs == argparse.REMAINDER
            for a in self._actions
        )

    def _arg_is_an_option_that_takes_every_later_arg(self, arg_string):
        """Check whether an arg names an option with nargs=REMAINDER.

        Such an option takes every arg after it as its values, options included.

        Args:
            arg_string: a single command line arg.

        Returns:
            bool: whether it names one
        """
        if not self._arg_is_read_as_an_option(arg_string):
            return False
        option_action = self._find_option_that_takes_its_values_from_later_args(
            arg_string
        )
        return option_action is not None and option_action.nargs == argparse.REMAINDER

    def _find_where_args_stop_being_read_as_options(self, args):
        """Find where argparse stops reading this parser's args as options.

        That is at the '--' separator, with a REMAINDER positional at its first
        arg, or just after an option with nargs=REMAINDER that comes before any
        subcommand, since those take every arg from there on, options included.

        Args:
            args: the command line args.

        Returns:
            int or None: that index, or None if argparse reads them all
        """
        end_of_options_index = args.index("--") if "--" in args else None
        if self._has_a_remainder_positional():
            first_positional_index = next(
                self._indexes_of_positional_args(args[:end_of_options_index]), None
            )
            if first_positional_index is not None:
                return first_positional_index
        # argparse hands the args from the subcommand on to the subparser
        subcommand_index = self._find_subcommand_index(args[:end_of_options_index])
        if subcommand_index is not None:
            args = args[:subcommand_index]
        for i, arg in enumerate(args[:end_of_options_index]):
            if self._arg_is_an_option_that_takes_every_later_arg(arg):
                return i + 1
        return end_of_options_index

    def _find_end_of_this_parsers_args(self, args):
        """Find where the args this parser parses itself end.

        Args:
            args: the command line args.

        Returns:
            int or None: the index of the subcommand, or else of where argparse
            stops reading args as options (see
            _find_where_args_stop_being_read_as_options()), or None
        """
        end_of_options_index = self._find_where_args_stop_being_read_as_options(args)
        subcommand_index = self._find_subcommand_index(args[:end_of_options_index])
        return (
            subcommand_index if subcommand_index is not None else end_of_options_index
        )

    def _option_strings_that_override(self, action):
        """Find the option strings that, when already on the command line, mean
        an env var or config file value for the given action should be dropped.

        These are the action's own option strings plus those of every other
        action in the same mutually exclusive group, mirroring the conflicts
        argparse checks for. Without the latter, a config file value for one
        member of a group would clash with a different member given on the
        command line instead of being overridden by it (see issue #164).

        Args:
            action: the argparse.Action to find overriding option strings for.

        Returns:
            list[str]: the option strings
        """
        option_strings = list(action.option_strings)
        for group in self._mutually_exclusive_groups:
            if action in group._group_actions:
                for other_action in group._group_actions:
                    if other_action is not action:
                        option_strings += other_action.option_strings
        return option_strings

    def parse_args(
        self, args=None, namespace=None, config_file_contents=None, env_vars=os.environ
    ):
        """Supports all the same args as the `argparse.ArgumentParser.parse_args()`,
        as well as the following additional args.

        Arguments:
            args: a list of args as in argparse, or a string (eg. "-x -y bla")
            config_file_contents: String. Used for testing.
            env_vars: Dictionary. Used for testing.

        Returns:
            argparse.Namespace: namespace
        """
        args, argv = self.parse_known_args(
            args=args,
            namespace=namespace,
            config_file_contents=config_file_contents,
            env_vars=env_vars,
            ignore_help_args=False,
        )
        self._reject_unrecognized_args(argv)
        return args

    def parse_intermixed_args(
        self, args=None, namespace=None, config_file_contents=None, env_vars=os.environ
    ):
        """Like parse_args(), but lets optional and positional args be
        intermixed, as `argparse.ArgumentParser.parse_intermixed_args()` does.

        Arguments:
            args: a list of args as in argparse, or a string (eg. "-x -y bla")
            config_file_contents: String. Used for testing.
            env_vars: Dictionary. Used for testing.

        Returns:
            argparse.Namespace: namespace
        """
        args, argv = self.parse_known_intermixed_args(
            args=args,
            namespace=namespace,
            config_file_contents=config_file_contents,
            env_vars=env_vars,
        )
        self._reject_unrecognized_args(argv)
        return args

    def parse_known_intermixed_args(
        self,
        args=None,
        namespace=None,
        config_file_contents=None,
        env_vars=os.environ,
        ignore_help_args=False,
    ):
        """Like parse_known_args(), but lets optional and positional args be
        intermixed, as `argparse.ArgumentParser.parse_known_intermixed_args()`
        does.

        Arguments:
            args: a list of args as in argparse, or a string (eg. "-x -y bla")
            config_file_contents (str). Used for testing.
            env_vars (dict). Used for testing.
            ignore_help_args (bool): see parse_known_args().

        Returns:
            tuple[argparse.Namespace, list[str]]: tuple namespace, unknown_args
        """
        self._parsing_intermixed_args = True
        try:
            return self.parse_known_args(
                args=args,
                namespace=namespace,
                config_file_contents=config_file_contents,
                env_vars=env_vars,
                ignore_help_args=ignore_help_args,
            )
        finally:
            self._parsing_intermixed_args = False

    def _reject_unrecognized_args(self, unrecognized_args):
        """Report args that no arg of this parser took, the way argparse does.

        Args:
            unrecognized_args: the args left over after parsing.
        """
        if unrecognized_args:
            self._report_error(
                "unrecognized arguments: %s" % " ".join(unrecognized_args)
            )

    def _report_error(self, message):
        """Report an error the way argparse does for its own errors.

        With exit_on_error=False, argparse raises an ArgumentError instead of
        exiting, so errors in config files and env vars do the same.

        Args:
            message: the error message.
        """
        if self.exit_on_error:
            self.error(message)
        else:
            raise ArgumentError(None, message)

    def _fill_in_action_attributes_this_module_reads(self):
        """Give each action the attributes this module's add_argument() sets.

        An action that didn't go through it, eg. one copied from a plain
        argparse parser passed as parents=, gets them with their default
        values, so the code that reads them works the same for every action.
        With auto_env_var_prefix set, each action that can have an env var and
        has none gets its automatic one. Both parsing and the help text read
        these, so this runs before either.
        """
        for action in self._actions:
            action.is_positional_arg = not action.option_strings
            for attribute_name in (
                "env_var",
                "is_config_file_arg",
                "is_write_out_config_file_arg",
            ):
                if not hasattr(action, attribute_name):
                    setattr(action, attribute_name, None)

        if self._auto_env_var_prefix is not None:
            for a in self._actions:
                config_file_keys = self.get_possible_config_keys(a)
                if config_file_keys and not (
                    a.env_var
                    or a.is_positional_arg
                    or a.is_config_file_arg
                    or a.is_write_out_config_file_arg
                    or isinstance(a, argparse._VersionAction)
                    or isinstance(a, argparse._HelpAction)
                ):
                    stripped_config_file_key = config_file_keys[0].strip(
                        self.prefix_chars
                    )
                    a.env_var = (
                        (self._auto_env_var_prefix + stripped_config_file_key)
                        .replace("-", "_")
                        .upper()
                    )

    def parse_known_args(
        self,
        args=None,
        namespace=None,
        config_file_contents=None,
        env_vars=os.environ,
        ignore_help_args=False,
    ):
        """Supports all the same args as the `argparse.ArgumentParser.parse_args()`,
        as well as the following additional args.

        Arguments:
            args: a list of args as in argparse, or a string (eg. "-x -y bla")
            config_file_contents (str). Used for testing.
            env_vars (dict). Used for testing.
            ignore_help_args (bool): This flag determines behavior when user specifies ``--help`` or ``-h``. If False,
                it will have the default behavior - printing help and exiting. If True, it won't do either.

        Returns:
            tuple[argparse.Namespace, list[str]]: tuple namescpace, unknown_args
        """
        if self._argparse_is_parsing_intermixed_args:
            # before Python 3.12, argparse's parse_known_intermixed_args() calls
            # parse_known_args() itself, with the args that
            # parse_known_intermixed_args() below already put together
            return argparse.ArgumentParser.parse_known_args(self, args, namespace)

        if args is None:
            args = sys.argv[1:]
        elif isinstance(args, str):
            args = args.split()
        else:
            args = list(args)

        if self.fromfile_prefix_chars:
            # argparse only reads files of args once it parses, which is after
            # the checks below for options already on the command line, so an
            # option in such a file would lose to its config file or env var
            # value. Read them now, the way argparse would.
            try:
                args = self._read_args_from_files(args)
            except argparse.ArgumentError as e:
                # from Python 3.12, a file that can't be read raises this
                self._report_error(str(e))

        self._fill_in_action_attributes_this_module_reads()

        if ignore_help_args:
            # argparse reads a '-h' after '--' or in a REMAINDER positional's or
            # option's args as a value, not a request for help, so those are kept
            end_of_option_args = self._find_where_args_stop_being_read_as_options(args)
            if end_of_option_args is None:
                end_of_option_args = len(args)
            args = [
                arg for arg in args[:end_of_option_args] if arg not in ("-h", "--help")
            ] + args[end_of_option_args:]

        # maps a string describing the source (eg. env var) to a settings dict
        # to keep track of where values came from (used by print_values()).
        # The settings dicts for env vars and config files will then map
        # the config key to an (argparse Action obj, string value) 2-tuple.
        self._source_to_settings = OrderedDict()
        if args:
            a_v_pair = (None, list(args))  # copy args list to isolate changes
            self._source_to_settings[_COMMAND_LINE_SOURCE_KEY] = {"": a_v_pair}

        # add env var settings to the commandline that aren't there already
        # where each inserted block of args that ends with a list for an option
        # that takes any number of values ends, with the key that set that
        # list, to check what follows it once all the blocks are in (see
        # _insert_args_from_config_file_or_env_vars)
        variable_length_list_ends = []
        env_var_items = []
        env_var_settings = OrderedDict()
        # A flag set to false (eg. FOO=false for a store_true arg) adds no args
        # to the command line, so already_on_command_line() can't tell it was
        # set, yet it should still keep lower-priority sources from setting
        # that flag (see issue #105). So remember the flags each source sets.
        # Only flags: an append arg set to an empty list also adds no args, but
        # it has never overridden lower-priority sources, and making it do so
        # would leave it None rather than an empty list.
        flag_actions_set_by_higher_priority_sources = set()
        actions_with_env_var_values = [
            a
            for a in self._actions
            if not a.is_positional_arg
            and a.env_var
            and a.env_var in env_vars
            and not already_on_command_line(
                args,
                self._option_strings_that_override(a),
                self.prefix_chars,
                parser=self,
            )
        ]
        for action in actions_with_env_var_values:
            key = action.env_var
            value = env_vars[key]
            # Skip empty string env vars for args with nargs to match YAML behavior
            # where empty values are treated as None/not present (see issue #296)
            if value == "" and action.nargs:
                continue
            # Make list-string into list.
            if action.nargs or isinstance(action, argparse._AppendAction):
                value = _parse_bracketed_list_value(value)
            env_var_items.append((action, key, value))
            env_var_settings[key] = (action, env_vars[key])

        args, env_var_args_were_added = self._add_args_from_source(
            args,
            env_var_items,
            "in an environment variable",
            variable_length_list_ends,
            flag_actions_set_by_higher_priority_sources,
        )
        if env_var_args_were_added and env_var_settings:
            self._source_to_settings[_ENV_VAR_SOURCE_KEY] = env_var_settings

        # before parsing any config files, check if -h was specified.
        supports_help_arg = any(
            a for a in self._actions if isinstance(a, argparse._HelpAction)
        )
        # only a '-h' that argparse reads as an option asks for help
        args_read_as_options = args[
            : self._find_where_args_stop_being_read_as_options(args)
        ]
        skip_config_file_parsing = supports_help_arg and (
            "-h" in args_read_as_options or "--help" in args_read_as_options
        )

        # prepare for reading config file(s)
        known_config_keys = {
            config_key: action
            for action in self._actions
            for config_key in self.get_possible_config_keys(action)
        }

        # open the config file(s). config_streams is a list of (stream, source_label) tuples.
        config_streams = []
        if config_file_contents is not None:
            stream = StringIO(config_file_contents)
            stream.name = "method arg"
            config_streams = [(stream, "method arg")]
        elif not skip_config_file_parsing:
            config_streams = self._open_config_files(args)

        # parse each config file
        try:
            for stream, source_label in reversed(config_streams):
                try:
                    config_items = self._config_file_parser.parse(stream)
                except ConfigFileParserException as e:
                    self._report_error(str(e))
                    # a program can override exit(), so _report_error() may
                    # return, and this file has no settings to add
                    continue

                # add each config item to the commandline unless it's there already
                config_file_items = []
                for key, value in config_items.items():
                    if key in known_config_keys:
                        action = known_config_keys[key]
                        discard_this_key = (
                            action in flag_actions_set_by_higher_priority_sources
                            or already_on_command_line(
                                args,
                                self._option_strings_that_override(action),
                                self.prefix_chars,
                                parser=self,
                            )
                        )
                    else:
                        action = None
                        if not isinstance(key, str) and not (
                            self._ignore_unknown_config_file_keys
                        ):
                            # eg. YAML reads a bare yes, no, on, off, true, false
                            # or number as something other than a string
                            self._report_error(
                                "The config file key %r was read as a %s rather "
                                "than a string, as YAML does for a bare yes, no, "
                                "on, off, true, false or number. Put the key in "
                                'quotes, eg. "yes": true.' % (key, type(key).__name__)
                            )
                            continue
                        discard_this_key = (
                            self._ignore_unknown_config_file_keys
                            or already_on_command_line(
                                args,
                                [
                                    self.get_command_line_key_for_unknown_config_file_setting(
                                        key
                                    )
                                ],
                                self.prefix_chars,
                            )
                        )

                    # Skip empty string values for args with nargs to match YAML behavior
                    # where empty values are treated as None/not present (see issue #296)
                    if value == "" and action and action.nargs:
                        continue

                    if not discard_this_key:
                        config_file_items.append((action, key, value))
                        source_key = "%s|%s" % (
                            _CONFIG_FILE_SOURCE_KEY,
                            source_label,
                        )
                        if source_key not in self._source_to_settings:
                            self._source_to_settings[source_key] = OrderedDict()
                        self._source_to_settings[source_key][key] = (action, value)

                # Flags are recorded between sources, not between entries in
                # one file.
                args, _ = self._add_args_from_source(
                    args,
                    config_file_items,
                    "in a config file (%s)" % source_label,
                    variable_length_list_ends,
                    flag_actions_set_by_higher_priority_sources,
                )
        finally:
            # Close every stream exactly once, regardless of whether parsing
            # succeeded or aborted partway through (e.g. a custom parser
            # raised a non-ConfigFileParserException).
            _close_config_streams(config_streams)

        for list_end, key in variable_length_list_ends:
            if list_end < len(args) and not self._arg_is_read_as_an_option(
                args[list_end]
            ):
                self._report_error(
                    "%s sets a list for an option that takes any number of values, "
                    "and argparse would read the %r after it as one more of them. "
                    "Set it on the command line instead, or put an option before "
                    "%r there." % (key, args[list_end], args[list_end])
                )

        # parse all args (including commandline, config file, and env var)
        self._write_out_config_file_paths = OrderedDict()
        self._parsed_values_and_the_strings_they_came_from = []
        self._actions_given_values = set()
        if self._parsing_intermixed_args:
            self._argparse_is_parsing_intermixed_args = True
            try:
                namespace, unknown_args = (
                    argparse.ArgumentParser.parse_known_intermixed_args(
                        self, args=args, namespace=namespace
                    )
                )
            finally:
                self._argparse_is_parsing_intermixed_args = False
        else:
            namespace, unknown_args = argparse.ArgumentParser.parse_known_args(
                self, args=args, namespace=namespace
            )

        # save default settings for use by print_values(). A positional arg has
        # no option string to find on the command line, so whether it was given
        # a value is only known from the parse.
        default_settings = OrderedDict()
        for action in self._actions:
            cares_about_default_value = (
                not action.is_positional_arg or action.nargs in [OPTIONAL, ZERO_OR_MORE]
            )
            if (
                already_on_command_line(
                    args, action.option_strings, self.prefix_chars, parser=self
                )
                or action in self._actions_given_values
                or not cares_about_default_value
                or action.default is None
                or action.default == SUPPRESS
                or isinstance(action, ACTION_TYPES_THAT_DONT_NEED_A_VALUE)
            ):
                continue
            else:
                if action.option_strings:
                    key = action.option_strings[-1]
                else:
                    key = action.dest
                default_settings[key] = (action, str(action.default))

        if default_settings:
            self._source_to_settings[_DEFAULTS_SOURCE_KEY] = default_settings

        output_file_paths = [
            path
            for path in self._write_out_config_file_paths.values()
            if path is not None
        ]
        # handle any args that have is_write_out_config_file_arg set to true.
        # Each one recorded its own path as it was parsed, which is the only
        # place that path can have come from, and the args built from config
        # files and env vars were kept from setting one at all above.
        self.write_config_file(namespace, output_file_paths, exit_after=True)
        return namespace, unknown_args

    def get_source_to_settings_dict(self):
        """
        If called after `parse_args()` or `parse_known_args()`, returns a dict
        whose keys say where values came from, each mapped to a dict of the
        settings from there:

        - "command_line": maps "" to (None, the list of command line args).
        - "environment_variables": maps each env var name to
          (`argparse.Action` obj, the env var's value).
        - "config_file|<source>": one key per config file that set anything,
          eg. "config_file|/etc/app.conf", or "config_file|method arg" for
          config_file_contents passed to parse_args(). Maps each config file
          key to (`argparse.Action` obj, or None for a key that no arg has,
          the value as read, a str or a list).
        - "defaults": maps each arg's last option string (or dest, for a
          positional arg) to (`argparse.Action` obj, str() of its default).

        Returns:
            dict[str, dict[str, tuple]]: source to settings dict
        """
        # _source_to_settings is set in parse_know_args().
        return self._source_to_settings  # type: ignore[attribute-error]

    def _synthesized_args_dont_set_a_write_out_config_file_arg(
        self, synthesized_args, source_description, parsers
    ):
        """Reject synthesized command line args that set a write-out-config-file arg.

        Config file entries and environment variables are turned into command
        line args and then parsed like any others, so either one's key or its
        value can end up naming an arg. A write-out-config-file arg overwrites
        the path it is given and then exits the program, so only the real
        command line may set one: otherwise whoever controls a config file the
        program reads could destroy an arbitrary file without the user passing
        any args at all. (This is also why get_possible_config_keys() keeps
        these args out of the keys a config file can set.)

        These args are checked against every parser reachable from this one,
        not just the one that looks like it will parse them. Which subcommand
        runs can't be told from the args: an option's value reads exactly like
        a subcommand name, and the synthesized args can name a subcommand
        themselves. So a config file may not name a write-out arg anywhere in
        the parser tree, which costs an error on a key named after one on a
        subcommand that wasn't going to run.

        Args:
            synthesized_args: command line args built from a config file or from
                environment variables, before they are added to the real ones.
            source_description: where they came from, for the error message.
            parsers: the parsers that could end up parsing them: this parser
                and every subparser reachable from it (see _subparsers_of()).
                A subparser is given the args this parser has put together and
                treats them as its own command line, and argparse parses it into
                this parser's namespace, so both what a subparser accepts and
                what it stores matter here. Subparsers that aren't ConfigArgParse
                parsers are walked through as well, since one of them can hold a
                subparser that is.

        Returns:
            bool: whether the args may be used. error() is meant to stop the
            program, but a program can override exit(), so this says so rather
            than counting on the call not returning.
        """
        if not self._write_out_config_file_args(parsers):
            return True

        # any of these parsers could be the one to expand an arg that names a
        # file of args, and each has its own idea of which chars start one
        fromfile_prefix_chars = "".join(
            parser.fromfile_prefix_chars or ""
            for parser in parsers
            if self._write_out_config_file_args(_subparsers_of(parser))
        )

        for arg_string in synthesized_args:
            if self._names_a_write_out_config_file_arg(arg_string, parsers):
                self._report_error(
                    "%s can only be set on the command line, not %s"
                    % (arg_string.split("=", 1)[0], source_description)
                )
                return False
            if arg_string and arg_string[0] in fromfile_prefix_chars:
                # argparse replaces this with the args read from that file, and
                # it does so after this check, so the file's contents would go
                # unchecked and could name any arg at all
                self._report_error(
                    "%s can't be used %s, since it would read command line args "
                    "from a file" % (arg_string, source_description)
                )
                return False

        return True

    def _names_a_write_out_config_file_arg(self, arg_string, parsers):
        """Check whether a command line arg would set a write-out-config-file arg.

        Args:
            arg_string: a single command line arg.
            parsers: the parsers that could end up parsing it.

        Returns:
            bool: whether it names a write-out-config-file arg
        """
        return any(
            # a parser that isn't one of ours has no matcher of its own, and
            # this one's is close enough to decide against
            getattr(parser, "_could_set_option", self._could_set_option)(
                arg_string, option_string
            )
            for parser, action in self._write_out_config_file_args(parsers)
            for option_string in action.option_strings
        )

    def _write_out_config_file_args(self, parsers):
        """Find the write-out-config-file args the given parsers define.

        Args:
            parsers: the parsers to look in.

        Returns:
            list: each such arg as an ``(argparse.ArgumentParser,
            argparse.Action)`` pair, since it's the parser an arg belongs to
            that decides which command line args can set it.
        """
        return [
            (parser, action)
            for parser in parsers
            for action in parser._actions
            if getattr(action, "is_write_out_config_file_arg", False)
        ]

    def _could_set_option(self, arg_string, option_string):
        """Check whether a command line arg could set the given option.

        This covers every form argparse accepts: the option on its own, with its
        value attached after an ``=`` (or, for a short option, straight after
        it), bundled with other short options, and abbreviated. Where argparse
        would need to look at the rest of the command line to decide, this says
        yes, since it is used to reject args that must not reach an option at
        all, and being wrong in that direction only costs an error message.

        Args:
            arg_string: a single command line arg.
            option_string: an option string of the option to look for.

        Returns:
            bool: whether the arg could set the option
        """
        if arg_string == option_string:
            return True
        if arg_string == "--":
            # argparse's end-of-options separator, which it matches literally
            # rather than through prefix_chars, and never treats as an option
            return False
        if len(arg_string) < 2 or arg_string[0] not in self.prefix_chars:
            return False

        # argparse looks for an option matching the part before any '=' first,
        # whatever the option's prefix. Failing that, a long option is looked up
        # by that same part, while a short one keeps whatever follows it, since
        # a short option's value can be attached straight to it.
        is_long_option = arg_string[1] in self.prefix_chars
        bare_key = arg_string.split("=", 1)[0]
        key = bare_key if is_long_option else arg_string
        if bare_key == option_string or key == option_string:
            return True
        matched_action = self._option_string_actions.get(
            bare_key, self._option_string_actions.get(key)
        )
        if matched_action is not None and (
            is_long_option
            or "=" not in arg_string
            or getattr(matched_action, "nargs", None) != 0
        ):
            # argparse resolves an exact match straight to that option, so an
            # arg naming a different one can never reach this one. The exception
            # is a single prefix char naming an option that takes no value,
            # followed by '=': before Python 3.11 argparse goes on to read the
            # rest of that arg as more short options, so keep looking.
            return False

        if is_long_option:
            # only an unambiguous abbreviation can reach it from here
            return self.allow_abbrev and option_string.startswith(key)

        if option_string.startswith(bare_key):
            # argparse looks a single-prefix-char arg up by the part before any
            # '=' and takes any option starting with it, so even a lone prefix
            # char reaches an option when it is the only candidate. Before
            # Python 3.13 it does this whatever allow_abbrev says.
            return True

        if len(option_string) != 2:
            # an option with a single prefix char but a longer name is only
            # reachable whole or abbreviated, which the check above covers
            return False

        # a short option can also follow other short options that take no value
        # of their own, with its own value attached after it. When the part
        # before an '=' is itself a flag that takes no value, argparse before
        # 3.11.9 and 3.12.3 starts reading short options after the '=' instead.
        bundled = (
            arg_string.split("=", 1)[1]
            if matched_action is not None and "=" in arg_string
            else arg_string[1:]
        )
        index = bundled.find(option_string[1])
        if index == -1:
            return False

        return all(
            getattr(self._option_string_actions.get(arg_string[0] + c), "nargs", None)
            == 0
            for c in bundled[:index]
            if c != "="
        )

    def write_config_file(self, parsed_namespace, output_file_paths, exit_after=False):
        """Write the given settings to output files.

        Args:
            parsed_namespace: namespace object created within parse_known_args()
            output_file_paths: any number of file paths to write the config to
            exit_after: whether to exit the program after writing the config files
        """
        if output_file_paths:
            # generate the config file contents first. Opening an output file
            # truncates it, so nothing is opened until the contents that will
            # replace it exist. Each file is then opened exactly once, which
            # also avoids the gap between checking a path and writing to it.
            config_items = self.get_items_for_config_file_output(
                self._source_to_settings, parsed_namespace
            )
            file_contents = self._config_file_parser.serialize(config_items)
            for output_file_path in output_file_paths:
                try:
                    with self._config_file_open_func(
                        output_file_path, "w"
                    ) as output_file:
                        output_file.write(file_contents)
                except IOError as e:
                    raise ValueError(
                        "Couldn't open {} for writing: {}".format(output_file_path, e)
                    )

            # a write-out arg with type=pathlib.Path gives Path objects
            print("Wrote config file to " + ", ".join(map(str, output_file_paths)))
            if exit_after:
                self.exit(0)

    def get_command_line_key_for_unknown_config_file_setting(self, key):
        """Compute a commandline arg key to be used for a config file setting
        that doesn't correspond to any defined configargparse arg (and so
        doesn't have a user-specified commandline arg key).

        Args:
            key: The config file key that was being set.

        Returns:
            str: command line key
        """
        key_without_prefix_chars = key.strip(self.prefix_chars)
        command_line_key = self.prefix_chars[0] * 2 + key_without_prefix_chars

        return command_line_key

    def _get_value(self, action, arg_string):
        """Convert a string to a value for an action, as argparse does, and
        record the string the value came from.

        argparse converts each command line or env var string this way, and a
        string default or const too. A type= callable can make a value whose
        str() it can't read back, such as a dict from json.loads or the open
        file argparse.FileType makes, so a config file written out for that
        value holds the recorded string instead (see
        _replace_parsed_values_with_the_strings_they_came_from()).

        Args:
            action: the action the string is for.
            arg_string: the string.

        Returns:
            object: the value argparse made from it
        """
        value = argparse.ArgumentParser._get_value(self, action, arg_string)
        self._parsed_values_and_the_strings_they_came_from.append(
            (action, value, arg_string)
        )
        return value

    def _get_values(self, action, arg_strings):
        """Convert an action's args to its value, as argparse does, and record
        whether it was given any.

        Args:
            action: the action the args are for.
            arg_strings: the args argparse took for it.

        Returns:
            object: the value argparse made from them
        """
        values = argparse.ArgumentParser._get_values(self, action, arg_strings)
        # checked after argparse's _get_values(), since older versions of it
        # remove a '--' from arg_strings themselves
        if arg_strings:
            self._actions_given_values.add(action)
        return values

    def _replace_parsed_values_with_the_strings_they_came_from(self, action, value):
        """Put back the string a value came from where str() can't stand in for it.

        Only a value other than a str, int, float, bool or Enum (eg. a dict
        from type=json.loads, or a list from a type= callable that splits its
        string) is replaced, and only when it is the very object argparse made
        for this action from a string in the last parse: a command line or env
        var value, or a string default or const. So a value set any other way,
        such as a default that isn't a string, is kept.

        Args:
            action: the action the value was parsed for.
            value: a parsed arg value, or an element of a list value.

        Returns:
            object: the value, with each such value or list element replaced
        """
        if not isinstance(value, (str, int, float, enum.Enum)):
            for (
                parsed_action,
                parsed_value,
                value_string,
            ) in self._parsed_values_and_the_strings_they_came_from:
                if parsed_action is action and parsed_value is value:
                    return value_string
        if isinstance(value, list):
            replaced_elements = [
                self._replace_parsed_values_with_the_strings_they_came_from(
                    action, element
                )
                for element in value
            ]
            # the same list when nothing in it was replaced, since a flag's
            # value is told apart from its const (which can be a list) by
            # identity
            if all(new is old for new, old in zip(replaced_elements, value)):
                return value
            return replaced_elements
        return value

    def get_items_for_config_file_output(self, source_to_settings, parsed_namespace):
        """Converts the given settings back to a dictionary that can be passed
        to ConfigFileParser.serialize(..).

        Args:
            source_to_settings: the dictionary returned by
                get_source_to_settings_dict()
            parsed_namespace: namespace object created within parse_known_args()
        Returns:
            OrderedDict: where keys are strings and values are a str, int,
            float or bool, or a list whose elements are any of these or lists
            of them
        """
        config_file_items = OrderedDict()
        for source, settings in source_to_settings.items():
            if source.startswith(_CONFIG_FILE_SOURCE_KEY):
                # written back as they were read
                for key, (action, value) in settings.items():
                    config_file_items[key] = value
                continue

            if source == _COMMAND_LINE_SOURCE_KEY:
                _, existing_command_line_args = settings[""]
                actions = [
                    action
                    for action in self._actions
                    if not action.is_positional_arg
                    and already_on_command_line(
                        existing_command_line_args,
                        action.option_strings,
                        self.prefix_chars,
                        parser=self,
                    )
                ]
            else:
                # environment variables and defaults
                actions = [action for action, _ in settings.values()]

            for action in actions:
                if getattr(action, "is_config_file_arg", False):
                    # the written file already holds what the config files it
                    # names held, and read back, a config file key for this
                    # would set the arg without opening the file it names
                    continue
                if source == _DEFAULTS_SOURCE_KEY and any(
                    action in group._group_actions
                    for group in self._mutually_exclusive_groups
                ):
                    # read back next to the group member that was set, or to
                    # another member's default, it would be a conflict, and
                    # without it the default applies anyway
                    continue
                config_file_keys = self.get_possible_config_keys(action)
                value = getattr(parsed_namespace, action.dest, None)
                if (
                    isinstance(action, argparse._AppendAction)
                    and isinstance(action.default, list)
                    and action.default
                    and isinstance(value, list)
                    and value[: len(action.default)] == action.default
                ):
                    # argparse appends to a copy of the default, so writing the
                    # default too would add it a second time when read back
                    value = value[len(action.default) :] or None
                if (
                    isinstance(action, argparse._CountAction)
                    and isinstance(action.default, int)
                    and isinstance(value, int)
                ):
                    # likewise a count adds to its default
                    value -= action.default
                if not config_file_keys or value is None:
                    continue
                value = self._replace_parsed_values_with_the_strings_they_came_from(
                    action, value
                )
                if source == _DEFAULTS_SOURCE_KEY and not (
                    _default_can_be_written_so_it_reads_back(value)
                ):
                    # without it the default applies anyway when the file is
                    # read back
                    continue
                config_file_items[config_file_keys[0]] = (
                    _convert_parsed_value_to_config_file_value(action, value)
                )
        return config_file_items

    def convert_item_to_command_line_arg(self, action, key, value):
        """Converts a config file or env var key + value to a list of
        commandline args to append to the commandline.

        Args:
            action: The argparse Action object for this setting, or None if this
                config file setting doesn't correspond to any defined
                configargparse arg.
            key: string (config file key or env var name)
            value: parsed value of type string or list

        Returns:
            list[str]: args
        """
        args = []

        if action is None:
            command_line_key = (
                self.get_command_line_key_for_unknown_config_file_setting(key)
            )
        else:
            command_line_key = action.option_strings[-1]

        # handle boolean value
        if action is not None and isinstance(
            action, ACTION_TYPES_THAT_DONT_NEED_A_VALUE
        ):
            if not isinstance(value, str):
                self._report_error(
                    "%s must be set to a single value such as true or false, "
                    "not %r" % (key, value)
                )
                return []
            if value.lower() in ("true", "yes", "on", "1"):
                if not is_boolean_optional_action(action):
                    args.append(command_line_key)
                else:
                    args += self._get_args_that_set_boolean_optional_action(
                        action, key, True
                    )
            elif value.lower() in ("false", "no", "off", "0"):
                # append nothing when set to "false" / "no", except for a
                # BooleanOptionalAction, which has a --no- form to give
                if is_boolean_optional_action(action):
                    args += self._get_args_that_set_boolean_optional_action(
                        action, key, False
                    )
            elif isinstance(action, argparse._CountAction) and value.isdecimal():
                # For count actions, repeat the flag the number of times specified
                args += [action.option_strings[0]] * int(value)
            else:
                self._report_error(
                    "Unexpected value for %s: '%s'. Expecting 'true', "
                    "'false', 'yes', 'no', 'on', 'off', '1' or '0'%s"
                    % (
                        key,
                        value,
                        (
                            " or a whole number"
                            if isinstance(action, argparse._CountAction)
                            else ""
                        ),
                    )
                )
        elif isinstance(value, list):
            # Only nargs is relevant here: any action, including a custom
            # argparse.Action subclass, consumes several values when nargs
            # allows it. Testing the action class as well would be redundant,
            # since actions that cannot consume a list have an nargs that
            # fails the test below anyway: 0 or None for store_const, count
            # and friends, 'A...' for subparsers, '...' for REMAINDER.
            accepts_list_and_has_nargs = action is not None and (
                _action_takes_a_list_of_values(action)
            )

            # the list elements that go on the command line as args of their own,
            # after command_line_key, rather than as command_line_key=element
            separate_value_args = []
            if action is None or isinstance(action, argparse._AppendAction):
                for list_elem in value:
                    if accepts_list_and_has_nargs and isinstance(list_elem, list):
                        sub_elems = [str(sub_elem) for sub_elem in list_elem]
                        args.append(command_line_key)
                        args += sub_elems
                        separate_value_args += sub_elems
                    else:
                        args.append("%s=%s" % (command_line_key, str(list_elem)))
            elif accepts_list_and_has_nargs:
                separate_value_args = [str(list_elem) for list_elem in value]
                args.append(command_line_key)
                args += separate_value_args
            else:
                self._report_error(
                    (
                        "%s can't be set to a list '%s' unless its action type is changed "
                        "to 'append' or nargs is set to '*', '+', or a number"
                    )
                    % (key, value)
                )
            # argparse must read each of these as a value. One that it reads as
            # an option instead would set that option from a config file or env
            # var that isn't meant to set it (eg. "-c other.ini" would read
            # another config file), and one that names a file of args would be
            # replaced with that file's args, which could set any option. A bare
            # '--' would end option parsing, which turns the user's own command
            # line options after it into positional args and hides them from
            # the checks that let them override config file and env var values.
            # The error doesn't show the element, since it may be a secret.
            for arg in separate_value_args:
                problem = self._describe_why_argparse_wont_read_arg_as_a_value(arg)
                if problem:
                    self._report_error("%s can't have %s" % (key, problem))
                    return []
        elif isinstance(value, str):
            args.append("%s=%s" % (command_line_key, value))
        else:
            raise ValueError(
                "Unexpected value type {} for value: {}".format(type(value), value)
            )

        return args

    def _get_args_that_set_boolean_optional_action(self, action, key, value):
        """Picks the option string that sets a BooleanOptionalAction arg to the
        value given for it in a config file or env var.

        argparse adds a negative form after some option strings of a
        BooleanOptionalAction ('--no-foo' after '--foo', and from Python 3.15
        also '++no-foo' after '++foo' and '-nofoo' after '-foo'), and sets the
        arg to False for the negative forms and to True for the rest. A short
        alias can come first, so the negative forms aren't at a fixed position.
        A config file key that names a negative form (eg. 'no-feature' for
        '--no-feature') means the opposite value, so 'no-feature = true' sets
        the arg to False, just as '--no-feature' would on the command line.

        Args:
            action: The argparse.BooleanOptionalAction to set.
            key: The config file key or env var name the value came from.
            value: The boolean value given for that key.

        Returns:
            list: the option string to add to the command line, or no args if
            none of the option strings sets that value
        """
        # Python 3.15+ lists the negative forms. Before that, argparse sets the
        # arg to False for exactly the option strings that start with '--no-'.
        negative_option_strings = getattr(action, "neg_option_strings", None)
        if negative_option_strings is None:
            negative_option_strings = [
                option_string
                for option_string in action.option_strings
                if option_string.startswith("--no-")
            ]
        # the config keys get_possible_config_keys() gives a negative form are
        # the form itself and, when it starts with a doubled prefix char, the
        # form without that prefix (eg. '--no-foo' and 'no-foo')
        key_names_a_negative_option_string = any(
            key == option_string
            or (option_string[0] == option_string[1] and key == option_string[2:])
            for option_string in negative_option_strings
        )
        value_to_set = not value if key_names_a_negative_option_string else value
        option_strings_that_set_value = [
            option_string
            for option_string in action.option_strings
            if (option_string in negative_option_strings) != value_to_set
        ]
        if not option_strings_that_set_value:
            # eg. an arg with only short option strings has no negative form
            self._report_error(
                "%s can't set %s to %s: none of %s does"
                % (key, action.dest, value_to_set, "/".join(action.option_strings))
            )
            # in case exit() was overridden and returned
            return []
        return option_strings_that_set_value[:1]

    def _describe_why_argparse_wont_read_arg_as_a_value(self, arg):
        """Says why argparse may not read the given command line arg as a value.

        This is stricter than argparse, which also reads some other args that
        start with a prefix char as values, such as one that contains a space.

        Args:
            arg (str): a command line arg.

        Returns:
            str: what's wrong with it, worded to follow "<key> can't have", or
            None if argparse reads it as a value.
        """
        # argparse ends option parsing at '--' whatever the prefix chars are
        if arg == "--":
            return "'--' as a list element, since it would end option parsing"
        if not arg:
            return None
        if arg[0] in (self.fromfile_prefix_chars or ""):
            return (
                "a list element that starts with '%s', since it would read "
                "command line args from a file" % arg[0]
            )
        if not self._arg_is_read_as_an_option(arg):
            return None
        return (
            "a list element that starts with '%s', since argparse may read it as "
            "an option rather than as a value" % arg[0]
        )

    def get_possible_config_keys(self, action):
        """This method decides which actions can be set in a config file and
        what their keys will be. It returns a list of 0 or more config keys that
        can be used to set the given action's value in a config file.

        Returns:
            list[str]: keys
        """
        keys = []

        # Do not write out the config options for writing out a config file
        if getattr(action, "is_write_out_config_file_arg", None):
            return keys

        for arg in action.option_strings:
            if any(arg.startswith(2 * c) for c in self.prefix_chars):
                keys += [arg[2:], arg]  # eg. for '--bla' return ['bla', '--bla']

        return keys

    def _open_config_files(self, command_line_args):
        """Tries to parse config file path(s) from within command_line_args.
        Returns a list of opened config files, including files specified on the
        commandline as well as any default_config_files specified in the
        constructor that are present on disk.

        Args:
            command_line_args: List of all args

        Returns:
            list[tuple[io.IOBase, str]]: list of ``(stream, source_label)``
            pairs. The ``source_label`` is unique per entry (file path for
            path entries; ``"<entry_label>[<index>]"`` for callable entries),
            so different entries cannot collapse into a single source key in
            ``format_values()``.
        """
        # open any default config files
        config_files = []
        try:
            for i, entry in enumerate(self._default_config_files):
                if isinstance(entry, (str, bytes, os.PathLike)):
                    # Path entries (str, bytes or PathLike). Checked before
                    # callable so objects implementing both __fspath__ and
                    # __call__ are treated as paths, matching the documented
                    # behavior.
                    # sorted, since glob lists files in whatever order the
                    # filesystem does, and later files take precedence
                    for f in sorted(glob.glob(os.path.expanduser(os.fsdecode(entry)))):
                        config_files.append((self._config_file_open_func(f), f))
                else:
                    # Callable entry (validation in __init__ guarantees this).
                    entry_label = getattr(entry, "__name__", repr(entry))
                    try:
                        stream = entry()
                    except Exception as e:
                        raise ConfigFileParserException(
                            "default_config_files entry %r raised while being "
                            "called: %s" % (entry_label, e)
                        ) from e
                    if stream is None:
                        raise TypeError(
                            "default_config_files entry %r returned None; "
                            "must return an open file-like object." % (entry_label,)
                        )
                    # Use a source label that always includes the entry index
                    # so two callables returning streams with the same .name
                    # (or even the same library-generated name from a previous
                    # iteration) cannot collide into one _source_to_settings
                    # entry. The label also doubles as stream.name when the
                    # stream lacks one, for readable parser error messages.
                    display_name = getattr(stream, "name", None)
                    source_label = "%s[%d]" % (display_name or entry_label, i)
                    # Append before attempting .name so the outer cleanup
                    # closes the stream if the assignment below raises.
                    config_files.append((stream, source_label))
                    if not hasattr(stream, "name"):
                        try:
                            stream.name = source_label
                        except Exception as e:
                            raise ConfigFileParserException(
                                "default_config_files entry %r returned a "
                                "stream whose .name attribute could not be "
                                "set: %s" % (entry_label, e)
                            ) from e

            # list actions with is_config_file_arg=True. Its possible there is
            # more than one such arg.
            user_config_file_arg_actions = [
                a for a in self._actions if getattr(a, "is_config_file_arg", False)
            ]

            if not user_config_file_arg_actions:
                return config_files

            for action in user_config_file_arg_actions:
                # try to parse out the config file path by using a clean new
                # ArgumentParser that only knows this one arg/action.
                # with this parser's own setting for abbreviations, so it finds
                # the path where the real parse does (files of args were
                # already read in parse_known_args())
                arg_parser = argparse.ArgumentParser(
                    prefix_chars=self.prefix_chars,
                    add_help=False,
                    allow_abbrev=self.allow_abbrev,
                )

                # a copy, since _add_action() makes the parser it's added to the
                # action's container, which conflict_handler='resolve' later
                # removes it from
                arg_parser._add_action(copy.copy(action))
                # it also needs this parser's other options, taking the same
                # number of values, so that it splits a bundle of short flags
                # such as '-vc path', and resolves an option whose name is a
                # prefix of the config option's (eg. '--set' for '--settings'),
                # the way the real parse does
                for other_action in self._actions:
                    if other_action is action or not other_action.option_strings:
                        continue
                    if other_action.nargs == 0:
                        arg_parser.add_argument(
                            *other_action.option_strings,
                            action="store_true",
                            dest=argparse.SUPPRESS,
                        )
                    else:
                        arg_parser.add_argument(
                            *other_action.option_strings,
                            nargs=other_action.nargs,
                            dest=argparse.SUPPRESS,
                        )

                # make parser not exit on error by replacing its error method.
                # Otherwise it sys.exits(..) if, for example, config file
                # is_required=True and user doesn't provide it.
                def error_method(self, message):
                    pass

                arg_parser.error = types.MethodType(error_method, arg_parser)

                # check whether the user provided a value. Only the args before
                # any subcommand are this parser's: argparse hands the rest to
                # the subparser, which may use the same option string itself.
                parsed = arg_parser.parse_known_args(
                    args=command_line_args[
                        : self._find_end_of_this_parsers_args(command_line_args)
                    ]
                )
                # before Python 3.9, argparse returns None once error() returns.
                # The real parse that follows reports the error itself.
                if parsed is None:
                    continue
                user_config_file = getattr(parsed[0], action.dest, None)

                if not user_config_file:
                    continue

                # open user-provided config file
                user_config_file = os.path.expanduser(user_config_file)
                try:
                    stream = self._config_file_open_func(user_config_file)
                except Exception as e:
                    self._report_error(
                        "Unable to open config file: %s. Error: %s"
                        % (user_config_file, str(e))
                    )
                    # in case exit() was overridden and returned
                    continue

                config_files.append((stream, user_config_file))

            return config_files
        except BaseException:
            # If anything in the body above raises (callable failure, .name
            # assignment failure, glob/open failure, an inner argparse type=
            # callback raising during user-config-file parsing, self._report_error()
            # exiting, etc.), close every stream we opened so we don't leak
            # file handles. close() is idempotent for standard streams, so it
            # is safe to call on streams already closed by a nested handler.
            _close_config_streams(config_files)
            raise

    def format_values(self):
        """Returns a string with all args and settings and where they came from
        (eg. commandline, config file, environment variable or default)

        Returns:
            str: source to settings string
        """
        source_key_to_display_value_map = {
            _COMMAND_LINE_SOURCE_KEY: "Command Line Args: ",
            _ENV_VAR_SOURCE_KEY: "Environment Variables:\n",
            _CONFIG_FILE_SOURCE_KEY: "Config File (%s):\n",
            _DEFAULTS_SOURCE_KEY: "Defaults:\n",
        }

        r = StringIO()
        for (
            source,
            settings,
        ) in self._source_to_settings.items():  # type: ignore[argument-error]
            source = source.split("|", 1)
            source = source_key_to_display_value_map[source[0]] % tuple(source[1:])
            r.write(source)
            for key, (action, value) in settings.items():
                if key:
                    r.write("  {:<19}{}\n".format(key + ":", value))
                else:
                    if isinstance(value, str):
                        r.write("  %s\n" % value)
                    elif isinstance(value, list):
                        r.write("  %s\n" % " ".join(value))

        return r.getvalue()

    def print_values(self, file=None):
        """Prints the format_values() string (to sys.stdout or another file)."""
        (file if file is not None else sys.stdout).write(self.format_values())

    def format_help(self, formatter=None):
        self._fill_in_action_attributes_this_module_reads()
        msg = ""
        added_config_file_help = False
        added_env_var_help = False
        if self._add_config_file_help:
            default_config_files = self._default_config_files
            cc = 2 * self.prefix_chars[0]  # eg. --
            has_config_settable_args = any(
                self.get_possible_config_keys(a)
                and not (
                    a.dest == "help"
                    or a.is_config_file_arg
                    or a.is_write_out_config_file_arg
                )
                for a in self._actions
            )
            config_path_actions = [
                a for a in self._actions if getattr(a, "is_config_file_arg", False)
            ]

            if has_config_settable_args and (
                default_config_files or config_path_actions
            ):
                added_config_file_help = True

                msg += (
                    "Args that start with '%s' can also be set in a config file"
                ) % cc
                config_arg_string = " or ".join(
                    a.option_strings[0] for a in config_path_actions if a.option_strings
                )
                if config_arg_string:
                    config_arg_string = "specified via " + config_arg_string
                if default_config_files or config_arg_string:
                    # Mirror _open_config_files: path entries (str/bytes/PathLike)
                    # are checked before callable, so an object that is both
                    # PathLike and callable is rendered as a path here too.
                    # os.fsdecode() handles bytes-returning __fspath__ (PEP
                    # 519 allows it); the try/except handles malformed
                    # PathLike whose __fspath__ returns the wrong type.
                    def _describe_default_config_file_entry(e):
                        if isinstance(e, (str, bytes, os.PathLike)):
                            try:
                                return os.fsdecode(os.fspath(e))
                            except (TypeError, ValueError):
                                return repr(e)
                        return getattr(e, "__name__", "<callable>")

                    described_files = tuple(
                        _describe_default_config_file_entry(e)
                        for e in default_config_files
                    )
                    msg += " (%s)." % " or ".join(
                        described_files + tuple(filter(None, [config_arg_string]))
                    )
                msg += " " + self._config_file_parser.get_syntax_description()

        if self._add_env_var_help:
            env_var_actions = [
                (a.env_var, a) for a in self._actions if getattr(a, "env_var", None)
            ]
            for env_var, a in env_var_actions:
                if a.help == SUPPRESS:
                    continue
                env_var_help_string = "   [env var: %s]" % env_var
                if not a.help:
                    a.help = ""
                # format_help() can run more than once, so add the env var to an
                # arg's help only the first time
                if env_var_help_string not in a.help:
                    a.help += env_var_help_string
                added_env_var_help = True

        if added_env_var_help or added_config_file_help:
            value_sources = ["defaults"]
            if added_config_file_help:
                value_sources = ["config file values"] + value_sources
            if added_env_var_help:
                value_sources = ["environment variables"] + value_sources
            msg += " In general, command-line values override %s." % (
                " which override ".join(value_sources)
            )

        text_width = max(self._get_formatter()._width, 11)
        msg = textwrap.fill(msg, text_width)

        # Python 3.15+ print_help() passes a formatter that knows which file the
        # help goes to, so that color is only used where that file supports it.
        # Earlier versions' format_help() takes no formatter.
        if formatter is None:
            help_text = argparse.ArgumentParser.format_help(self)
        else:
            help_text = argparse.ArgumentParser.format_help(self, formatter=formatter)
        return help_text + ("\n{}\n".format(msg) if msg != "" else "")


def add_argument(self, *args, **kwargs):
    """
    This method supports the same args as ArgumentParser.add_argument(..)
    as well as the additional args below.

    Keyword Arguments:
        env_var: If set, the value of this environment variable will override
            any config file or default values for this arg (but can itself
            be overridden on the commandline). Also, if auto_env_var_prefix is
            set in the constructor, this env var name will be used instead of
            the automatic name.
        is_config_file_arg: If True, this arg is treated as a config file path
            This provides an alternative way to specify config files in place of
            the ArgumentParser(fromfile_prefix_chars=..) mechanism.
            Default: False
        is_write_out_config_file_arg: If True, this arg will be treated as a
            config file path, and, when it is specified on the command line,
            will cause configargparse to write all current commandline args to
            this file as config options and then exit. It can only be set on the
            command line: a config file key that names it is an error, and it
            can't be combined with env_var, nor be a positional arg, since a
            config file value would be indistinguishable from a path the user
            typed and could overwrite an arbitrary file.
            Default: False

    Returns:
        argparse.Action: the new argparse action
    """

    env_var = kwargs.pop("env_var", None)

    is_config_file_arg = kwargs.pop("is_config_file_arg", None) or kwargs.pop(
        "is_config_file", None
    )  # for backward compat.

    is_write_out_config_file_arg = kwargs.pop("is_write_out_config_file_arg", None)

    action = self.original_add_argument_method(*args, **kwargs)

    if is_write_out_config_file_arg and isinstance(action, argparse._StoreAction):
        # this arg has to record the path it is given as it is parsed, rather
        # than leave it to be read back out of the namespace later. Whatever
        # store action the program asked for keeps doing its own work.
        action.__class__ = type(
            type(action).__name__,
            (_WriteOutConfigFileActionMixin, type(action)),
            {},
        )

    action.is_positional_arg = not action.option_strings
    action.env_var = env_var
    action.is_config_file_arg = is_config_file_arg
    action.is_write_out_config_file_arg = is_write_out_config_file_arg

    if action.is_positional_arg and env_var:
        raise ValueError("env_var can't be set for a positional arg.")
    if action.is_config_file_arg and not isinstance(action, argparse._StoreAction):
        raise ValueError("arg with is_config_file_arg=True must have action='store'")
    if action.is_write_out_config_file_arg:
        error_prefix = "arg with is_write_out_config_file_arg=True "
        if not isinstance(action, _WriteOutConfigFileActionMixin):
            raise ValueError(error_prefix + "must have action='store'")
        if is_config_file_arg:
            raise ValueError(error_prefix + "can't also have is_config_file_arg=True")
        if env_var:
            # writing out a config file overwrites the given path and then
            # exits, so an env var that sets one could destroy an arbitrary file
            raise ValueError(error_prefix + "can't also have env_var set")
        if action.is_positional_arg:
            # a config file value becomes a plain command line arg, and a
            # positional would take one of those just as readily as one the user
            # typed, leaving no way to keep a config file from choosing the path
            # that gets overwritten
            raise ValueError(error_prefix + "can't be a positional arg")

    return action


def _subparsers_of(parser, seen=None):
    """Find a parser and every subparser reachable from it.

    Args:
        parser: the parser to start from.
        seen: ids of the parsers already visited, so that a subparser leading
            back to one of its own ancestors doesn't loop forever.

    Returns:
        list[argparse.ArgumentParser]: the parser and its subparsers
    """
    if seen is None:
        seen = set()
    if id(parser) in seen:
        return []
    seen.add(id(parser))

    parsers = [parser]
    for action in getattr(parser, "_actions", []):
        if _dispatches_to_subparsers(action):
            for subparser in action.choices.values():
                if isinstance(subparser, argparse.ArgumentParser):
                    parsers += _subparsers_of(subparser, seen)

    return parsers


def _dispatches_to_subparsers(action):
    """Check whether an action hands the rest of the command line to a subparser.

    Nearly always this is an ``argparse._SubParsersAction``, but a program can
    register a class of its own for the job, so recognize the shape too: a
    mapping of subcommand names to parsers, taking the rest of the args.

    Args:
        action: the action to check.

    Returns:
        bool: whether it dispatches to subparsers
    """
    if isinstance(action, argparse._SubParsersAction):
        return True

    return getattr(action, "nargs", None) in (
        argparse.PARSER,
        argparse.REMAINDER,
    ) and isinstance(getattr(action, "choices", None), dict)


def _is_variable_length_list(action, value):
    """Check whether a value is a list for an option that takes any number of values.

    Args:
        action: the action the value is for, or None for an unknown key.
        value: the value from a config file or env var.

    Returns:
        bool: whether argparse would read the args after it as more values
    """
    if not (
        action is not None
        and isinstance(value, list)
        and action.nargs in (argparse.ONE_OR_MORE, argparse.ZERO_OR_MORE)
    ):
        return False
    if isinstance(action, argparse._AppendAction):
        # convert_item_to_command_line_arg() writes each element that isn't
        # itself a list as '--key=element', which takes no more args after it
        return bool(value) and isinstance(value[-1], list)
    return True


def _close_config_streams(streams_and_source_labels):
    """Close each config file stream, ignoring any error closing one raises.

    Args:
        streams_and_source_labels: (stream, source label) pairs.
    """
    for stream, _ in streams_and_source_labels:
        try:
            if hasattr(stream, "close"):
                stream.close()
        except Exception:
            pass


def _parse_bracketed_list_value(value):
    """Turns a config file or env var value written as a list into a list.

    Args:
        value: the value as written, eg. '["a", "b"]' or '[a, b]'.

    Returns:
        the list, or the value unchanged if it isn't in square brackets
    """
    if not (value.startswith("[") and value.endswith("]")):
        return value
    try:
        return json.loads(value)
    except Exception:
        # for backward compatibility with the legacy format (eg. [a, b, c]
        # instead of proper json ["a", "b", "c"])
        return [elem.strip() for elem in value[1:-1].split(",")]


def _default_can_be_written_so_it_reads_back(value):
    """Check whether a default can be written to a config file and read back.

    It can when it is a str, int, float, bool, Enum or path, or a list or tuple
    of these that isn't empty. Read back, an empty list sets an option to no
    values, which argparse rejects for nargs='+', and str() of other objects,
    such as the open file in a default of sys.stdout, isn't anything the
    option's type= can take.

    Args:
        value: the default, after any string it was converted from has been
            put back.

    Returns:
        bool: whether it can
    """
    if isinstance(value, (list, tuple)):
        return bool(value) and all(
            _default_can_be_written_so_it_reads_back(element) for element in value
        )
    return isinstance(value, (str, int, float, enum.Enum, os.PathLike))


def _action_takes_a_list_of_values(action):
    """Check whether argparse stores a list of values for an action.

    It does when nargs is '*', '+' or a number, even 1.

    Args:
        action: an argparse action.

    Returns:
        bool: whether it does
    """
    return action.nargs in ("+", "*") or (
        isinstance(action.nargs, int) and action.nargs >= 1
    )


def _convert_parsed_value_to_config_file_value(action, value):
    """Convert a parsed arg value to the value a config file should hold for it.

    For a flag (``store_true``, ``store_false``, ``store_const`` or
    ``append_const``), the config file value says whether to invoke the flag,
    not what it stores, so it is ``"true"`` exactly when the parsed value is
    (or, for ``append_const``, holds) the one the flag stores. Other booleans
    are written in lowercase too, so values from the command line and from
    environment variables are written the same way. A tuple for an option that
    takes a list of values, such as a default of ``(640, 480)`` for
    ``nargs=2``, is written as a list.

    Values of other types, such as a ``pathlib.Path`` default, are written as
    ``str()``, since every config file format can write a string and read it
    back. (Such a value that argparse made from a string has already been
    replaced with that string, see
    ``ArgumentParser._replace_parsed_values_with_the_strings_they_came_from()``.)

    Args:
        action: the action the value was parsed for.
        value: the value the parser stored for the action.

    Returns:
        object: ``"true"`` or ``"false"`` for flags and booleans, otherwise the
        value with anything that isn't a str, int, float, bool or list
        converted to a str.
    """
    if isinstance(action, argparse._StoreConstAction):
        # "is" rather than "==": a store_false default of 0 equals False
        value = value is action.const
    elif isinstance(action, argparse._AppendConstAction):
        # the dest list holds the consts of every append_const arg sharing it
        value = any(element is action.const for element in value)
    elif isinstance(value, tuple) and _action_takes_a_list_of_values(action):
        # eg. a default of (640, 480) for nargs=2, which str() would write as
        # one value
        value = list(value)
    if isinstance(value, bool):
        return str(value).lower()
    return _convert_to_types_every_config_file_parser_can_write(
        _replace_enum_members_its_type_reads_by_name_with_their_names(action, value)
    )


def _replace_enum_members_its_type_reads_by_name_with_their_names(action, value):
    """Write an Enum member as its name where the action's type= needs that.

    An Enum member is otherwise written as its value (see
    _convert_to_types_every_config_file_parser_can_write()), which suits
    ``type=SomeEnum``, but not a lookup by name such as
    ``type=lambda s: SomeEnum[s]``. So a member that the type= callable doesn't
    read back from its value, but does from its name, is replaced with its name.

    Args:
        action: the action the value was parsed for.
        value: a parsed arg value, or an element of a list value.

    Returns:
        object: the value, with each such member replaced by its name
    """
    if isinstance(value, list):
        return [
            _replace_enum_members_its_type_reads_by_name_with_their_names(
                action, element
            )
            for element in value
        ]
    if not isinstance(value, enum.Enum) or not callable(action.type):
        return value

    def type_reads_back_the_member_from(value_string):
        try:
            return action.type(value_string) is value
        except Exception:
            return False

    if not type_reads_back_the_member_from(
        str(value.value)
    ) and type_reads_back_the_member_from(value.name):
        return value.name
    return value


def _convert_to_types_every_config_file_parser_can_write(value):
    """Convert a value to a plain str, int, float, bool or list of those.

    An Enum member is written as its value, which is what a type= callable like
    ``type=SomeEnum`` takes back (one that takes its name instead has already
    been given the name, see
    _replace_enum_members_its_type_reads_by_name_with_their_names()). Some
    config file writers only handle the built-in types
    themselves, so a subclass of one (eg. a str or int Enum's value, or an
    IntEnum) becomes that built-in type, and anything else becomes a str.

    Args:
        value: a parsed arg value, or an element of a list value.

    Returns:
        object: the value, with each list element converted the same way
    """
    if isinstance(value, list):
        return [
            _convert_to_types_every_config_file_parser_can_write(element)
            for element in value
        ]
    if type(value) in (str, int, float, bool):
        return value
    if isinstance(value, enum.Enum):
        return _convert_to_types_every_config_file_parser_can_write(value.value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str.__str__(value)
    return str(value)


def already_on_command_line(
    existing_args_list, potential_command_line_args, prefix_chars, parser=None
):
    """Utility method for checking if any of the potential_command_line_args is
    already present in existing_args.

    Args:
        existing_args_list: the command line args.
        potential_command_line_args: the option strings to look for.
        prefix_chars: the chars that start an option.
        parser: the ArgumentParser the args are for. When given, only the args
            that parser reads itself are looked at (not those after a
            subcommand, which argparse hands to the subparser, nor those after
            '--' or that a REMAINDER positional or option takes), and every form argparse
            accepts is recognized, such as '-vv' for '-v' or '-Ifoo' for '-I'.
            Otherwise only an option string on its own or followed by '=value'
            is, in the args before any '--'.

    Returns:
        bool: already on command line?
    """
    if parser is not None:
        return any(
            parser._could_set_option(arg_string, option_string)
            for arg_string in existing_args_list[
                : parser._find_end_of_this_parsers_args(existing_args_list)
            ]
            for option_string in potential_command_line_args
        )
    for arg_string in existing_args_list:
        if arg_string == "--":
            return False
        if arg_string and arg_string[0] in prefix_chars and "=" in arg_string:
            arg_string = arg_string.split("=", 1)[0]
        if arg_string in potential_command_line_args:
            return True
    return False


# NOTE: Aliases below are not auto-documented. This could be improved by updating
# to latest version of pydoctor when https://github.com/twisted/pydoctor/pull/414
# has been merged, which would allow aliases to be documented automatically.

# wrap ArgumentParser's add_argument(..) method with the one above
argparse._ActionsContainer.original_add_argument_method = (
    argparse._ActionsContainer.add_argument
)
argparse._ActionsContainer.add_argument = add_argument


# add all public classes and constants from argparse module's namespace to this
# module's namespace so that the 2 modules are truly interchangeable
Action = argparse.Action
ArgumentDefaultsHelpFormatter = argparse.ArgumentDefaultsHelpFormatter
ArgumentError = argparse.ArgumentError
ArgumentTypeError = argparse.ArgumentTypeError
FileType = argparse.FileType
HelpFormatter = argparse.HelpFormatter
MetavarTypeHelpFormatter = argparse.MetavarTypeHelpFormatter
Namespace = argparse.Namespace
RawDescriptionHelpFormatter = argparse.RawDescriptionHelpFormatter
RawTextHelpFormatter = argparse.RawTextHelpFormatter
ONE_OR_MORE = argparse.ONE_OR_MORE
OPTIONAL = argparse.OPTIONAL
PARSER = argparse.PARSER
REMAINDER = argparse.REMAINDER
SUPPRESS = argparse.SUPPRESS
ZERO_OR_MORE = argparse.ZERO_OR_MORE


# deprecated PEP-8 incompatible API names.
initArgumentParser = init_argument_parser
getArgumentParser = get_argument_parser
getArgParser = get_argument_parser
getParser = get_argument_parser

# create shorter aliases for the key methods and class names
get_arg_parser = get_argument_parser
get_parser = get_argument_parser

ArgParser = ArgumentParser
Parser = ArgumentParser

argparse._ActionsContainer.add_arg = argparse._ActionsContainer.add_argument
argparse._ActionsContainer.add = argparse._ActionsContainer.add_argument

ArgumentParser.parse = ArgumentParser.parse_args
ArgumentParser.parse_known = ArgumentParser.parse_known_args

RawFormatter = RawDescriptionHelpFormatter
DefaultsFormatter = ArgumentDefaultsHelpFormatter
DefaultsRawFormatter = ArgumentDefaultsRawHelpFormatter
