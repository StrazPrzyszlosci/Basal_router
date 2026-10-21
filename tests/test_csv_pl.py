"""pytest tests for the Polish CSV reader in ``csv_pl.py``."""

from __future__ import annotations

import io
from decimal import Decimal

import pytest

from csv_pl import CsvFormatError, parse_amount, parse_csv_pl, parse_csv_pl_file


class TestParseAmount:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Plain integers and decimal commas.
            ("1234", Decimal("1234")),
            ("1234,56", Decimal("1234.56")),
            ("0,5", Decimal("0.5")),
            ("-0,5", Decimal("-0.5")),
            ("+12,30", Decimal("12.30")),
            # Space thousands separators, including non-breaking/thin spaces.
            ("1 234,56", Decimal("1234.56")),
            ("1\u00a0234,56", Decimal("1234.56")),
            ("1\u202f234,56", Decimal("1234.56")),
            ("1 234 567,89", Decimal("1234567.89")),
            # Dot thousands separator (Polish convention).
            ("1.234,56", Decimal("1234.56")),
            ("1.234.567,89", Decimal("1234567.89")),
            ("1.234", Decimal("1234")),
            ("12.345", Decimal("12345")),
            # A dot may still be a decimal point when it is not a 3-digit group.
            ("1234.56", Decimal("1234.56")),
            ("1234.5", Decimal("1234.5")),
            ("0.500", Decimal("0.500")),
            # A comma is always decimal in Polish data.
            ("1,234", Decimal("1.234")),
            # Currency suffixes and prefixes.
            ("1 234,56 zł", Decimal("1234.56")),
            ("1234,56zł", Decimal("1234.56")),
            ("1234,56 PLN", Decimal("1234.56")),
            ("PLN 1234,56", Decimal("1234.56")),
            ("€1 234,56", Decimal("1234.56")),
            ("1234,56 €", Decimal("1234.56")),
            # Negative bookkeeping styles.
            ("-1 234,56", Decimal("-1234.56")),
            ("-1.234,56", Decimal("-1234.56")),
            ("(1 234,56)", Decimal("-1234.56")),
            ("(1234,56 zł)", Decimal("-1234.56")),
            ("1234,56-", Decimal("-1234.56")),
            ("\u22121234,56", Decimal("-1234.56")),
            # Surrounding whitespace.
            ("   1 234,56   ", Decimal("1234.56")),
        ],
    )
    def test_parses_polish_formats(self, raw: str, expected: Decimal) -> None:
        assert parse_amount(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "-", "--", "—", "–", "brak", "N/A", "nd"])
    def test_placeholders_become_none(self, raw: str) -> None:
        assert parse_amount(raw) is None

    def test_none_stays_none(self) -> None:
        assert parse_amount(None) is None

    @pytest.mark.parametrize("raw", ["abc", "12,34,56", "1 234,56%", "zł", "1e3", "12,", ",12"])
    def test_rejects_invalid_amounts(self, raw: str) -> None:
        with pytest.raises(CsvFormatError, match="Invalid amount"):
            parse_amount(raw)

    def test_error_message_contains_offending_value(self) -> None:
        with pytest.raises(CsvFormatError, match="abc"):
            parse_amount("abc")

    def test_accepts_int_and_decimal_without_float_noise(self) -> None:
        assert parse_amount(7) == Decimal("7")
        amount = Decimal("10.25")
        assert parse_amount(amount) is amount

    def test_accepts_float_via_shortest_repr(self) -> None:
        assert parse_amount(0.1) == Decimal("0.1")
        assert parse_amount(1.5) == Decimal("1.5")

    def test_rejects_bool_and_other_types(self) -> None:
        with pytest.raises(CsvFormatError):
            parse_amount(True)
        with pytest.raises(TypeError):
            parse_amount(["1,00"])  # type: ignore[arg-type]

    def test_respects_decimal_context_precision(self) -> None:
        # Decimal arithmetic is exact where float would give 0.30000000000000004.
        assert parse_amount("0,10") + parse_amount("0,20") == Decimal("0.3")


