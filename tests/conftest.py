"""
Pytest configuration for Edoras tests.

The edoras package is installed via `pip install -e .` (see pyproject.toml),
so no sys.path manipulation is needed here.
"""
import pytest


@pytest.fixture
def db_path(tmp_path):
    """
    Fixture: path to the production database (read-only).
    Tests that need a writable DB should copy it to tmp_path.
    """
    import os
    from config import DB_PATH
    return DB_PATH
