"""Help handling follows the actions registered for each parser scope."""

import argparse

import pytest

import configargparse


def parser_with_config(tmp_path, prefix="-", add_help=True, valid=True, **kwargs):
    config = tmp_path / "settings.ini"
    config.write_text("[settings]\nport=443\n" if valid else "not an INI section\n")
    parser = configargparse.ArgumentParser(
        prefix_chars=prefix,
        add_help=add_help,
        default_config_files=[str(config)],
        config_file_parser_class=configargparse.ConfigparserConfigFileParser,
        **kwargs
    )
    parser.add_argument(prefix[0] * 2 + "port", type=int, default=80)
    return parser


@pytest.mark.parametrize("option", ["-h", "--help"])
@pytest.mark.parametrize("custom_help", [False, True])
def test_regular_traditional_spellings_do_not_skip_config(
    tmp_path, option, custom_help
):
    parser = parser_with_config(tmp_path, add_help=False)
    parser.add_argument(option, dest="host")
    if custom_help:
        parser.add_argument("--usage", action="help")

    args = parser.parse_args([option, "example.org"], env_vars={})

    assert args.host == "example.org"
    assert args.port == 443


@pytest.mark.parametrize("option", ["-h", "--help"])
@pytest.mark.parametrize("custom_help", [False, True])
def test_ignoring_help_keeps_regular_options(tmp_path, option, custom_help):
    parser = parser_with_config(tmp_path, add_help=False)
    parser.add_argument(option, dest="host")
    if custom_help:
        parser.add_argument("--usage", action="help")

    args, unknown = parser.parse_known_args(
        [option, "example.org"], ignore_help_args=True, env_vars={}
    )

    assert args.host == "example.org"
    assert args.port == 443
    assert unknown == []


HELP_CASES = [
    ("-", True, None, "-h"),
    ("-", True, None, "--help"),
    ("+", True, None, "+h"),
    ("+", True, None, "++help"),
    ("/", True, None, "//help"),
    ("-", False, ("--usage",), "--usage"),
    ("-", False, ("-?", "--usage"), "-?"),
    ("-", False, ("-?", "--usage"), "--usage"),
]


@pytest.mark.parametrize("prefix, add_help, aliases, option", HELP_CASES)
def test_registered_help_bypasses_bad_default_config(
    tmp_path, capsys, prefix, add_help, aliases, option
):
    parser = parser_with_config(tmp_path, prefix, add_help, valid=False)
    if aliases:
        parser.add_argument(*aliases, action="help")

    with pytest.raises(SystemExit) as caught:
        parser.parse_args([option], env_vars={})

    assert caught.value.code == 0
    output = capsys.readouterr()
    assert "usage:" in output.out
    assert output.err == ""


@pytest.mark.parametrize("prefix, add_help, aliases, option", HELP_CASES)
def test_ignoring_registered_help_still_loads_config(
    tmp_path, prefix, add_help, aliases, option
):
    parser = parser_with_config(tmp_path, prefix, add_help)
    if aliases:
        parser.add_argument(*aliases, action="help")

    args, unknown = parser.parse_known_args(
        [option], ignore_help_args=True, env_vars={}
    )

    assert args.port == 443
    assert unknown == []


@pytest.mark.parametrize("ignore_help", [False, True])
def test_resolved_short_help_alias_belongs_to_new_action(tmp_path, ignore_help):
    parser = parser_with_config(tmp_path, conflict_handler="resolve")
    parser.add_argument("-h", dest="host")

    args, unknown = parser.parse_known_args(
        ["-h", "example.org"], ignore_help_args=ignore_help, env_vars={}
    )

    assert args.host == "example.org"
    assert args.port == 443
    assert unknown == []


@pytest.mark.parametrize("ignore_help", [False, True])
@pytest.mark.parametrize("kind", ["separator", "positional", "option"])
def test_help_spellings_used_as_data_are_preserved(tmp_path, ignore_help, kind):
    parser = parser_with_config(tmp_path, add_help=False)
    parser.add_argument("--usage", action="help")
    if kind == "separator":
        parser.add_argument("values", nargs="*")
        arguments = ["--", "--usage"]
        expected = ["--usage"]
    elif kind == "positional":
        parser.add_argument("values", nargs=argparse.REMAINDER)
        arguments = ["start", "--usage"]
        expected = arguments
    else:
        parser.add_argument("--values", nargs=argparse.REMAINDER)
        arguments = ["--values", "--usage"]
        expected = ["--usage"]

    args, unknown = parser.parse_known_args(
        arguments, ignore_help_args=ignore_help, env_vars={}
    )

    assert args.values == expected
    assert args.port == 443
    assert unknown == []


def test_inactive_child_help_does_not_consume_active_child_option(tmp_path):
    parser = parser_with_config(tmp_path)
    children = parser.add_subparsers()
    active = children.add_parser("run", add_help=False)
    active.add_argument("--assist")
    inactive = children.add_parser("other", add_help=False)
    inactive.add_argument("--assist", action="help")

    args, unknown = parser.parse_known_args(
        ["run", "--assist", "literal"], ignore_help_args=True, env_vars={}
    )

    assert args.assist == "literal"
    assert args.port == 443
    assert unknown == []


@pytest.mark.parametrize("parent_help", [False, True])
def test_plain_argparse_child_keeps_legacy_parent_help_gate(
    tmp_path, capsys, parent_help
):
    parser = parser_with_config(tmp_path, add_help=False, valid=False)
    if parent_help:
        parser.add_argument("--usage", action="help")
    parser.add_subparsers(parser_class=argparse.ArgumentParser).add_parser("run")

    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["run", "-h"], env_vars={})

    assert caught.value.code == (0 if parent_help else 2)
    capsys.readouterr()


