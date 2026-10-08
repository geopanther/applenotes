import importlib.util
from pathlib import Path

import pytest


def test_branch_gate_is_independent_of_statement_coverage():
    spec = importlib.util.spec_from_file_location(
        "coverage_gate", Path(__file__).resolve().parents[1] / "scripts/check_coverage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.branch_percent({"totals": {"covered_branches": 90, "num_branches": 100}}) == 90
    assert module.branch_percent({"totals": {"covered_branches": 0, "num_branches": 0}}) == 100
    assert module.branch_percent({"totals": {"covered_branches": 89, "num_branches": 100}}) < 90
    with pytest.raises(KeyError):
        module.branch_percent({})
