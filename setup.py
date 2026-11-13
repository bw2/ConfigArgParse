from setuptools import setup

with open("README.md", encoding="utf-8") as readme_file:
    long_description = readme_file.read()

install_requires = []
test_extra_requirements = [
    "black",
    "toml",
    "PyYAML",
    "pytest",
    "pytest-cov",
    "pytest-subtests",
]


setup(
    name="ConfigArgParse",
    use_scm_version={"version_scheme": "no-guess-dev"},
    setup_requires=["setuptools_scm"],
    description="A drop-in replacement for argparse that allows options to "
    "also be set via config files and/or environment variables.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/bw2/ConfigArgParse",
    py_modules=["configargparse"],
    include_package_data=True,
    license="MIT",
    keywords="options, argparse, ConfigArgParse, config, environment variables, "
    "envvars, ENV, environment, optparse, YAML, INI",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Programming Language :: Python :: 3.15",
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
    ],
    python_requires=">=3.6",
    install_requires=install_requires,
    extras_require={
        "toml": ["toml"],
        "yaml": ["PyYAML"],
        "test": test_extra_requirements,
    },
)
