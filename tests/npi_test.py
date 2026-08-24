from app.utils.npi import is_valid_npi


def test_npi_checksum_validation() -> None:
    assert is_valid_npi("1234567893")
    assert not is_valid_npi("1234567890")
    assert not is_valid_npi("12345678901")
    assert not is_valid_npi("12345A7893")
    assert not is_valid_npi("0000000000")
