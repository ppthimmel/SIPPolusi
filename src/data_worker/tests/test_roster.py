"""Uji penyusunan daftar stasiun terhadap markah nyata portal.

Cuplikan pada REAL_ROW disalin apa adanya dari `/lokasi-spku` pada
13 September 2026, lengkap dengan kelas Tailwind dan lekukannya, agar
perubahan pada ekspresi reguler ketahuan sebelum dijalankan ke lapangan.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from spku.extract import extract_scalar  # noqa: E402
from spku.roster import (  # noqa: E402
    initial_of,
    parse_list_page,
    parse_sitemap,
    reconcile,
)

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"

REAL_ROW = '''<table><tbody>
<tr class="hover:bg-blue-50 hover:shadow-sm transition-all duration-150 cursor-pointer group dark:hover:bg-blue-900/20" data-redirect="/spku/d0d713ca-1e2d-430c-980a-7c288f7f5210" style="">
                                <td class="px-6 py-5 text-center text-sm text-[#667085] tracking-[-0.21px] border-b border-[#eaecf0] dark:text-gray-400">1</td>
                                <td class="px-6 py-5 text-sm font-medium text-[#101828] group-hover:text-blue-600 tracking-[-0.21px] border-b border-[#eaecf0] dark:text-white dark:group-hover:text-blue-400">
                                    DKI17 SDN 07 Kramat Pela
                                    <div class="text-xs font-normal text-[#667085] dark:text-gray-400 mt-0.5">JL GANDARIA TENGAH No.5, RT.2/RW.1, Kramat Pela, Kebayoran Baru, South Jakarta City, Jakarta 12130</div>
                                </td>
                                <td class="px-6 py-5 border-b border-[#eaecf0]">
                                    <div class="flex items-center justify-center gap-1">
                                            <span class="bg-[#45c474] text-white text-[10px] px-1.5 py-1 rounded tracking-[-0.15px]">PM25</span>
                                    </div>
                                </td>
                                <td class="px-6 py-5">KEBAYORAN  BARU</td>
                                <td class="px-6 py-5">KOTA ADM. JAKARTA SELATAN</td>
                            </tr>
</tbody></table>'''


def test_markah_nyata_menghasilkan_uuid_dan_label():
    roster = parse_list_page(REAL_ROW)
    assert roster == {"d0d713ca-1e2d-430c-980a-7c288f7f5210": "DKI17 SDN 07 Kramat Pela"}


def test_alamat_tidak_ikut_terbaca_sebagai_label():
    label = parse_list_page(REAL_ROW)["d0d713ca-1e2d-430c-980a-7c288f7f5210"]
    assert "GANDARIA" not in label


def test_kode_ringkas_terbaca_dari_label():
    assert initial_of("DKI17 SDN 07 Kramat Pela") == "DKI17"
    assert initial_of("LCS25 Stasiun Palmerah") == "LCS25"
    assert initial_of("tanpa kode") is None


def test_peta_situs_dan_halaman_daftar_saling_memeriksa():
    listing = parse_list_page((FIX / "lokasi_spku.html").read_text(encoding="utf-8"))
    sitemap = parse_sitemap((FIX / "sitemap.xml").read_text(encoding="utf-8"))
    merged, warnings = reconcile(listing, sitemap)
    assert len(merged) == 3
    assert warnings == []


def test_stasiun_yang_hanya_ada_di_peta_situs_dilaporkan():
    listing = {"aaaaaaaa-0000-4000-8000-000000000001": "DKI01 A"}
    sitemap = {"aaaaaaaa-0000-4000-8000-000000000001",
               "bbbbbbbb-0000-4000-8000-000000000002"}
    merged, warnings = reconcile(listing, sitemap)
    assert len(merged) == 2
    assert any("peta situs" in w for w in warnings)


def test_waktu_pembaruan_beranda_terbaca_sebagai_skalar():
    """__SPKU_UPDATE_TIME__ bernilai string, bukan objek, sehingga pemindai
    pembatas berimbang tidak dapat membacanya."""
    html = (FIX / "home_ok.html").read_text(encoding="utf-8")
    assert extract_scalar(html, "__SPKU_UPDATE_TIME__") == "2026-09-13 13:30:00"


def test_waktu_pembaruan_null_saat_portal_bermasalah():
    html = (FIX / "home_kosong.html").read_text(encoding="utf-8")
    assert extract_scalar(html, "__SPKU_UPDATE_TIME__") is None
