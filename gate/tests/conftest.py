"""conftest.py for gate/tests — configures pytest-asyncio for gate package tests."""
# Ensure asyncio_mode = "auto" is active for this directory.
# (The orchestrator pyproject.toml sets it for orchestrator/tests; this override
#  covers gate/tests when run directly via pytest gate/tests/.)
pytest_plugins = ("pytest_asyncio",)
