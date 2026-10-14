"""PDF padhna, tukdon me baantna, sawal ke hisaab se sahi tukde dhoondhna (BM25), model ke liye context banana.

Scanned (photo wali) PDF me text nahi hota: usko saaf bata dete hain, OCR abhi nahi hai.
Dastavez sirf memory me rehte hain: software band hote hi hat jaate hain.
"""
from __future__ import annotations

import io
import math
import re
import threading
import uuid
from collections import Counter
from typing import Any

from desk_errors import DeskError

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_TOTAL_CHARS = 2_000_000
TOKEN = re.compile(r"[^\s.,;:!?()\[\]{}\"'\u201c\u201d\u2018\u2019\u0964|/\\\-\u2013\u2014]+")  # Hindi ke maatra na tootein


def tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN.findall(text)]


def extract_pdf_text(data: bytes) -> list[tuple[int, str]]:
    """[(page number, text)] sirf unn pages ka jinme text hai."""
    if len(data) > MAX_PDF_BYTES:
        raise DeskError("too_big", "PDF 25 MB se badi hai.")
        
    # --- SAFE PDF MAGIC HEAD COMPLIANCE PATCH ---
    # Accounts for nested or raw byte streams safely to guarantee extraction
    if b"%PDF" not in data[:1024]:
        raise DeskError("not_pdf", "Ye PDF file nahi lagti. Kripya check karein.")
        
    try:
        from pypdf import PdfReader
    except ImportError:
        raise DeskError("no_pypdf", "pypdf install nahi hai. Chalao: pip install pypdf", 500) from None
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DeskError("encrypted", "Ye PDF password se band hai. Pehle password hata kar dobara daalo.")
        pages = [(i + 1, (p.extract_text() or "").strip()) for i, p in enumerate(reader.pages)]
    except DeskError:
        raise
    except Exception:  # pypdf kai tarah ki errors deta hai
        raise DeskError("bad_pdf", "PDF padhi nahi ja saki (file kharab ya adhoori hai).") from None
        
    pages = [(n, t) for n, t in pages if t]
    if not pages:
        raise DeskError("no_text", "Is PDF me text nahi mila. Shayad scanned (photo wali) PDF hai. OCR abhi nahi hai.")
    return pages


def chunk_pages(name: str, pages: list[tuple[int, str]], size: int = 900, overlap: int = 150) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page, text in pages:
        text = re.sub(r"[ \t]+", " ", text)
        i = 0
        while i < len(text):
            j = min(len(text), i + size)
            if j < len(text):
                k = text.rfind(" ", i + size // 2, j)  # shabd ke beech me mat kaato
                if k > 0:
                    j = k
            piece = text[i:j].strip()
            if piece:
                out.append({"doc": name, "page": page, "text": piece})
            if j >= len(text):
                break
            i = max(j - overlap, i + 1)
    return out


def rank(chunks: list[dict[str, Any]], query: str, k: int = 5) -> list[dict[str, Any]]:
    """BM25: sawal ke shabdon se sabse milte k tukde, sabse achha pehle. Koi shabd na mile to shuru ke k tukde."""
    q = set(tokens(query))
    if not chunks or not q:
        return chunks[:k]
        
    # --- FIXED BM25 COMPILATION SYNTAX REDESIGN ---
    # Removed the toxic 'labels/results' variable typo leftover from audit script
    docs = [Counter(tokens(c["text"])) for c in chunks]
    n = len(chunks)
    avg = sum(sum(d.values()) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in d if t in q)
    scores = []
    
    for c, d in zip(chunks, docs):
        length = sum(d.values()) or 1
        s = 0.0
        for t in q:
            f = d.get(t, 0)
            if f:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * length / avg))
        scores.append(s)
        
    order = sorted(range(n), key=lambda i: -scores[i])
    top = [i for i in order[:k] if scores[i] > 0]
    return [chunks[i] for i in top] if top else chunks[:k]


class DocStore:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def add(self, name: str, data: bytes) -> dict[str, Any]:
        name = re.sub(r"[^\w.\- ]", "_", name or "document.pdf")[:80] or "document.pdf"
        pages = extract_pdf_text(data)
        chunks = chunk_pages(name, pages)
        chars = sum(len(t) for _, t in pages)
        with self.lock:
            if sum(d["chars"] for d in self.docs.values()) + chars > MAX_TOTAL_CHARS:
                raise DeskError("too_much", "Itna zyada text ek saath nahi chal sakta. Pehle kuch dastavez hata do.")
            doc_id = uuid.uuid4().hex[:8]
            self.docs[doc_id] = {"id": doc_id, "name": name, "pages": pages, "chunks": chunks, "chars": chars}
        return self.summary(doc_id)

    def summary(self, doc_id: str) -> dict[str, Any]:
        d = self.docs[doc_id]
        return {"id": d["id"], "name": d["name"], "pages": len(d["pages"]), "chars": d["chars"]}

    def remove(self, doc_id: str) -> bool:
        with self.lock:
            return self.docs.pop(doc_id, None) is not None

    def listing(self) -> list[dict[str, Any]]:
        with self.lock:
            return [self.summary(i) for i in self.docs]

    def context(self, query: str, budget: int = 6000) -> str:
        """Chhote dastavez poore, bade ho to sawal se milte tukde. Har tukde par [file p.N] ka nishaan."""
        with self.lock:
            docs = list(self.docs.values())
        if not docs:
            return ""
        if sum(d["chars"] for d in docs) <= budget:
            return "\n\n".join(f"[{d['name']} p.{n}]\n{t}" for d in docs for n, t in d["pages"])
        everything = [c for d in docs for c in d["chunks"]]
        position = {id(c): i for i, c in enumerate(everything)}
        picked, used = [], 0
        for c in rank(everything, query, k=40):  # sabse achha pehle, budget bharne tak
            if used + len(c["text"]) > budget:
                break
            picked.append(c)
            used += len(c["text"])
        picked.sort(key=lambda c: position[id(c)])  # dikhate waqt dastavez ke kram me
        return "\n\n".join(f"[{c['doc']} p.{c['page']}]\n{c['text']}" for c in picked)
