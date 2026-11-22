"""Shared test helpers.

Every OpenSpec scenario is linked to at least one test with
`@scenario("<capability>", "<scenario name>")`. tests/unit/test_scenario_coverage.py
fails if a scenario in the specs has no linked test.
"""

import pytest

scenario = pytest.mark.scenario
