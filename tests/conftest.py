import platform
import pytest
import sys


@pytest.fixture(autouse=True)
def cov(cov):
    """
    Tags each test with the current OS + Python version.
    This allows to see the coverage in greater detail.
    """
    if not cov:
        return

    context = "{}-py{}.{}".format(platform.system(), sys.version_info.major, sys.version_info.minor)
    cov.switch_context(context)
    return cov