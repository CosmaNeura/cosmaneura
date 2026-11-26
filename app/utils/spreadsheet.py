"""Formula-injection-safe CSV output for files opened in spreadsheets."""

import csv
from collections.abc import Iterable, Mapping
from typing import Any, TextIO

# Excel, LibreOffice and Google Sheets evaluate a cell beginning with any of
# these as a formula (or DDE command). Full-width variants are converted to
# their ASCII forms by some IMEs and locales.
_FORMULA_LEADERS = frozenset("=+-@\t\r\n\uff1d\uff0b\uff0d\uff20")


def neutralize_formula(value: Any) -> Any:
    """Return ``value`` with a single-quote prefix if a spreadsheet would
    evaluate it as a formula. ``None`` and non-string values pass through."""

    if not isinstance(value, str) or not value:
        return value
    if value[0] in _FORMULA_LEADERS or value.lstrip()[:1] in _FORMULA_LEADERS:
        return "'" + value
    return value


def write_safe_csv(
    buffer: TextIO,
    fieldnames: list[str],
    rows: Iterable[Mapping[str, Any]],
) -> None:
    """Write ``rows`` with every cell formula-neutralized.

    Every field is quoted so a spreadsheet using ``;`` as its list separator
    cannot split a value into a new, formula-leading cell.
    """

    writer = csv.DictWriter(buffer, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
    writer.writerow({name: neutralize_formula(name) for name in fieldnames})
    for row in rows:
        writer.writerow(
            {name: neutralize_formula(value) for name, value in row.items()}
        )
