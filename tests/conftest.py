"""Shared test fixtures.

`template_config_dir` points at the config template shipped INSIDE the
package (src/agentic_dev/templates/workspace/config), not at
`settings.config_dir` -- the latter resolves to a real workspace repo that
may not exist on a given machine (CI, a fresh clone) or may hold a user's
own edited config, neither of which is what "does the shipped default
still validate" should depend on. Tests that specifically want to validate
the *shipped* config (test_config.py, test_souls.py, test_state_machine.py)
use this fixture instead of `agentic_dev.settings.settings`.
"""

import importlib.resources
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def template_config_dir() -> Path:
    return Path(str(importlib.resources.files("agentic_dev") / "templates" / "workspace" / "config"))