class TestParseCsvPl:
    def test_basic_semicolon_file_with_polish_headers(self) -> None:
        text = (
            "Data;Opis;Kwota\n"
            "2026-01-02;Faktura F-1;1 234,56\n"
            '2026-01-03;"Opis; ze średnikiem";-99,99\n'
        )

        rows = parse_csv_pl(text)

        assert rows == [
            {"Data": "2026-01-02", "Opis": "Faktura F-1", "Kwota": Decimal("1234.56")},
            {"Data": "2026-01-03", "Opis": "Opis; ze średnikiem", "Kwota": Decimal("-99.99")},
        ]

    def test_detects_semicolon_over_decimal_commas(self) -> None:
        rows = parse_csv_pl("Data;Kwota\n2026-01-02;10,50\n")

        assert rows[0]["Kwota"] == Decimal("10.50")

    def test_detects_comma_delimiter_with_quoted_amounts(self) -> None:
        text = 'Data,Opis,Kwota\n2026-01-02,"Faktura, nr 1","1 234,56"\n'

        rows = parse_csv_pl(text)

        assert rows == [
            {"Data": "2026-01-02", "Opis": "Faktura, nr 1", "Kwota": Decimal("1234.56")}
        ]

    def test_detects_tab_and_pipe_delimiters(self) -> None:
        tab_rows = parse_csv_pl("Data\tKwota\n2026-01-02\t10,50\n")
        pipe_rows = parse_csv_pl("Data|Kwota\n2026-01-02|10,50\n")

        assert tab_rows[0]["Kwota"] == Decimal("10.50")
        assert pipe_rows[0]["Kwota"] == Decimal("10.50")

    def test_explicit_delimiter_beats_detection(self) -> None:
        rows = parse_csv_pl("Data|Kwota\n2026-01-02|10,50\n", delimiter="|")

        assert rows == [{"Data": "2026-01-02", "Kwota": Decimal("10.50")}]

    def test_strips_utf8_bom_from_bytes(self) -> None:
        data = "\ufeffData;Kwota\n2026-01-02;10,50\n".encode("utf-8")

        rows = parse_csv_pl(data)

        assert list(rows[0]) == ["Data", "Kwota"]
        assert rows[0]["Kwota"] == Decimal("10.50")

    def test_handles_crlf_line_endings(self) -> None:
        rows = parse_csv_pl("Data;Kwota\r\n2026-01-02;10,50\r\n")

        assert rows == [{"Data": "2026-01-02", "Kwota": Decimal("10.50")}]

    def test_keeps_quoted_newlines_inside_values(self) -> None:
        text = 'Data;Opis;Kwota\n2026-01-02;"linia 1\nlinia 2";10,00\n'

        rows = parse_csv_pl(text)

        assert rows[0]["Opis"] == "linia 1\nlinia 2"
        assert rows[0]["Kwota"] == Decimal("10.00")

    def test_auto_detects_several_amount_columns(self) -> None:
        text = (
            "Data;Opis;Kwota netto;Kwota brutto;Cena jedn.;Saldo;Kod\n"
            "2026-01-02;Faktura;1 000,00;1 230,00;12,50;-5,00;0012\n"
        )

        row = parse_csv_pl(text)[0]

        assert row["Kwota netto"] == Decimal("1000.00")
        assert row["Kwota brutto"] == Decimal("1230.00")
        assert row["Cena jedn."] == Decimal("12.50")
        assert row["Saldo"] == Decimal("-5.00")
        assert row["Kod"] == "0012"  # not an amount column: leading zeros preserved

    def test_amount_columns_can_be_forced(self) -> None:
        text = "Data;Bilans;Kod\n2026-01-02;1 234,56;0007\n"

        row = parse_csv_pl(text, amount_columns=["Bilans"])[0]

        assert row == {"Data": "2026-01-02", "Bilans": Decimal("1234.56"), "Kod": "0007"}

    def test_amount_column_lookup_is_case_insensitive(self) -> None:
        rows = parse_csv_pl("Data;Kwota\n2026-01-02;10,50\n", amount_columns=["kwota"])

        assert rows[0]["Kwota"] == Decimal("10.50")

    def test_no_amount_columns_configured_keeps_strings(self) -> None:
        rows = parse_csv_pl("Data;Wartosc\n2026-01-02;10,50\n", amount_columns=[])

        assert rows == [{"Data": "2026-01-02", "Wartosc": "10,50"}]

    def test_unknown_amount_column_raises(self) -> None:
        with pytest.raises(CsvFormatError, match="Unknown amount column.*Nie ma"):
            parse_csv_pl("Data;Kwota\n2026-01-02;10,50\n", amount_columns=["Nie ma"])

    @pytest.mark.parametrize("raw", ["-", "", "—", "brak"])
    def test_missing_amounts_become_none(self, raw: str) -> None:
        text = f"Data;Kwota\n2026-01-02;{raw}\n"

        assert parse_csv_pl(text)[0]["Kwota"] is None

    def test_trims_header_and_value_whitespace(self) -> None:
        rows = parse_csv_pl(" Data ; Kwota \n 2026-01-02 ; 10,50 \n")

        assert rows == [{"Data": "2026-01-02", "Kwota": Decimal("10.50")}]

    def test_skips_blank_and_whitespace_only_lines(self) -> None:
        text = "Data;Kwota\n\n   \n2026-01-02;10,50\n\n2026-01-03;20,00\n"

        rows = parse_csv_pl(text)

        assert [row["Kwota"] for row in rows] == [Decimal("10.50"), Decimal("20.00")]

    def test_tolerates_trailing_separator(self) -> None:
        rows = parse_csv_pl("Data;Kwota;\n2026-01-02;10,50;\n")

        assert rows == [{"Data": "2026-01-02", "Kwota": Decimal("10.50")}]

    def test_empty_inputs_return_empty_list(self) -> None:
        assert parse_csv_pl("") == []
        assert parse_csv_pl("   \n\n") == []
        assert parse_csv_pl("Data;Kwota\n") == []

    def test_invalid_amount_reports_physical_line_and_column(self) -> None:
        text = "Data;Kwota\n2026-01-02;1 234,56\n2026-01-03;abc\n"

        with pytest.raises(CsvFormatError) as excinfo:
            parse_csv_pl(text)

        message = str(excinfo.value)
        assert "Line 3" in message
        assert "'Kwota'" in message
        assert "abc" in message

    def test_field_count_mismatch_reports_line(self) -> None:
        with pytest.raises(CsvFormatError, match="Line 2: expected 2 fields, got 3"):
            parse_csv_pl("Data;Kwota\n2026-01-02;10,50;extra\n")

    def test_duplicate_header_raises(self) -> None:
        with pytest.raises(CsvFormatError, match="Duplicate header 'data'"):
            parse_csv_pl("Data;data\n2026-01-02;10,50\n")

    def test_empty_header_name_raises(self) -> None:
        with pytest.raises(CsvFormatError, match="Empty header"):
            parse_csv_pl("Data;;Kwota\n2026-01-02;x;10,50\n")

    def test_accepts_file_objects(self) -> None:
        text = "Data;Kwota\n2026-01-02;10,50\n"

        from_text = parse_csv_pl(io.StringIO(text))
        from_bytes = parse_csv_pl(io.BytesIO(text.encode("utf-8")))

        assert from_text == from_bytes == [
            {"Data": "2026-01-02", "Kwota": Decimal("10.50")}
        ]

    def test_accepts_iterable_of_lines(self) -> None:
        rows = parse_csv_pl(["Data;Kwota\n", "2026-01-02;10,50\n"])

        assert rows[0]["Kwota"] == Decimal("10.50")

    def test_sums_are_exact(self) -> None:
        text = "Data;Kwota\n2026-01-02;0,10\n2026-01-03;0,20\n"

        total = sum(row["Kwota"] for row in parse_csv_pl(text))

        assert total == Decimal("0.30")


class TestParseCsvPlFile:
    def test_reads_file_with_bom(self, tmp_path) -> None:
        path = tmp_path / "wyciag.csv"
        path.write_bytes("\ufeffData;Opis;Kwota\n2026-01-02;Przelew;1 234,56\n".encode("utf-8"))

        rows = parse_csv_pl_file(path)

        assert rows == [
            {"Data": "2026-01-02", "Opis": "Przelew", "Kwota": Decimal("1234.56")}
        ]

    def test_reads_cp1250_encoded_file(self, tmp_path) -> None:
        path = tmp_path / "wyciag.csv"
        text = "Data;Opis;Kwota\n2026-01-02;Faktura za prąd;10,50\n"
        path.write_bytes(text.encode("cp1250"))

        rows = parse_csv_pl_file(path, encoding="cp1250")

        assert rows[0]["Opis"] == "Faktura za prąd"
        assert rows[0]["Kwota"] == Decimal("10.50")

    def test_accepts_string_path(self, tmp_path) -> None:
        path = tmp_path / "wyciag.csv"
        path.write_text("Data;Kwota\n2026-01-02;10,50\n", encoding="utf-8")

        assert parse_csv_pl_file(str(path))[0]["Kwota"] == Decimal("10.50")
