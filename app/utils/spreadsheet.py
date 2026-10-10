"""Excel parsing with a decompression bound.

Upload limits cap the bytes on the wire, but an .xlsx is a zip: a few MB can
inflate to gigabytes once pandas/openpyxl expand it inside an API worker. Check
the archive's declared sizes before handing it to the parser. Legacy .xls files
are not zip archives and are already bounded by the upload size limit.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

MAX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200


class SpreadsheetTooLarge(ValueError):
    """The workbook would expand beyond what a worker should parse."""


def check_xlsx_expansion(
    path: str | Path,
    max_bytes: int = MAX_UNCOMPRESSED_BYTES,
    max_ratio: int = MAX_COMPRESSION_RATIO,
) -> None:
    if not zipfile.is_zipfile(path):
        return
    with zipfile.ZipFile(path) as archive:
        total = 0
        for info in archive.infolist():
            total += info.file_size
            ratio = info.file_size / max(info.compress_size, 1)
            if total > max_bytes or (info.file_size > 1024 * 1024 and ratio > max_ratio):
                raise SpreadsheetTooLarge("文件解压后过大或压缩比异常，请导出为 CSV 或拆分后再上传。")


def read_excel_safely(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    check_xlsx_expansion(path)
    return pd.read_excel(path, **kwargs)
