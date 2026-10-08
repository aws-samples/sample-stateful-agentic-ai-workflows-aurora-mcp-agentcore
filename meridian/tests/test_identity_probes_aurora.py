"""Against the live cluster: row-level security hides Jordan's rows from the decoy's scope."""

import pytest

from backend.db.rds_data_client import get_rds_data_client
from scripts.identity_probes.effects import AuroraPort
from scripts.identity_probes.probes import DECOY_TRAVELER, JORDAN_TRAVELER


@pytest.mark.database
def test_the_decoy_sees_none_of_jordans_rows_and_jordan_sees_all_of_them():
    port = AuroraPort(get_rds_data_client())

    baseline = port.baseline_count(JORDAN_TRAVELER)

    assert baseline > 0
    assert port.scoped_count(DECOY_TRAVELER, JORDAN_TRAVELER) == 0
    assert port.scoped_count(JORDAN_TRAVELER, JORDAN_TRAVELER) == baseline
