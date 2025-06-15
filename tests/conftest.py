import platform
import pytest
import sys


@pytest.fixture(autouse=True)
def cov(cov, request):
    """
    Tags each test with the current OS + Python version.
    This allows to see the coverage in greater detail.
    """
    if not cov:
        return

    sys_context = "{}-py{}.{}".format(platform.system(), sys.version_info.major, sys.version_info.minor)
    test_name = request.node.nodeid
    context = "[{}]{}".format(sys_context, test_name)
    cov.switch_context(context)
    return cov