def test_plain_argparse_child_keeps_legacy_ignore_behavior(tmp_path):
    parser = parser_with_config(tmp_path)
    children = parser.add_subparsers(parser_class=argparse.ArgumentParser)
    child = children.add_parser("run")
    child.add_argument("values", nargs="*")

    args, unknown = parser.parse_known_args(
        ["run", "-h", "--", "--help"], ignore_help_args=True, env_vars={}
    )

    assert args.values == ["--help"]
    assert args.port == 443
    assert unknown == []


@pytest.mark.parametrize("ignore_help", [False, True])
@pytest.mark.parametrize("leading", [None, 1, "*", "+"])
@pytest.mark.parametrize("kind", ["positional", "option"])
def test_raw_parser_payload_keeps_custom_help_spelling(ignore_help, leading, kind):
    parser = configargparse.ArgumentParser(add_help=False)
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--usage", action="help")
    arguments = []
    if leading is not None:
        parser.add_argument("before", nargs=leading)
        arguments.append("before")
    if kind == "option":
        parser.add_argument("--payload", nargs=argparse.PARSER)
        arguments.append("--payload")
    else:
        parser.add_argument("payload", nargs=argparse.PARSER)
    arguments += ["start", "--usage"]

    args, unknown = parser.parse_known_args(
        arguments, ignore_help_args=ignore_help, env_vars={}
    )

    assert args.payload == ["start", "--usage"]
    if leading is not None:
        assert args.before == ["before"]
    assert args.port == 80
    assert unknown == []


@pytest.mark.parametrize("layout", ["leading-positional", "bundled-option"])
def test_subparser_scope_keeps_legacy_help_filter(layout):
    parser = configargparse.ArgumentParser()
    if layout == "leading-positional":
        parser.add_argument("prefix")
        arguments = ["run", "other", "-h"]
    else:
        parser.add_argument("-v", action="store_true")
        parser.add_argument("-p")
        arguments = ["-vp", "run", "other", "-h"]
    children = parser.add_subparsers(dest="command")
    child = children.add_parser("run", add_help=False)
    child.add_argument("-h", dest="host")
    children.add_parser("other")

    args, unknown = parser.parse_known_args(
        arguments, ignore_help_args=True, env_vars={}
    )

    assert args.command == "other"
    if layout == "leading-positional":
        assert args.prefix == "run"
    else:
        assert args.v is True
        assert args.p == "run"
    assert unknown == []


@pytest.mark.parametrize("action", ["store", "append", "extend"])
def test_parser_choices_do_not_imply_subcommand_dispatch(action):
    if (
        action == "extend"
        and "extend" not in argparse.ArgumentParser()._registries["action"]
    ):
        pytest.skip("extend requires Python 3.8")
    parser = configargparse.ArgumentParser(add_help=False)
    parser.add_argument("--usage", action="help")
    child = configargparse.ArgumentParser(add_help=False)
    child.add_argument("--usage", action="help")
    parser.add_argument(
        "payload", nargs=argparse.PARSER, action=action, choices={"run": child}
    )

    args, unknown = parser.parse_known_args(
        ["run", "--usage"], ignore_help_args=True, env_vars={}
    )

    expected = [["run", "--usage"]] if action == "append" else ["run", "--usage"]
    assert args.payload == expected
    assert unknown == []


@pytest.mark.parametrize("layout", ["cluster", "attached-value", "equals-value"])
def test_nonexact_short_layout_keeps_custom_help_payload(layout):
    parser = configargparse.ArgumentParser(add_help=False)
    parser.add_argument("--usage", action="help")
    parser.add_argument("-v", action="store_true")
    parser.add_argument("-p")
    parser.add_argument("-R", nargs=argparse.REMAINDER)
    if layout == "cluster":
        arguments = ["-vR", "start", "--usage"]
        expected = ["start", "--usage"]
    else:
        arguments = [
            "-pvalue" if layout == "attached-value" else "-p=value",
            "-R",
            "start",
            "--usage",
        ]
        expected = ["start", "--usage"]

    args, unknown = parser.parse_known_args(
        arguments, ignore_help_args=True, env_vars={}
    )

    assert args.R == expected
    if layout == "cluster":
        assert args.v is True
    else:
        assert args.p == "value"
    assert unknown == []


@pytest.mark.parametrize(
    "option, registered", [("--help", "--help-all"), ("-h", "-help")]
)
def test_legacy_literal_help_abbreviation_bypasses_bad_config(
    tmp_path, capsys, option, registered
):
    parser = parser_with_config(tmp_path, add_help=False, valid=False)
    parser.add_argument(registered, action="help")

    with pytest.raises(SystemExit) as caught:
        parser.parse_args([option], env_vars={})

    assert caught.value.code == 0
    output = capsys.readouterr()
    assert "usage:" in output.out
    assert output.err == ""


@pytest.mark.parametrize("option", ["-h", "--help"])
@pytest.mark.parametrize("prefix", ["-", "+"])
def test_unregistered_traditional_help_keeps_legacy_ignore(tmp_path, option, prefix):
    parser = parser_with_config(tmp_path, prefix=prefix, add_help=False)
    parser.add_argument(prefix * 2 + "usage", action="help")

    args, unknown = parser.parse_known_args(
        [option], ignore_help_args=True, env_vars={}
    )

    assert args.port == 443
    assert unknown == []
