import pytest
import re

NEVER_AWAITED_RE = re.compile(r"coroutine '([^']+)' was never awaited", re.M)


@pytest.hookimpl(hookwrapper=True)
def pytest_pyfunc_call(pyfuncitem):
    with pytest.warns(None) as records:
        yield
        for record in records:
            message = str(record.message)
            assert NEVER_AWAITED_RE.search(message) is None, message

