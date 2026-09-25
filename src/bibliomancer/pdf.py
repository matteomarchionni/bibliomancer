"""PDF access for the whole app, on pypdfium2 (PDFium; BSD-3 / Apache-2.0).

Replaces PyMuPDF (AGPL), so the app can be distributed under a permissive
licence. Everything the pipeline needs from a PDF goes through here:

    with open_pdf(path) as doc:          # raises Encrypted for password PDFs
        doc.page_count
        doc.text(i)                      # plain text of page i
        doc.metadata()                   # {'title': ..., 'creationDate': 'D:2024...'}
        doc.png(i, scale)                # page i rendered, PNG bytes (vision OCR)
        doc.annotations(i)               # [{'comment', 'marked'}] (scout briefs)
"""

from __future__ import annotations

import ctypes
import io

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

# markup annotations whose covered text matters (highlight, underline, squiggly, strikeout)
_MARKUP = {pdfium_c.FPDF_ANNOT_HIGHLIGHT, pdfium_c.FPDF_ANNOT_UNDERLINE,
           pdfium_c.FPDF_ANNOT_SQUIGGLY, pdfium_c.FPDF_ANNOT_STRIKEOUT}
_SKIP = {pdfium_c.FPDF_ANNOT_LINK, pdfium_c.FPDF_ANNOT_WIDGET, pdfium_c.FPDF_ANNOT_POPUP}


class Encrypted(Exception):
    """The PDF needs a password."""


def _norm(s: str) -> str:
    # PDFium marks a hyphen at a line break as U+FFFE (sometimes \x02); PyMuPDF
    # gave a plain '-', which the DOI re-joining in scouts.py relies on
    # ("10.1136/gutjnl-" + newline + "2026-338471").
    return (s.replace("\r\n", "\n").replace("\r", "\n").replace("￾", "-")
            .replace("\x02", "-").replace("\x00", ""))


class Pdf:
    def __init__(self, path: str):
        try:
            self._doc = pdfium.PdfDocument(path)
        except pdfium.PdfiumError as exc:
            if "password" in str(exc).lower():
                raise Encrypted(str(exc)) from None
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        try:
            self._doc.close()
        except Exception:
            pass

    @property
    def page_count(self) -> int:
        return len(self._doc)

    def text(self, i: int) -> str:
        page = self._doc[i]
        tp = page.get_textpage()
        try:
            return _norm(tp.get_text_range())
        finally:
            tp.close()
            page.close()

    def metadata(self) -> dict:
        """PyMuPDF-style keys: title, author, creationDate, modDate..."""
        md = self._doc.get_metadata_dict(skip_empty=True)
        keys = {"Title": "title", "Author": "author", "Subject": "subject", "Keywords": "keywords",
                "Creator": "creator", "Producer": "producer", "CreationDate": "creationDate",
                "ModDate": "modDate"}
        return {keys.get(k, k): v for k, v in md.items()}

    def size(self, i: int) -> tuple[float, float]:
        """Page width, height in points."""
        return self._doc.get_page_size(i)

    def png(self, i: int, scale: float) -> bytes:
        page = self._doc[i]
        try:
            img = page.render(scale=scale).to_pil()
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()
        finally:
            page.close()

    def annotations(self, i: int) -> list[dict]:
        """Comments and highlighted text on page i: [{'comment', 'marked'}]."""
        page = self._doc[i]
        tp = page.get_textpage()
        out = []
        try:
            for k in range(pdfium_c.FPDFPage_GetAnnotCount(page.raw)):
                annot = pdfium_c.FPDFPage_GetAnnot(page.raw, k)
                if not annot:
                    continue
                try:
                    sub = pdfium_c.FPDFAnnot_GetSubtype(annot)
                    if sub in _SKIP:
                        continue
                    comment = _annot_string(annot, "Contents")
                    marked = " ".join(_text_in(tp, box) for box in _boxes(annot)).strip()
                    if sub not in _MARKUP and not comment:
                        continue
                    out.append({"comment": comment.strip(), "marked": _norm(marked)})
                finally:
                    pdfium_c.FPDFPage_CloseAnnot(annot)
        finally:
            tp.close()
            page.close()
        return out


def _boxes(annot) -> list[tuple[float, float, float, float]]:
    """(left, bottom, right, top) of the marked area: one box per highlighted
    line (QuadPoints) - the annotation's outer rectangle would also cut into the
    ends of the lines above and below a multi-line highlight."""
    out = []
    for k in range(pdfium_c.FPDFAnnot_CountAttachmentPoints(annot)):
        q = pdfium_c.FS_QUADPOINTSF()
        if pdfium_c.FPDFAnnot_GetAttachmentPoints(annot, k, q):
            xs, ys = (q.x1, q.x2, q.x3, q.x4), (q.y1, q.y2, q.y3, q.y4)
            out.append((min(xs), min(ys), max(xs), max(ys)))
    out.sort(key=lambda b: (-round(b[3]), b[0]))          # reading order: top line first
    if not out:
        r = pdfium_c.FS_RECTF()
        if pdfium_c.FPDFAnnot_GetRect(annot, r):
            out.append((r.left, min(r.bottom, r.top), r.right, max(r.bottom, r.top)))
    return out


def _text_in(tp, box) -> str:
    """Text of the characters whose centre lies inside the box (like whole words
    under a highlight, not glyphs clipped at its edge)."""
    left, bottom, right, top = box
    pad = (top - bottom) * 0.15
    try:
        return tp.get_text_bounded(left=left - pad, bottom=bottom + pad, right=right + pad,
                                   top=top - pad)
    except Exception:
        return ""


def _annot_string(annot, key: str) -> str:
    n = pdfium_c.FPDFAnnot_GetStringValue(annot, key.encode(), None, 0)
    if n <= 2:
        return ""
    buf = ctypes.create_string_buffer(n)
    pdfium_c.FPDFAnnot_GetStringValue(annot, key.encode(),
                                      ctypes.cast(buf, ctypes.POINTER(pdfium_c.FPDF_WCHAR)), n)
    return buf.raw[:n - 2].decode("utf-16-le", errors="replace")


def open_pdf(path: str) -> Pdf:
    return Pdf(path)
