"""The text of a file, read locally so Claude gets it quickly: PDFs with pdftotext, Word/Excel/PowerPoint and
LibreOffice files from the XML inside them (they're zip files), and plain text. A scanned PDF or a picture has
no text: then Claude looks at the file itself (see skills/documents.py)."""
import os
import re
import subprocess
import zipfile
from xml.etree import ElementTree

TEXT = (".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".log", ".ini", ".cfg",
        ".conf", ".toml", ".py", ".js", ".ts", ".sh", ".c", ".h", ".cpp", ".java", ".go", ".rs", ".sql", ".srt", ".tex")
OFFICE = (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp")
LOOK = (".png", ".jpg", ".jpeg", ".gif", ".webp")  # Claude's Read tool shows it pictures (and PDFs) as they are
READABLE = TEXT + OFFICE + LOOK + (".pdf", ".rtf")
PDF_PAGES = 40  # read at most this many pages of a PDF


class Unreadable(Exception):
    pass


def text_of(path, limit=60000):
    """(text, pages) of the file, the text cut at `limit` characters; pages is how many a PDF has (else 0).
    Empty text means there's none to read (a scan or a picture). Raises Unreadable for other kinds."""
    ext = os.path.splitext(path)[1].lower()
    if ext in LOOK:
        return "", 0
    if ext == ".pdf":
        return _pdf(path, limit)
    if ext in OFFICE:
        return _office(path, ext)[:limit], 0
    if ext == ".rtf":
        return _rtf(path)[:limit], 0
    if ext in TEXT or _looks_like_text(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit), 0
    raise Unreadable("I can't read %s files" % (ext.lstrip(".") or "those"))


def pdf_pages(path):
    try:
        out = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    m = re.search(r"^Pages:\s+(\d+)", out, re.MULTILINE)
    return int(m.group(1)) if m else 0


def _pdf(path, limit):
    pages = pdf_pages(path)
    try:
        res = subprocess.run(["pdftotext", "-layout", "-l", str(PDF_PAGES), path, "-"], capture_output=True,
                             text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise Unreadable("the PDF took too long to read")
    if res.returncode != 0:
        raise Unreadable("the PDF wouldn't open%s" % (" (it may need a password)" if "ncrypt" in res.stderr or
                                                      "assword" in res.stderr else ""))
    text = re.sub(r"[ \t]{3,}", "  ", res.stdout)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # A scanned page gives a few stray characters at most.
    return (text[:limit] if len(text) > 40 * min(pages or 1, 5) else ""), pages


def _office(path, ext):
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise Unreadable("the file looks damaged")
    with z:
        names = z.namelist()
        if ext in (".odt", ".ods", ".odp"):
            return _odf(z.read("content.xml"), ext)
        if ext == ".docx":
            return _paragraphs(z.read("word/document.xml"))
        if ext == ".pptx":
            slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                            key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[1]).group()))
            return "\n\n".join("Slide %s:\n%s" % (re.search(r"(\d+)\.xml$", n).group(1), _paragraphs(z.read(n)))
                                 for n in slides)
        return _xlsx(z, names)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _paragraphs(xml):
    """Word and PowerPoint text: each <w:p>/<a:p> paragraph on its own line, table cells split by tabs."""
    lines = []
    for p in ElementTree.fromstring(xml).iter():
        if _local(p.tag) != "p":
            continue
        line = "".join((t.text or "") if _local(t.tag) == "t" else "\t" if _local(t.tag) == "tab" else ""
                       for t in p.iter())
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def _xlsx(z, names):
    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")):
            shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
    sheet_names = [s.get("name") for s in ElementTree.fromstring(z.read("xl/workbook.xml")).iter()
                   if _local(s.tag) == "sheet"]
    files = sorted((n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                   key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
    out = []
    for i, name in enumerate(files):
        rows = []
        for row in ElementTree.fromstring(z.read(name)).iter():
            if _local(row.tag) != "row":
                continue
            cells = []
            for c in row:
                v = next((x.text for x in c.iter() if _local(x.tag) in ("v", "t") and x.text), "")
                if c.get("t") == "s" and v.isdigit() and int(v) < len(shared):
                    v = shared[int(v)]
                col = _column(c.get("r") or "")
                cells += [""] * max(0, col - len(cells))  # blank cells aren't in the file: keep the columns
                cells.append(v)
            if any(cells):
                rows.append("\t".join(cells))
        title = sheet_names[i] if i < len(sheet_names) else "Sheet %d" % (i + 1)
        out.append("Sheet \"%s\":\n%s" % (title, "\n".join(rows)))
    return "\n\n".join(out)


def _column(ref):
    """0-based column of a cell reference ("C4" -> 2); -1 when there's none."""
    n = 0
    for ch in re.match(r"[A-Z]*", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _odf(xml, ext):
    """LibreOffice text: paragraphs and headings; spreadsheet rows with tab-separated cells; one block per slide."""
    root = ElementTree.fromstring(xml)
    if ext == ".ods":
        out = []
        for table in (t for t in root.iter() if _local(t.tag) == "table"):
            rows = []
            for row in (r for r in table.iter() if _local(r.tag) == "table-row"):
                cells = ["".join(c.itertext()) for c in row if _local(c.tag) == "table-cell"]
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    rows.append("\t".join(cells))
            out.append("Sheet \"%s\":\n%s" % (table.get("{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name"),
                                              "\n".join(rows)))
        return "\n\n".join(out)
    lines = ["".join(p.itertext()).strip() for p in root.iter() if _local(p.tag) in ("p", "h")]
    return "\n".join(l for l in lines if l)


def _rtf(path):
    with open(path, encoding="latin-1") as f:
        raw = f.read()
    raw = re.sub(r"\\par[d]?", "\n", raw)
    raw = re.sub(r"\\'[0-9a-f]{2}|\\[a-z]+-?\d* ?|[{}]", "", raw)
    return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _looks_like_text(path):
    try:
        with open(path, "rb") as f:
            head = f.read(2048)
    except OSError:
        return False
    return bool(head) and b"\0" not in head
