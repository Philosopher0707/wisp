# tests/test_task_profiles.py — profile overrides + CI safest (M6 T4).
import pytest
from wisp.config import WispConfig
from wisp.task.profiles import PROFILES, apply_profile


def test_all_five_profiles_present():
    assert set(PROFILES) == {"personal", "enterprise-managed", "offline-secure",
                             "read-only-review", "ci-headless"}


def test_unknown_profile_rejected():
    with pytest.raises(ValueError, match="unknown profile"):
        apply_profile(WispConfig(), "root")


def test_read_only_review_denies_mutation_posture():
    cfg = apply_profile(WispConfig(), "read-only-review")
    mode = cfg.permission_mode
    assert (mode.value if hasattr(mode, "value") else mode) == "read_only"


def test_ci_headless_safest():
    cfg = apply_profile(WispConfig(), "ci-headless")
    assert cfg.profile_deny_exec is True
    assert cfg.profile_network_off is True
    assert cfg.auto_approve is False


def test_personal_allows_writes_without_auto():
    cfg = apply_profile(WispConfig(), "personal")
    mode = cfg.permission_mode
    assert (mode.value if hasattr(mode, "value") else mode) == "auto_edit"
    assert cfg.auto_approve is False
