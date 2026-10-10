"""Uploads must not let a small .xlsx inflate without bound inside a worker."""
from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from app.utils.spreadsheet import SpreadsheetTooLarge, check_xlsx_expansion, read_excel_safely


def _bomb(path, inflated_bytes: int) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * inflated_bytes)


def test_highly_compressed_member_is_rejected(tmp_path):
    path = tmp_path / "bomb.xlsx"
    _bomb(path, 20 * 1024 * 1024)
    with pytest.raises(SpreadsheetTooLarge):
        check_xlsx_expansion(path)


def test_total_expansion_cap(tmp_path):
    path = tmp_path / "big.xlsx"
    _bomb(path, 3 * 1024 * 1024)
    with pytest.raises(SpreadsheetTooLarge):
        check_xlsx_expansion(path, max_bytes=1024 * 1024, max_ratio=10**9)


def test_ordinary_workbook_reads(tmp_path):
    path = tmp_path / "ok.xlsx"
    pd.DataFrame({"订单号": ["1", "2"], "金额": ["10", "20"]}).to_excel(path, index=False)
    df = read_excel_safely(path, dtype=str)
    assert list(df["订单号"]) == ["1", "2"]


def test_non_zip_files_are_left_to_the_parser(tmp_path):
    path = tmp_path / "legacy.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0not-a-zip")
    check_xlsx_expansion(path)  # no error: .xls is bounded by the upload size limit
