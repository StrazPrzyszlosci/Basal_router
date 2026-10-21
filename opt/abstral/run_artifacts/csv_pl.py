"""Read Polish CSV exports: semicolon-delimited, decimal commas, "zł" suffixes.

Polish spreadsheet exports normally use ``;`` as the field separator (because
``,`` is the decimal separator) and ``1 234,56`` as the number format.  This
module turns such files into ``list[dict]`` with amount columns converted to
``Decimal`` so no binary floating-point error sneaks into totals.

    >>> parse_csv_pl("Data;Opis;Kwota\\n2026-01-02;Faktura;1 234,56\\n")
    [{'Data': '2026-01-02', 'Opis': 'Faktura', 'Kwota': Decimal('1234.56')}]

Amount columns are detected by header keyword (see ``AMOUNT_HEADER_HINTS``) or
can be given explicitly with ``amount_columns=``.  Missing values (empty cell,
``-``, ``—``, ``brak``) become ``None``.
"""

from __future__ import annotations

import csv
import io
import os
import re
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

__all__ = [
    "AMOUNT_HEADER_HINTS",
    "CsvFormatError",
    "parse_amount",
    "parse_csv_pl",
    "parse_csv_pl_file",
]

#: Header fragments that mark a column as monetary (matched case-insensitively).
AMOUNT_HEADER_HINTS = (
    "kwota",
    "wartość",
    "wartosc",
    "cena",
    "saldo",
    "suma",
    "amount",
)

_PLACEHOLDERS = {"", "-", "--", "—", "–", "n/a", "nd", "brak"}
_CURRENCY_RE = re.compile(r"(?i)(?:pln|zł|zl|eur|usd|gbp|chf|€|\$|£)")
_SPACE_RE = re.compile(r"[\s\u00a0\u202f\u2009]+")
_PARENS_RE = re.compile(r"^\((.*)\)$")
_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_HEADER_LINE_SPLIT = re.compile(r"\r\n|\r|\n")


class CsvFormatError(ValueError):
    """A Polish CSV file (or one of its amounts) could not be parsed."""


