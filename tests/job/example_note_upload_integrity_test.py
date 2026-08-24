# pyright: reportMissingImports=false

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest
from pdf2image.exceptions import PDFPopplerTimeoutError

import app.jobs.extract as extract
from app.services.example_note_upload import ExampleNoteBlobIntegrityError


def _note() -> Any:
    return cast(
        Any,
        SimpleNamespace(
            azure_file_name="example-note:v1:encrypted-reference",
            doctor_id="doctor_owner",
        ),
    )


def test_pdf_rendering_is_page_dimension_concurrency_and_time_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_temp_paths: list[str] = []
    render_calls: list[tuple[str, dict[str, object]]] = []
    pages = [object(), object()]
    monkeypatch.setattr(
        extract,
        "download_registered_or_legacy_example_note",
        lambda *_args, **_kwargs: b"%PDF-1.7\nsynthetic",
    )

    def pdfinfo(path: str, **kwargs: object) -> dict[str, int]:
        seen_temp_paths.append(path)
        assert kwargs == {"timeout": extract._PDF_INFO_TIMEOUT_SECONDS}
        return {"Pages": len(pages)}

    def convert(path: str, **kwargs: object) -> list[object]:
        render_calls.append((path, kwargs))
        return pages

    monkeypatch.setattr(extract, "pdfinfo_from_path", pdfinfo)
    monkeypatch.setattr(extract, "convert_from_path", convert)
    monkeypatch.setattr(extract, "get_img_uri", lambda page: f"image:{id(page)}")

    result = extract.download_note(_note())

    assert len(result) == len(pages)
    assert render_calls == [
        (
            seen_temp_paths[0],
            {
                "dpi": 500,
                "first_page": 1,
                "last_page": len(pages),
                "thread_count": 1,
                "strict": True,
                "size": extract._MAX_RENDER_DIMENSION,
                "timeout": extract._PDF_RENDER_TIMEOUT_SECONDS,
            },
        )
    ]
    assert not Path(seen_temp_paths[0]).exists()


def test_pdf_page_limit_fails_before_render_and_removes_temp_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_temp_paths: list[str] = []
    convert = Mock()
    monkeypatch.setattr(
        extract,
        "download_registered_or_legacy_example_note",
        lambda *_args, **_kwargs: b"%PDF-1.7\nsynthetic",
    )

    def pdfinfo(path: str, **_kwargs: object) -> dict[str, int]:
        seen_temp_paths.append(path)
        return {"Pages": extract._MAX_EXAMPLE_NOTE_PAGES + 1}

    monkeypatch.setattr(extract, "pdfinfo_from_path", pdfinfo)
    monkeypatch.setattr(extract, "convert_from_path", convert)

    with pytest.raises(ExampleNoteBlobIntegrityError) as error:
        extract.download_note(_note())

    assert str(error.value) == "example_note_pdf_page_limit_exceeded"
    convert.assert_not_called()
    assert not Path(seen_temp_paths[0]).exists()


def test_pdf_parser_timeout_is_sanitized_and_removes_temp_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_temp_paths: list[str] = []
    convert = Mock()
    monkeypatch.setattr(
        extract,
        "download_registered_or_legacy_example_note",
        lambda *_args, **_kwargs: b"%PDF-1.7\nsynthetic",
    )

    def pdfinfo(path: str, **_kwargs: object) -> dict[str, int]:
        seen_temp_paths.append(path)
        raise PDFPopplerTimeoutError("private temporary path")

    monkeypatch.setattr(extract, "pdfinfo_from_path", pdfinfo)
    monkeypatch.setattr(extract, "convert_from_path", convert)

    with pytest.raises(ExampleNoteBlobIntegrityError) as error:
        extract.download_note(_note())

    assert str(error.value) == "example_note_pdf_processing_failed"
    assert "private" not in str(error.value)
    convert.assert_not_called()
    assert not Path(seen_temp_paths[0]).exists()
