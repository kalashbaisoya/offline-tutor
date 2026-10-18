"""S1b: turn PDF pages into clean Markdown + LaTeX using the teacher's vision on the page image.

The chosen books' text layers are old OCR with broken formulas ("fIx) = b x exp[-(b x2/2)], 0 ~ x < 00").
The teacher sees the page image *and* the raw text layer and writes a faithful transcription. It adds nothing.

Usage: python train/clean_pages.py BOOK.pdf FIRST LAST OUT_DIR    (1-based PDF page numbers, inclusive)
Writes OUT_DIR/pages/pNNNN.md. Resumable: pages already written are skipped.
"""

import base64
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium

from common import PARALLEL, Status, teacher

PROMPT = (
    "Transcribe this textbook page into Markdown. Rules:\n"
    "- Use the page IMAGE as the truth. The raw text layer below is old OCR and only helps with spelling.\n"
    "- Write every formula in LaTeX ($...$ inline, $$...$$ display) exactly as printed; keep equation numbers like (3.14).\n"
    "- Section headings as '## 3.4 PARALLEL SYSTEMS' (keep the number); sub-headings as '###'.\n"
    "- Tables as Markdown tables. Each figure or diagram as one line: [Figure 3.2: what it shows, incl. any labels/values].\n"
    "- Drop running headers, footers and page numbers.\n"
    "- Do not add, explain, summarise or correct anything. Output only the transcription.\n\n"
    "Raw text layer:\n"
)


def page_png(pdf, i):
    buf = io.BytesIO()
    pdf[i].render(scale=2).to_pil().save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def main(book, first, last, out_dir):
    first, last, out = int(first), int(last), Path(out_dir)
    (out / "pages").mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(book)
    todo = [p for p in range(first, last + 1) if not (out / "pages" / f"p{p:04d}.md").exists()]
    status = Status(out, "clean_pages", len(todo))
    if not todo:
        status.write("done")
        return
    jobs = [(p, pdf[p - 1].get_textpage().get_text_range(), page_png(pdf, p - 1)) for p in todo]  # pdfium is not thread-safe
    raw_pages = []

    def work(job):
        p, raw, png = job
        msg = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
            {"type": "text", "text": PROMPT + raw}]}]
        for repeat_penalty in (1.0, 1.1):  # a cut-off transcription is almost always a repetition loop
            text, meta = srv.chat(msg, max_tokens=4000, temperature=0.2, top_p=0.9, repeat_penalty=repeat_penalty)
            if meta["finish"] == "stop":
                break
        else:  # keep the run going; the page is flagged and keeps its raw OCR text
            text = "<!-- RAW OCR: transcription failed -->\n" + raw
            raw_pages.append(p)
        (out / "pages" / f"p{p:04d}.md").write_text(text.strip() + "\n", encoding="utf-8")
        status.tick(last_page=p, raw_ocr_pages=raw_pages)

    try:
        with teacher(vision=True) as srv, ThreadPoolExecutor(PARALLEL) as ex:
            list(ex.map(work, jobs))
    except Exception as e:
        status.write("failed", error=f"{type(e).__name__}: {e}")
        raise
    status.write("done")


if __name__ == "__main__":
    main(*sys.argv[1:5])
