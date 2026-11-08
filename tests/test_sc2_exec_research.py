"""Research path: every one of the 24 Terran research catalog entries through provider -> router -> executor ->
python-sc2 ``BotAI.do`` -> raw ``ActionRawUnitCommand``; each entry also refuses (nothing sent, Korean reason) when its
precondition is removed.  Offline request-shape evidence only."""

from __future__ import annotations

import pytest

pytest.importorskip("sc2.ids.ability_id")
pytest.importorskip("s2clientprotocol")

from scripts.sc2_accuracy import harness  # noqa: E402
from starcraft_commander import sc2_catalog as cat  # noqa: E402
from starcraft_commander.contracts import SC2ActionType, SC2CommandAction, SC2ExecutionError  # noqa: E402
from starcraft_commander.narrator import _error_line  # noqa: E402

pytestmark = pytest.mark.skipif(not harness.stableid_available(), reason="stableid.json not present on this machine")


def test_no_catalog_refusal_reason_leaks_the_raw_adapter_detail_to_the_user() -> None:
    codes = set(cat.REFUSAL_KO) | {
        "missing_prerequisite: ARMORY", "prerequisite_under_construction: ARMORY", "wrong_issuer: BARRACKS",
        "unresolvable_upgrade", "insufficient_researchers",
    }
    for code in sorted(codes):
        error = SC2ExecutionError(
            message=code, action_type=SC2ActionType.RESEARCH_UPGRADE, exception_type="ActionRefused",
            metadata={"detail": code},
        )
        text = _error_line(error)
        assert "세부:" not in text, (code, text)
        assert any("\uac00" <= ch <= "\ud7a3" for ch in text), (code, text)
