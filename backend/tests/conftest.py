"""Platform requirements shared by tests of the private POSIX storage backend."""
import os
import pytest


@pytest.fixture
def posix_storage():
    if os.name != 'posix' or any(
        not hasattr(os, name) for name in ('O_DIRECTORY', 'O_NOFOLLOW', 'O_NONBLOCK')
    ):
        pytest.skip('Private storage requires POSIX directory descriptors, no-follow opens and Unix permissions.')


@pytest.fixture
def matching_detection_embeddings(monkeypatch):
    """Stub only model inference; execute the real detection filter in workflow tests."""
    import numpy as np
    from backend.app import modeltest_remote

    calls = []
    def embed(models, images):
        calls.append([image.size for image in images])
        # Unit vectors make each crop match every reference without loading weights.
        return np.ones((len(images), 1), dtype=float)
    monkeypatch.setattr(modeltest_remote, 'siglip_embed', embed)
    return calls
