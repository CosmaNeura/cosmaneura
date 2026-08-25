"""National Provider Identifier validation."""


def is_valid_npi(value: str) -> bool:
    """Validate a 10-digit NPI with the CMS Luhn check-digit rule."""

    if (
        len(value) != 10
        or not value.isascii()
        or not value.isdigit()
        or value[0] not in {"1", "2"}
    ):
        return False

    # CMS assigns the 80840 health-industry prefix. Its contribution to the
    # checksum is the constant 24; the tenth NPI digit is the check digit.
    total = 24
    for index, character in enumerate(value[:9]):
        digit = int(character)
        product = digit * (2 if index % 2 == 0 else 1)
        total += product // 10 + product % 10

    expected_check_digit = (10 - total % 10) % 10
    return int(value[-1]) == expected_check_digit
