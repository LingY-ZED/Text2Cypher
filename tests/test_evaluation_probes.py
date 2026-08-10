"""The recovery probe suite must cover all four deterministic paths."""

from evaluation.probes import run_recovery_probes


def test_all_recovery_probes_succeed() -> None:
    assert run_recovery_probes() == {
        "parse": True,
        "validation": True,
        "execution": True,
        "empty_result": True,
    }