def parse_amount(value: str | int | Decimal | None) -> Decimal | None:
    """Parse a Polish formatted amount into an exact ``Decimal``.

    Handles ``1 234,56``, ``1.234,56``, non-breaking spaces, ``zł``/``PLN``
    suffixes, accounting negatives such as ``(1 234,56)`` and trailing minus
    (``1234,56-``).  Returns ``None`` for placeholders like ``""`` or ``"-"``.

    A lone ``.`` is treated as a thousands separator when it groups a 3-digit
    tail and the integer part is not ``0`` (``1.234`` -> ``1234``), otherwise
    as a decimal point (``1234.56`` -> ``1234.56``).
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise CsvFormatError(f"Invalid amount: {value!r}")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # repr() keeps the shortest round-tripping form, avoiding 0.1 artefacts.
        value = repr(value)
    if not isinstance(value, str):
        raise TypeError(f"Amount must be a string, got {type(value).__name__}")

    text = value.strip()
    if text.casefold() in _PLACEHOLDERS:
        return None

    negative = False
    parens = _PARENS_RE.match(text)
    if parens:
        negative = True
        text = parens.group(1).strip()

    text = _CURRENCY_RE.sub("", text)
    text = _SPACE_RE.sub("", text)
    text = text.replace("\u2212", "-")  # Unicode minus sign

    if text.endswith("-") and not text.startswith("-"):
        text = "-" + text[:-1]

    if "," in text and "." in text:
        # The rightmost separator is the decimal one; the other groups thousands.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif text.count(".") == 1:
        whole, frac = text.split(".")
        digits = whole.lstrip("+-")
        if len(frac) == 3 and digits.isdigit() and digits != "0" and len(digits) <= 3:
            text = text.replace(".", "")

    if not _NUMBER_RE.match(text):
        raise CsvFormatError(f"Invalid amount: {value!r}")

    try:
        amount = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - guarded by _NUMBER_RE
        raise CsvFormatError(f"Invalid amount: {value!r}") from exc

    return -amount if negative and amount > 0 else amount


def _read_text(source: str | bytes | Iterable[str], encoding: str) -> str:
    if hasattr(source, "read"):
        data = source.read()
        if isinstance(data, (bytes, bytearray)):
            return bytes(data).decode(encoding)
        return str(data)
    if isinstance(source, (bytes, bytearray)):
        return bytes(source).decode(encoding)
    if isinstance(source, str):
        return source
    if isinstance(source, Iterable):
        return "".join(str(chunk) for chunk in source)
    raise TypeError(f"Unsupported CSV source: {type(source).__name__}")


def _detect_delimiter(text: str) -> str:
    """Pick the delimiter from the header line; ``;`` wins ties."""
    candidates = (";", "\t", ",", "|")
    line = ""
    for raw in _HEADER_LINE_SPLIT.split(text):
        if raw.strip():
            line = raw
            break

    counts: dict[str, int] = {}
    in_quotes = False
    for char in line:
        if char == '"':
            in_quotes = not in_quotes
        elif not in_quotes and char in candidates:
            counts[char] = counts.get(char, 0) + 1

    return max(candidates, key=lambda delim: (counts.get(delim, 0), delim == ";"))


def _looks_like_amount_header(header: str) -> bool:
    folded = header.casefold()
    return any(hint in folded for hint in AMOUNT_HEADER_HINTS)


def parse_csv_pl(
    source: str | bytes | Iterable[str],
    *,
    delimiter: str | None = None,
    amount_columns: Sequence[str] | None = None,
    encoding: str = "utf-8-sig",
) -> list[dict[str, Any]]:
    """Parse Polish CSV data into a list of row dictionaries.

    ``source`` may be text, bytes, an iterable of lines or an open file object.
    ``delimiter`` is auto-detected (``;``, tab, ``,`` or ``|``) when omitted.
    ``amount_columns`` defaults to every header containing an amount hint.

    All values stay ``str`` (stripped) except amount columns, which become
    ``Decimal`` or ``None``.  Raises :class:`CsvFormatError` with the physical
    line number for malformed rows, duplicate headers or bad amounts.
    """
    text = _read_text(source, encoding)
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text.strip():
        return []

    delimiter = delimiter or _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)

    header: list[str] | None = None
    rows: list[tuple[dict[str, str], int]] = []

    for row in reader:
        if not row or all(not cell.strip() for cell in row):
            continue

        if header is None:
            header = [cell.strip().lstrip("\ufeff") for cell in row]
            # Spreadsheet exports often leave a trailing separator in the header.
            while header and not header[-1]:
                header.pop()
            if not header or any(not name for name in header):
                raise CsvFormatError(f"Empty header name on line {reader.line_num}")
            seen: set[str] = set()
            for name in header:
                key = name.casefold()
                if key in seen:
                    raise CsvFormatError(
                        f"Duplicate header {name!r} on line {reader.line_num}"
                    )
                seen.add(key)
            continue

        # Tolerate a trailing separator at the end of a record.
        while len(row) > len(header) and not row[-1].strip():
            row.pop()
        if len(row) != len(header):
            raise CsvFormatError(
                f"Line {reader.line_num}: expected {len(header)} fields, "
                f"got {len(row)}"
            )

        record = {
            name: cell.strip() for name, cell in zip(header, row, strict=True)
        }
        rows.append((record, reader.line_num))

    if header is None:
        return []

    if amount_columns is None:
        targets = [name for name in header if _looks_like_amount_header(name)]
    else:
        known = {name.casefold(): name for name in header}
        targets = []
        for column in amount_columns:
            key = str(column).strip().casefold()
            if key not in known:
                raise CsvFormatError(f"Unknown amount column: {column!r}")
            targets.append(known[key])

    for record, line_num in rows:
        for column in targets:
            try:
                record[column] = parse_amount(record[column])
            except CsvFormatError as exc:
                raise CsvFormatError(
                    f"Line {line_num}, column {column!r}: {exc}"
                ) from exc

    return [record for record, _ in rows]


def parse_csv_pl_file(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8-sig",
    delimiter: str | None = None,
    amount_columns: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Read ``path`` and delegate to :func:`parse_csv_pl`."""
    return parse_csv_pl(
        Path(path).read_bytes(),
        delimiter=delimiter,
        amount_columns=amount_columns,
        encoding=encoding,
    )
