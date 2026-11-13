import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import pdf_reader as reader


class PdfReaderTests(unittest.TestCase):
    def test_form_feeds_preserve_final_blank_page(self):
        with patch.object(reader, "run", return_value=(b"first\n\f\f", "")):
            self.assertEqual(reader.native_range(Path("example.pdf"), 1, 2), (["first\n", ""], ""))

    def test_failed_range_isolated_to_physical_page(self):
        outcomes = [reader.ReaderError("range failed"), (["first"], ""),
                    reader.ReaderError("page failed"), ([""], "")]
        with patch.object(reader, "native_range", side_effect=outcomes):
            pages = list(reader.native_pages(Path("example.pdf"), [1, 2, 3]))
        self.assertEqual([(n, text) for n, text, _ in pages], [(1, "first"), (2, None), (3, "")])
        self.assertEqual(pages[1][2], ["page failed"])

    def test_blank_preview_only_accepts_an_empty_raster_or_isolated_rules(self):
        white = b"\xff" * 20
        rule = b"\xff" * 3 + b"\x40" * 14 + b"\xff" * 3
        letter = b"\xff" * 3 + b"\x40" + b"\xff" * 16
        header = b"P5\n20 5\n255\n"
        self.assertTrue(reader.is_blank_preview(header + white * 5))
        self.assertTrue(reader.is_blank_preview(header + white * 2 + rule + white * 2))
        self.assertFalse(reader.is_blank_preview(header + white * 2 + letter + white * 2))

    def test_forced_ocr_keeps_healthy_native_text_as_primary(self):
        with (patch.object(reader, "page_geometry", return_value=(10, 20, 0)),
              patch.object(reader, "render", return_value=(b"image", 72)),
              patch.object(reader, "is_blank_preview", return_value=False),
              patch.object(reader, "ocr", return_value=("alternate", [], "", 300))):
            result = reader.read_page(Path("example.pdf"), 7, "original", [], coordinates=False,
                                      enable_ocr=True, force_ocr=True, language="eng")
        self.assertEqual((result["page"], result["text"], result["method"]), (7, "original", "native"))
        self.assertEqual(result["ocr"]["text"], "alternate")

    def test_failed_native_boxes_do_not_block_ocr(self):
        with (patch.object(reader, "page_geometry", return_value=(10, 20, 0)),
              patch.object(reader, "native_boxes", side_effect=reader.ReaderError("bad boxes")),
              patch.object(reader, "render", return_value=(b"image", 72)),
              patch.object(reader, "is_blank_preview", return_value=False),
              patch.object(reader, "ocr", return_value=("recognized", [], "", 300))):
            result = reader.read_page(Path("example.pdf"), 3, None, [], coordinates=True,
                                      enable_ocr=True, force_ocr=False, language="eng")
        self.assertEqual((result["text"], result["method"]), ("recognized", "ocr"))
        self.assertIn("bad boxes", result["warnings"])

    def test_coordinate_size_follows_visible_rotation(self):
        info = b"Page    3 size:  410 x 630 pts\nPage    3 rot:   90\n"
        with patch.object(reader, "run", return_value=(info, "")):
            self.assertEqual(reader.page_geometry(Path("example.pdf"), 3), (630, 410, 90))
        page = SimpleNamespace(page_obj=SimpleNamespace(mediabox=(0, 0, 600, 800),
                               cropbox=(40, 100, 450, 730)), rotation=90, bbox=(0, 0, 800, 600))
        self.assertEqual(reader.visible_crop(page), (100, 40, 730, 450))

    def test_search_retains_physical_pages_and_original_whitespace(self):
        document = {"source": {"sha256": "fixture"}, "pages": [
            {"page": 4, "text": "档案\n  171号", "method": "native", "status": "ok"},
            {"page": 9, "text": "考察档案 171", "method": "native", "status": "ok"},
        ]}
        result = reader.find_text(document, "档案171")
        self.assertEqual([(m["page"], m["match"]) for m in result["matches"]],
                         [(4, "档案\n  171"), (9, "档案 171")])

    def test_search_reports_truncation_without_changing_match_offsets(self):
        document = {"source": {"sha256": "fixture"}, "pages": [
            {"page": 4, "text": "档案 171", "method": "native", "status": "ok"},
            {"page": 9, "text": "档案171", "method": "native", "status": "ok"},
        ]}
        result = reader.find_text(document, "档案171", limit=1)
        self.assertTrue(result["truncated"])
        self.assertEqual((result["matches"][0]["page"], result["matches"][0]["match"]), (4, "档案 171"))

    def test_verify_reopens_one_page_and_matches_exact_span(self):
        digest = "a" * 64
        path = Path("example.pdf")
        document = {"source": {"sha256": digest}, "pages": [
            {"page": 9, "text": "原文  171", "method": "native", "status": "ok"},
        ]}
        with (patch.object(reader, "sha256", return_value=digest),
              patch.object(reader, "parse_pdf", return_value=document) as parse):
            result = reader.verify_excerpt(path, expected_sha=digest, page=9, start=0,
                                           end=7, quote="原文  171")
            self.assertTrue(result["verified"])
            parse.assert_called_once_with(path.resolve(), pages="9", language="eng+chi_sim")
            result = reader.verify_excerpt(path, expected_sha=digest, page=9, start=0,
                                           end=7, quote="原文171")
            self.assertFalse(result["verified"])
            self.assertEqual(result["reason"], "excerpt_mismatch")

    def test_verify_wrong_digest_does_not_reparse(self):
        with (patch.object(reader, "sha256", return_value="a" * 64),
              patch.object(reader, "parse_pdf") as parse):
            result = reader.verify_excerpt(Path("example.pdf"), expected_sha="b" * 64,
                                           page=9, start=0, end=2, quote="原文")
            self.assertFalse(result["verified"])
            self.assertEqual(result["reason"], "source_digest_mismatch")
            parse.assert_not_called()

    def test_table_boundaries_are_increasing_and_on_a_selected_page(self):
        spec = {"page": 9, "bbox": [10, 20, 30, 40], "columns": [10, 30], "rows": [20, 40]}
        self.assertEqual(reader.table_specs([spec], [9]), {9: [spec]})
        with self.assertRaises(reader.ReaderError):
            reader.table_specs([spec], [8])
        with self.assertRaises(reader.ReaderError):
            reader.table_specs([{**spec, "columns": [10, 30, 20]}], [9])
