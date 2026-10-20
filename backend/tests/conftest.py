"""Platform requirements shared by tests of the private POSIX storage backend."""
import os
import pytest


@pytest.fixture
def posix_storage():
    if os.name != 'posix' or any(
        not hasattr(os, name) for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')
    ):
        pytest.skip('Private storage requires POSIX directory descriptors, no-follow opens and Unix permissions.')
