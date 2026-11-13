import pytest

from netools.core.admission import AdmissionGate
from netools.core.errors import ServiceError


def test_gate_counts_nested_entries_and_releases_on_error():
    gate = AdmissionGate(2)
    with gate:
        with gate:
            assert gate.active == 2
            with pytest.raises(ServiceError) as raised:
                with gate:
                    pass
            assert raised.value.code == "overloaded"
            assert raised.value.status_code == 503
        assert gate.active == 1
    with pytest.raises(RuntimeError):
        with gate:
            raise RuntimeError
    assert gate.active == 0
