"""Explicit pytest entry for the live relay -> wrapper -> engine scenarios.

Default test runs remain zero-token and report this test as skipped. Operators
must opt in with CC_REMOTE_RUN_E2E=1 and may select a named scenario.
"""
from __future__ import annotations

import asyncio
import importlib
import os

import pytest


SCENARIOS = {
    "smoke": "tests.e2e_smoke",
    "history": "tests.e2e_history_sync",
    "multiclient": "tests.e2e_multiclient",
    "multisession": "tests.e2e_multisession",
    "reconnect": "tests.e2e_reconnect",
    "web-auth": "tests.e2e_web_auth",
}


@pytest.mark.e2e
def test_live_relay_wrapper_engine_scenario():
    if os.environ.get("CC_REMOTE_RUN_E2E") != "1":
        pytest.skip("set CC_REMOTE_RUN_E2E=1 to run a real model E2E scenario")
    scenario = os.environ.get("CC_REMOTE_E2E_SCENARIO", "smoke")
    module_name = SCENARIOS.get(scenario)
    if module_name is None:
        pytest.fail(
            f"unknown CC_REMOTE_E2E_SCENARIO={scenario!r}; "
            f"choose one of {', '.join(SCENARIOS)}")
    module = importlib.import_module(module_name)
    asyncio.run(module.main())
