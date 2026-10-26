import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from spku.extract import ExtractionError, extract_json  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def test_menemukan_variabel_tanpa_prefiks_window():
    html = (FIX / "detail_referensi_lengkap.html").read_text(encoding="utf-8")
    payload = extract_json(html, "SPKU_DETAIL_DATA")
    assert payload["datasourceKode"] == "DKI1"


def test_tidak_terpotong_oleh_pembatas_di_dalam_string():
    """Muatan memuat string berisi '};' dan '];'; pemindai harus mengabaikannya."""
    html = (FIX / "detail_referensi_lengkap.html").read_text(encoding="utf-8")
    payload = extract_json(html, "SPKU_DETAIL_DATA")
    assert len(payload["rawHistory"]) == 6 * 96


def test_variabel_absen_mengembalikan_none():
    assert extract_json("<html></html>", "SPKU_DETAIL_DATA") is None


def test_muatan_rusak_menimbulkan_galat():
    html = (FIX / "detail_rusak.html").read_text(encoding="utf-8")
    with pytest.raises(ExtractionError):
        extract_json(html, "SPKU_DETAIL_DATA")


def test_beranda_dengan_prefiks_window():
    html = (FIX / "home_ok.html").read_text(encoding="utf-8")
    rows = extract_json(html, "__SPKU_DATA__")
    assert len(rows) == 20


def test_beranda_kosong_terbaca_sebagai_daftar_kosong():
    html = (FIX / "home_kosong.html").read_text(encoding="utf-8")
    assert extract_json(html, "__SPKU_DATA__") == []
