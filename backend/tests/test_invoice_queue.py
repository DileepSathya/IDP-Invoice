"""Tests for watcher queue upload paths."""

from __future__ import annotations

from pathlib import Path

from backend.invoice_files import TO_BE_PROCESSED_DIR, queue_path_for_upload


def test_queue_path_for_upload_targets_to_be_processed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "backend.invoice_files.TO_BE_PROCESSED_DIR",
        tmp_path / "to_be_processed",
    )
    monkeypatch.setattr(
        "backend.invoice_files.ensure_invoice_data_layout",
        lambda: None,
    )
    path = queue_path_for_upload("invoice.pdf")
    assert path.parent == (tmp_path / "to_be_processed").resolve()
    assert path.suffix.lower() == ".pdf"
    assert path.stem.startswith("invoice_")
