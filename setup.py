import logging
import os
import sys


try:
    from setuptools import setup
except ImportError:
    print("WARNING: setuptools not installed. Will try using distutils instead..")
    from distutils.core import setup


def launch_http_server(directory):
    assert os.path.isdir(directory)

    try:
        try:
            from SimpleHTTPServer import SimpleHTTPRequestHandler
        except ImportError:
            from http.server import SimpleHTTPRequestHandler

        try:
            import SocketServer
        except ImportError:
            import socketserver as SocketServer

        import socket

        for port in [80] + list(range(8000, 8100)):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("localhost", port))
                s.close()
            except socket.error as e:
                logging.debug("Can't use port %d: %s" % (port, e.strerror))
                continue

            print(
                "HTML coverage report now available at http://{}{}".format(
                    socket.gethostname(), (":%s" % port) if port != 80 else ""
                )
            )

            os.chdir(directory)
            SocketServer.TCPServer(("", port), SimpleHTTPRequestHandler).serve_forever()
        else:
            logging.debug("All network port. ")
    except Exception as e:
        logging.error(
            "ERROR: while starting an HTTP server to serve "
            "the coverage report: %s" % e
        )


command = sys.argv[-1]
if command == "publish":
    os.system("rm -rf dist")
    os.system("python3 setup.py sdist")
    os.system("python3 setup.py bdist_wheel")
    os.system(
        "twine check dist/*"
    )  # check for formatting or other issues that would cause twine upload to error out
    os.system("twine upload dist/*whl dist/*gz")
    sys.exit()
elif command == "coverage":
    try:
        import coverage
    except:
        sys.exit("coverage.py not installed (pip install --user coverage)")
    setup_py_path = os.path.abspath(__file__)
    os.system("coverage run --source=configargparse " + setup_py_path + " test")
    os.system("coverage report")
    os.system("coverage html")
    print("Done computing coverage")
    launch_http_server(directory="htmlcov")
    sys.exit()

long_description = ""
if command not in ["test", "coverage"]:
    long_description = open("README.rst").read()

install_requires = []
tests_require = [
    "mock",
    "PyYAML",
    "pytest",
]


setup(
    name="ConfigArgParse",
    version="1.7.1",
    description="A drop-in replacement for argparse that allows options to "
    "also be set via config files and/or environment variables.",
    long_description=long_description,
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
        "Programming Language :: Python :: Implementation :: CPython",
        "Programming Language :: Python :: Implementation :: PyPy",
    ],
    test_suite="tests",
    python_requires=">=3.6",
    install_requires=install_requires,
    tests_require=tests_require,
    extras_require={
        "yaml": ["PyYAML"],
        "test": tests_require,
    },
)
