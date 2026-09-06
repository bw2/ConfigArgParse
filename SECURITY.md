# Security Policy

## Supported versions

Security fixes are released for the latest version only. If you are running an older
version, please upgrade before reporting an issue, in case it is already fixed.

## Reporting a vulnerability

Please report security issues privately, not as a public GitHub issue.

Use GitHub's private vulnerability reporting:

https://github.com/bw2/ConfigArgParse/security/advisories/new

That form is private to the maintainers.

Helpful things to include:

- the version of ConfigArgParse you tested, and the Python version
- a short script that reproduces the problem, ideally one file
- what an attacker would need to control (a config file, an environment variable, command
  line arguments) and what they gain

## What to expect

This is a small project maintained in spare time, so please allow a few days for a first
reply. If a report is valid, the usual sequence is a fix on `master`, a release to PyPI, and
a published GitHub security advisory crediting you unless you would rather not be named.
