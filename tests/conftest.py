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

    sys_context = f"{platform.system()}-py{sys.version_info.major}.{ sys.version_info.minor}".lower()
    test_name = request.node.nodeid
    context = f"{test_name}_{sys_context}"
    cov.switch_context(context)
    return cov
