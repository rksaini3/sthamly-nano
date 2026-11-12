"""Desk documents: galat file, bahut badi file, khali/kharab PDF, aur search (tukde + ranking)."""
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desk"))

import desk_docs as d  # noqa: E402
from desk_errors import DeskError  # noqa: E402


def blank_pdf() -> bytes:
    from pypdf import PdfWriter

    buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buf)
    return buf.getvalue()


class PdfChecks(unittest.TestCase):
    def test_not_pdf_is_refused(self):
        with self.assertRaises(DeskError) as cm:
            d.extract_pdf_text(b"hello world")
        self.assertEqual(cm.exception.code, "not_pdf")

    def test_too_big_is_refused(self):
        with self.assertRaises(DeskError) as cm:
            d.extract_pdf_text(b"%PDF" + b"0" * d.MAX_PDF_BYTES)
        self.assertEqual(cm.exception.code, "too_big")

    def test_blank_pdf_has_no_text(self):
        with self.assertRaises(DeskError) as cm:
            d.extract_pdf_text(blank_pdf())
        self.assertEqual(cm.exception.code, "no_text")

    def test_broken_pdf_is_refused(self):
        with self.assertRaises(DeskError) as cm:
            d.extract_pdf_text(b"%PDF-1.4 adhura kachra")
        self.assertIn(cm.exception.code, ("bad_pdf", "no_text"))


class Search(unittest.TestCase):
    def test_chunks_keep_file_name_and_page(self):
        chunks = d.chunk_pages("case.pdf", [(3, "alpha beta " * 200)])
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c["doc"] == "case.pdf" and c["page"] == 3 for c in chunks))

    def test_rank_puts_matching_chunk_first(self):
        chunks = [
            {"doc": "a.pdf", "page": 1, "text": "bail ki arzi yahan hai"},
            {"doc": "a.pdf", "page": 2, "text": "sirf kuch aur baatein"},
        ]
        top = d.rank(chunks, "bail arzi", k=1)
        self.assertEqual(top[0]["page"], 1)


if __name__ == "__main__":
    unittest.main()
