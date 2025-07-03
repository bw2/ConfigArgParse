# TODO: Delete this file after we remove python 3.6 support and use pyproject.toml directly
from setuptools import setup
import ast
import re


def parse_toml_min(filename):
    """
    Hack to parse pyproject.toml and not duplicate the data inside it.
    """

    def set_in_tree(tree, path, k, v):
        d = tree
        for p in path:
            d = d.setdefault(p, {})
        d[k] = v

    def parse_value(val):
        val = val.strip()
        if val == "true":
            return True
        if val == "false":
            return False
        if val == "[]":
            return []
        if val.startswith("[") and val.endswith("]"):
            try:
                return ast.literal_eval(val)
            except Exception:
                pass
        if val.startswith("{") and val.endswith("}"):
            # TOML inline tables use = not : so replace only at key boundaries
            s = re.sub(r"(\w+)\s*=", r'"\1":', val)
            try:
                return ast.literal_eval(s)
            except Exception:
                pass
        # If it's quoted, remove quotes
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            return val[1:-1]
        # Try int/float/bare string
        try:
            return ast.literal_eval(val)
        except Exception:
            return val

    tree = {}
    cur = []
    with open(filename) as f:
        lines = list(f)
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#"):
            continue
        m = re.match(r"\[(.+)\]", line)
        if m:
            cur = m.group(1).split(".")
            continue
        if "=" in line:
            k, v = map(str.strip, line.split("=", 1))
            # Handle multi-line array
            if v.startswith("[") and not v.endswith("]"):
                array_lines = [v]
                while i < len(lines):
                    next_line = lines[i].strip()
                    array_lines.append(next_line)
                    i += 1
                    if next_line.endswith("]"):
                        break
                v = " ".join(array_lines)
            set_in_tree(tree, cur, k, parse_value(v))
    return tree


toml_data = parse_toml_min("pyproject.toml")

setup(
    name=toml_data["project"]["name"],
    version=toml_data["project"]["version"],
    description=toml_data["project"]["description"],
    long_description=toml_data["project"]["readme"],
    url=toml_data["project"]["urls"]["Homepage"],
    py_modules=toml_data["tool"]["setuptools"]["py-modules"],
    include_package_data=toml_data["tool"]["setuptools"]["include-package-data"],
    license=toml_data["project"]["license"]["text"],
    keywords=" ".join(toml_data["project"]["keywords"]),
    classifiers=toml_data["project"]["classifiers"],
    test_suite="tests",
    python_requires=toml_data["project"]["requires-python"],
    tests_require=toml_data["project"]["optional-dependencies"]["test"],
    extras_require=toml_data["project"]["optional-dependencies"],
)
