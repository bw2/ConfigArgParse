"""Config and environment settings preserve bundled short-option values."""

import sys

import pytest

import configargparse


def make_parser(tmp_path, monkeypatch, source, prefix="-", **kwargs):
    variable = "CONFIGARGPARSE_BUNDLE_SETTING"
    monkeypatch.delenv(variable, raising=False)
    if source == "config":
        config = tmp_path / "settings.conf"
        config.write_text("setting = configured\n")
        kwargs["default_config_files"] = [str(config)]
    else:
        monkeypatch.setenv(variable, "configured")
    parser = configargparse.ArgumentParser(prefix_chars=prefix, **kwargs)
    parser.add_argument(prefix * 2 + "setting", env_var=variable)
    parser.add_argument(prefix + "v", action="count", default=0)
    return parser


@pytest.mark.parametrize("source", ["config", "env"])
@pytest.mark.parametrize(
    "arguments, nargs, output, verbosity",
    [
        (["-vo", "run", "run"], None, "run", 1),
        (["-vvvo", "run", "run"], None, "run", 3),
        (["-vo", "run", "run"], "?", "run", 1),
        (["-vo", "run", "out", "run"], 2, ["run", "out"], 1),
        (["-vo", "run", "out", "--flag", "run"], "+", ["run", "out"], 1),
    ],
)
def test_bundled_option_values_are_not_subcommands(
    tmp_path, monkeypatch, source, arguments, nargs, output, verbosity
):
    parser = make_parser(tmp_path, monkeypatch, source)
    parser.add_argument("-o", "--output", nargs=nargs)
    parser.add_argument("--flag", action="store_true")
    parser.add_subparsers(dest="command").add_parser("run")

    args = parser.parse_args(arguments)

    assert args.setting == "configured"
    assert args.output == output
    assert args.v == verbosity
    assert args.command == "run"


@pytest.mark.parametrize("source", ["config", "env"])
@pytest.mark.parametrize(
    "arguments, output",
    [
        (["-vorun", "run"], "run"),
        (["-vo=run", "run"], "run"),
        (["-vo=", "run"], ""),
        (["-v", "-o", "run", "run"], "run"),
    ],
)
def test_attached_values_and_separate_options_keep_the_subcommand(
    tmp_path, monkeypatch, source, arguments, output
):
    parser = make_parser(tmp_path, monkeypatch, source)
    parser.add_argument("-o", "--output")
    parser.add_subparsers(dest="command").add_parser("run")

    args = parser.parse_args(arguments)

    assert vars(args) == dict(setting="configured", v=1, output=output, command="run")


@pytest.mark.parametrize("source", ["config", "env"])
def test_exact_registered_option_takes_precedence(tmp_path, monkeypatch, source):
    parser = make_parser(tmp_path, monkeypatch, source)
    parser.add_argument("-o")
    parser.add_argument("-vo", action="store_true")
    parser.add_subparsers(dest="command").add_parser("run")

    args = parser.parse_args(["-vo", "run"])

    assert vars(args) == dict(setting="configured", v=0, o=None, vo=True, command="run")


@pytest.mark.parametrize("source", ["config", "env"])
@pytest.mark.parametrize(
    "allow_abbrev",
    [
        True,
        pytest.param(
            False,
            marks=pytest.mark.skipif(
                sys.version_info < (3, 8),
                reason="Before Python 3.8, allow_abbrev=False disables short flag bundles",
            ),
        ),
    ],
)
def test_bundles_with_custom_prefix(tmp_path, monkeypatch, source, allow_abbrev):
    parser = make_parser(
        tmp_path, monkeypatch, source, prefix="+", allow_abbrev=allow_abbrev
    )
    parser.add_argument("+o", dest="output")
    parser.add_subparsers(dest="command").add_parser("run")

    args = parser.parse_args(["+vo", "run", "run"])

    assert vars(args) == dict(setting="configured", v=1, output="run", command="run")


def test_bundle_of_flags_does_not_consume_subcommand(tmp_path, monkeypatch):
    parser = make_parser(tmp_path, monkeypatch, "env")
    parser.add_subparsers(dest="command").add_parser("run")

    args = parser.parse_args(["-vv", "run"])

    assert vars(args) == dict(setting="configured", v=2, command="run")
