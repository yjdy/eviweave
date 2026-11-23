#!/usr/bin/env python3
"""Read a PDF locally with Poppler and optional pdfplumber and Tesseract."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from itertools import groupby
from pathlib import Path

SCHEMA_VERSION = 1
XHTML = "{http://www.w3.org/1999/xhtml}"
DAMAGED = re.compile(r"[\ufffd\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")
PAGE_RANGE = re.compile(r"([1-9][0-9]*)(?:-([1-9][0-9]*))?")
COORDINATES = "visible page, top-left origin, PDF points"


class ReaderError(Exception):
    """An input, dependency or extraction error that can be reported to a caller."""


def run(command, *, data=None, timeout=30, maximum=64 << 20):
    environment = {"PATH": os.defpath, "LC_ALL": "C.UTF-8"}
    for name in ("PATH", "TMPDIR", "TESSDATA_PREFIX", "FONTCONFIG_PATH", "FONTCONFIG_FILE",
                 "XDG_CACHE_HOME", "OMP_THREAD_LIMIT", "PDF_READER_WORKBENCH"):
        if name in os.environ:
            environment[name] = os.environ[name]
    try:
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            result = subprocess.run(command, input=data, stdout=out, stderr=err,
                                    timeout=timeout, env=environment)
            if out.tell() > maximum or err.tell() > maximum:
                raise ReaderError(f"{command[0]} exceeded its output limit")
            err.seek(0)
            warning = err.read(4096).decode("utf-8", "replace").strip()
            if result.returncode:
                raise ReaderError(f"{command[0]} exited {result.returncode}: {warning}")
            out.seek(0)
            return out.read(), warning
    except FileNotFoundError as exc:
        raise ReaderError(f"Required executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ReaderError(f"{command[0]} timed out after {timeout}s") from exc


def utf8(raw, tool):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReaderError(f"{tool} returned invalid UTF-8") from exc


def sha256(path):
    with path.open("rb") as file:
        digest = hashlib.sha256()
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
        return digest.hexdigest()


def select_pages(selection, count):
    if selection is None:
        return list(range(1, count + 1))
    pages = set()
    for part in selection.split(","):
        match = PAGE_RANGE.fullmatch(part.strip())
        if not match:
            raise ReaderError(f"Invalid page selection: {part}")
        first = int(match[1])
        last = int(match[2] or match[1])
        if not 1 <= first <= last <= count:
            raise ReaderError(f"Page range is outside 1–{count}: {part}")
        pages.update(range(first, last + 1))
    return sorted(pages)


def runs(pages):
    for _, group in groupby(enumerate(pages), key=lambda item: item[1] - item[0]):
        values = [number for _, number in group]
        yield values[0], values[-1]


def native_range(path, first, last):
    raw, warning = run(["pdftotext", "-enc", "UTF-8", "-layout", "-f", str(first),
                        "-l", str(last), str(path), "-"])
    pages = utf8(raw, "pdftotext").split("\f")
    if pages[-1] == "":
        pages.pop()
    if len(pages) != last - first + 1:
        raise ReaderError(f"pdftotext returned {len(pages)} pages for {first}–{last}")
    return pages, warning


def native_pages(path, numbers):
    """Retry a failed range by physical page. An unreadable page keeps its slot."""
    for first, last in runs(numbers):
        try:
            pages, warning = native_range(path, first, last)
        except ReaderError as exc:
            for number in range(first, last + 1):
                if first == last:
                    yield number, None, [str(exc)]
                    continue
                try:
                    single, warning = native_range(path, number, number)
                    yield number, single[0], [str(exc), *([warning] if warning else [])]
                except ReaderError as error:
                    yield number, None, [str(error)]
        else:
            for number, text in enumerate(pages, first):
                yield number, text, [warning] if warning else []


def page_geometry(path, number):
    selector = ["-f", str(number), "-l", str(number)]
    raw, _ = run(["pdfinfo", *selector, str(path)])
    info = utf8(raw, "pdfinfo")
    dimensions = re.search(r"^Page(?:\s+\d+)?\s+size:\s+([0-9.]+) x ([0-9.]+) pts", info, re.M)
    angle = re.search(r"^Page(?:\s+\d+)?\s+rot:\s+(-?\d+)\s*$", info, re.M)
    try:
        width, height = float(dimensions[1]), float(dimensions[2])
        rotation = int(angle[1]) % 360
        if not all(math.isfinite(size) and size > 0 for size in (width, height)):
            raise ValueError("invalid page size")
    except (TypeError, ValueError) as exc:
        raise ReaderError(f"Invalid PDF page geometry on page {number}") from exc
    # pdfinfo reports unrotated CropBox dimensions alongside the rotation.
    if rotation in (90, 270):
        width, height = height, width
    return width, height, rotation


def native_boxes(path, number):
    raw, _ = run(["pdftotext", "-bbox-layout", "-cropbox", "-f", str(number),
                  "-l", str(number), str(path), "-"])
    try:
        page = ET.fromstring(raw).find(f".//{XHTML}page")
        if page is None:
            raise ValueError("missing page")
        return [{"text": word.text or "",
                 "bbox": [float(word.attrib[key]) for key in ("xMin", "yMin", "xMax", "yMax")]}
                for word in page.iter(f"{XHTML}word")]
    except (ET.ParseError, KeyError, ValueError) as exc:
        raise ReaderError(f"Invalid Poppler coordinate output on page {number}") from exc


def render(path, number, size, dpi, *, gray=False):
    # Bound the longest raster side before invoking the decoder.
    dpi = min(dpi, int(4095 * 72 / max(size)))
    if dpi < 1:
        raise ReaderError("Page is too large to render")
    raw, _ = run(["pdftoppm", "-f", str(number), "-l", str(number), "-singlefile",
                  "-cropbox", "-r", str(dpi), "-gray" if gray else "-png", str(path)])
    return raw, dpi


def is_blank_preview(raw):
    """Accept an empty raster or a few isolated, thin horizontal rules."""
    header = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255[\r\n]", raw)
    if not header:
        raise ReaderError("Invalid Poppler grayscale preview")
    width, height = map(int, header.groups())
    pixels = raw[header.end():]
    if not width or not height or len(pixels) != width * height:
        raise ReaderError("Incomplete Poppler grayscale preview")
    ink = []
    for y in range(height):
        row = pixels[y * width:(y + 1) * width]
        if min(row) >= 250:
            continue
        positions = [x for x, value in enumerate(row) if value < 250]
        span = positions[-1] - positions[0] + 1
        if span < width * 0.4 or len(positions) < span * 0.95:
            return False
        ink.append(y)
    bands = list(runs(ink))
    return len(bands) <= 3 and all(last - first < 2 for first, last in bands)


def ocr(path, number, size, language):
    image, dpi = render(path, number, size, 300)
    if len(image) < 24 or image[:8] != b"\x89PNG\r\n\x1a\n":
        raise ReaderError("Invalid Poppler PNG output")
    pixels_x, pixels_y = struct.unpack(">II", image[16:24])
    if not pixels_x or not pixels_y:
        raise ReaderError("Invalid Poppler raster dimensions")
    raw, warning = run(["tesseract", "stdin", "stdout", "-l", language, "--psm", "3", "tsv"],
                       data=image, timeout=60)
    sx, sy = size[0] / pixels_x, size[1] / pixels_y
    entries = []
    try:
        rows = csv.DictReader(io.StringIO(utf8(raw, "tesseract")), delimiter="\t")
        for row in rows:
            if row["level"] != "5" or not row["text"]:
                continue
            x, y, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
            entries.append(((row["block_num"], row["par_num"], row["line_num"]),
                            {"text": row["text"], "bbox": [x*sx, y*sy, (x+width)*sx, (y+height)*sy],
                             "confidence": float(row["conf"])}))
    except (KeyError, TypeError, ValueError) as exc:
        raise ReaderError("Invalid Tesseract word output") from exc
    text = "\n".join(" ".join(item["text"] for _, item in line)
                     for _, line in groupby(entries, key=lambda entry: entry[0]))
    return text, [item for _, item in entries], warning, dpi


def number_list(values, field):
    if (not isinstance(values, list) or not values
            or any(type(n) not in (int, float) or not abs(n) <= 1e7 for n in values)):
        raise ReaderError(f"Invalid numeric list for {field}")
    return values


def table_specs(items, pages):
    if not isinstance(items, list):
        raise ReaderError("Table specification must be an array")
    validated = {}
    for item in items:
        if not isinstance(item, dict) or set(item) - {"page", "bbox", "columns", "rows"}:
            raise ReaderError("A table needs page, bbox and optionally both columns and rows")
        page = item.get("page")
        if type(page) is not int or page not in pages:
            raise ReaderError("Table page must be in the requested page range")
        bbox = number_list(item.get("bbox"), "bbox")
        if len(bbox) != 4 or min(bbox) < 0 or bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
            raise ReaderError("Expected a positive-area table bbox")
        if ("columns" in item) != ("rows" in item):
            raise ReaderError("Supply both columns and rows or neither")
        for key, a, b in (("columns", bbox[0], bbox[2]), ("rows", bbox[1], bbox[3])):
            if key in item:
                edges = number_list(item[key], key)
                if (len(edges) < 2 or edges[0] < a or edges[-1] > b
                        or any(left >= right for left, right in zip(edges, edges[1:]))):
                    raise ReaderError(f"{key} must be increasing boundaries within bbox")
        validated.setdefault(page, []).append(item)
    return validated


def visible_crop(page):
    """Map a visible rotated CropBox into pdfplumber's MediaBox space."""
    mx0, my0, mx1, my1 = page.page_obj.mediabox
    cx0, cy0, cx1, cy1 = page.page_obj.cropbox
    left, right = max(0, cx0-mx0), max(0, mx1-cx1)
    top, bottom = max(0, my1-cy1), max(0, cy0-my0)
    offsets = {0: (left, top, right, bottom), 90: (bottom, left, top, right),
               180: (right, bottom, left, top), 270: (top, right, bottom, left)}
    if page.rotation not in offsets:
        raise ReaderError(f"Unsupported PDF page rotation: {page.rotation}")
    left, top, right, bottom = offsets[page.rotation]
    x0, y0, x1, y1 = page.bbox
    return x0+left, y0+top, x1-right, y1-bottom


def table_output(pdf, spec):
    visible_box = visible_crop(pdf)
    dx, dy = visible_box[:2]
    a, b, c, d = spec["bbox"]
    if c > visible_box[2]-dx or d > visible_box[3]-dy:
        raise ReaderError("Table bbox extends outside the visible page")
    crop = pdf.crop((a+dx, b+dy, c+dx, d+dy))
    guided = "columns" in spec
    options = {"vertical_strategy": "text", "horizontal_strategy": "text"}
    if guided:
        options = {"vertical_strategy": "explicit", "horizontal_strategy": "explicit",
                   "explicit_vertical_lines": [x+dx for x in spec["columns"]],
                   "explicit_horizontal_lines": [y+dy for y in spec["rows"]]}

    def visible(box):
        return None if box is None else [box[0]-dx, box[1]-dy, box[2]-dx, box[3]-dy]

    tables = []
    for table in crop.find_tables(options):
        cells = [[{"text": text, "bbox": visible(box)} for text, box in zip(texts, row.cells)]
                 for texts, row in zip(table.extract(), table.rows)]
        tables.append({"bbox": visible(table.bbox), "rows": cells})
    complete = guided and len(tables) == 1 and len(tables[0]["rows"]) == len(spec["rows"]) - 1
    if complete:
        complete = all(len(row) == len(spec["columns"]) - 1 and all(cell["bbox"] for cell in row)
                       for row in tables[0]["rows"])
    return {"bbox": spec["bbox"], "mode": "guided" if guided else "candidate",
            "status": "extracted" if complete else "needs_review", "tables": tables}


def attach_tables(path, pages, specs):
    if not specs:
        return None
    try:
        import pdfplumber
        from pdfplumber.utils.exceptions import MalformedPDFException, PdfminerException
    except ImportError as exc:
        raise ReaderError("Install pdfplumber to use a table specification") from exc
    try:
        with pdfplumber.open(path) as pdf:
            for page in pages:
                requests = specs.get(page["page"])
                if not requests:
                    continue
                extracted = []
                for spec in requests:
                    try:
                        extracted.append(table_output(pdf.pages[page["page"] - 1], spec))
                    except (ReaderError, ValueError, IndexError, MalformedPDFException, PdfminerException) as exc:
                        extracted.append({"bbox": spec["bbox"], "status": "error",
                                          "warnings": [str(exc)], "tables": []})
                page["tables"] = extracted
    except (MalformedPDFException, PdfminerException) as exc:
        raise ReaderError(f"pdfplumber could not open the PDF: {exc}") from exc
    return pdfplumber.__version__


def read_page(path, number, native, warnings, *, coordinates, enable_ocr, force_ocr, language):
    text = native or ""
    status = "error" if native is None else "ok"
    result = {"page": number, "status": status, "method": "native" if native is not None else None,
              "text": text, "native_text": native, "warnings": warnings}
    damaged = bool(DAMAGED.search(text))
    if damaged:
        warnings.append("suspect_unicode")
    if coordinates or not text.strip() or damaged or force_ocr:
        try:
            width, height, rotation = page_geometry(path, number)
            result.update(size=[width, height], rotation=rotation)
        except ReaderError as exc:
            warnings.append(str(exc))
            if result["status"] == "ok":
                result["status"] = "needs_review"
            return result
    if coordinates:
        try:
            result["words"] = native_boxes(path, number)
        except ReaderError as exc:
            warnings.append(str(exc))
            if result["status"] == "ok":
                result["status"] = "needs_review"
    if text.strip() and not damaged and not force_ocr:
        return result
    try:
        preview, _ = render(path, number, (width, height), 72, gray=True)
        if is_blank_preview(preview) and not text.strip():
            result.update(status="blank" if native is not None else "needs_review")
        elif not enable_ocr:
            result.update(status="needs_review")
            warnings.append("ocr_disabled")
        else:
            recognized, words, warning, dpi = ocr(path, number, (width, height), language)
            if warning:
                warnings.append(warning)
            result["ocr"] = {"text": recognized, "words": words, "language": language, "dpi": dpi}
            if recognized.strip():
                if text.strip() and not damaged:
                    warnings.append("ocr_alternative")
                else:
                    result.update(text=recognized, method="ocr",
                                  status="needs_review" if text.strip() else "ok")
            else:
                result["status"] = "needs_review"
                warnings.append("ocr_returned_no_text")
    except ReaderError as exc:
        result["status"] = "needs_review" if text.strip() else "error"
        warnings.append(str(exc))
    return result


def parse_pdf(path, *, pages=None, tables=(), coordinates=False, enable_ocr=True,
              ocr_pages=None, language="eng+chi_sim"):
    path = Path(path).resolve()
    if not path.is_file():
        raise ReaderError(f"PDF file does not exist: {path}")
    if not re.fullmatch(r"[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*", language):
        raise ReaderError("Expected Tesseract language codes separated by +")
    digest = sha256(path)
    raw, diagnostic = run(["pdfinfo", str(path)])
    count = re.search(r"^Pages:\s+(\d+)\s*$", utf8(raw, "pdfinfo"), re.M)
    if not count or int(count[1]) < 1:
        raise ReaderError("Unable to determine the PDF page count")
    count = int(count[1])
    numbers = select_pages(pages, count)
    forced = set(select_pages(ocr_pages, count)) if ocr_pages is not None else set()
    if forced and (not enable_ocr or not forced.issubset(numbers)):
        raise ReaderError("OCR pages must be within the requested pages, with OCR enabled")
    requests = table_specs(list(tables) if isinstance(tables, tuple) else tables, numbers)
    outputs = [read_page(path, n, native, warnings, coordinates=coordinates,
                         enable_ocr=enable_ocr, force_ocr=n in forced, language=language)
               for n, native, warnings in native_pages(path, numbers)]
    table_engine = attach_tables(path, outputs, requests)
    if sha256(path) != digest:
        raise ReaderError("Source PDF changed during extraction")
    engines = {"native": "pdftotext"}
    if table_engine:
        engines["tables"] = f"pdfplumber {table_engine}"
    if any("ocr" in page for page in outputs):
        engines["ocr"] = "tesseract"
    return {"schema_version": SCHEMA_VERSION, "source": {"path": str(path), "sha256": digest},
            "page_count": count, "coordinates": COORDINATES, "engines": engines,
            "warnings": [diagnostic] if diagnostic else [], "pages": outputs}


def load_document(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ReaderError("Unsupported reader document format")
    return data


def read_document(document, selection):
    pages = select_pages(selection, document["page_count"])
    found = {page["page"]: page for page in document["pages"]}
    if any(n not in found for n in pages):
        raise ReaderError("Some requested pages are missing from this parsed document")
    return {**document, "pages": [found[n] for n in pages]}


def find_text(document, query, *, limit=None):
    """Ignore whitespace for matching while returning verbatim original excerpts."""
    needle = "".join(query.split())
    if not needle:
        raise ReaderError("Search text must contain a non-whitespace character")
    matches = []
    for page in document["pages"]:
        text = page["text"]
        positions = [i for i, char in enumerate(text) if not char.isspace()]
        flat = "".join(text[i] for i in positions)
        start = flat.find(needle)
        while start >= 0:
            a, b = positions[start], positions[start + len(needle) - 1] + 1
            if limit is not None and len(matches) >= limit:
                return {"source": document["source"], "matches": matches, "truncated": True}
            matches.append({"page": page["page"], "method": page["method"], "status": page["status"],
                            "start": a, "end": b, "match": text[a:b],
                            "excerpt": text[max(0, a-100):b+100]})
            start = flat.find(needle, start + len(needle))
    return {"source": document["source"], "matches": matches}


def verify_excerpt(path, *, expected_sha, page, start, end, quote, language="eng+chi_sim"):
    """Re-read one physical page and compare a literal at its reported offsets."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ReaderError("Expected a lowercase SHA-256 digest")
    if page < 1 or start < 0 or end <= start or not quote or not quote.strip():
        raise ReaderError("A positive page and nonempty text span are required")
    path = Path(path).resolve()
    digest = sha256(path)
    result = {"source": {"path": str(path), "sha256": digest},
              "page": page, "start": start, "end": end, "verified": False}
    if digest != expected_sha:
        return {**result, "reason": "source_digest_mismatch"}
    document = parse_pdf(path, pages=str(page), language=language)
    if document["source"]["sha256"] != expected_sha:
        return {**result, "reason": "source_digest_mismatch", "source": document["source"]}
    found = document["pages"][0]
    verified = end <= len(found["text"]) and found["text"][start:end] == quote
    result.update(verified=verified, method=found["method"], status=found["status"])
    if verified:
        result["match"] = quote
    else:
        result["reason"] = "excerpt_mismatch"
    return result


def write_document(value, output, original):
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        sys.stdout.write(raw)
        return
    path = Path(output)
    if path.resolve() == Path(original).resolve() or (path.exists() and path.samefile(original)):
        raise ReaderError("Output cannot overwrite the source file")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(raw)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="action", required=True)
    parse = sub.add_parser("parse", help="extract a PDF into page-oriented JSON")
    parse.add_argument("pdf")
    parse.add_argument("--pages", help="physical pages, e.g. 4,9-10")
    parse.add_argument("--coordinates", action="store_true", help="include native word boxes")
    parse.add_argument("--table-spec", type=Path, help="JSON array of table regions")
    ocr_options = parse.add_mutually_exclusive_group()
    ocr_options.add_argument("--no-ocr", action="store_true")
    ocr_options.add_argument("--ocr-pages", help="also OCR these physical pages")
    parse.add_argument("--language", default="eng+chi_sim", help="Tesseract languages")
    parse.add_argument("--out")
    read = sub.add_parser("read", help="read pages from a previously parsed JSON")
    read.add_argument("document")
    read.add_argument("--pages", required=True)
    find = sub.add_parser("find", help="search parsed pages for literal text")
    find.add_argument("document")
    find.add_argument("query")
    search = sub.add_parser("search", help="search one original PDF without dumping its pages")
    search.add_argument("pdf")
    search.add_argument("query")
    search.add_argument("--pages", help="physical page range")
    search.add_argument("--limit", type=int, default=20, help="maximum number of matches")
    search.add_argument("--language", default="eng+chi_sim", help="Tesseract languages")
    verify = sub.add_parser("verify", help="re-read an excerpt from the original PDF")
    verify.add_argument("pdf")
    verify.add_argument("--sha256", required=True)
    verify.add_argument("--page", type=int, required=True)
    verify.add_argument("--start", type=int, required=True)
    verify.add_argument("--end", type=int, required=True)
    verify.add_argument("--quote", required=True)
    verify.add_argument("--language", default="eng+chi_sim", help="Tesseract languages")
    args = cli.parse_args(argv)
    try:
        if args.action == "parse":
            specs = json.loads(args.table_spec.read_text(encoding="utf-8")) if args.table_spec else []
            result = parse_pdf(args.pdf, pages=args.pages, coordinates=args.coordinates, tables=specs,
                               enable_ocr=not args.no_ocr, ocr_pages=args.ocr_pages, language=args.language)
            if args.out and args.table_spec and (Path(args.out).resolve() == args.table_spec.resolve()
                    or (Path(args.out).exists() and Path(args.out).samefile(args.table_spec))):
                raise ReaderError("Output cannot overwrite the table specification")
            write_document(result, args.out, args.pdf)
            failed = any(page["status"] == "error" or any(
                table["status"] == "error" for table in page.get("tables", []))
                for page in result["pages"])
            return int(failed)
        if args.action == "search":
            if args.limit < 1 or not args.query.strip():
                raise ReaderError("A positive match limit and nonempty query are required")
            document = parse_pdf(args.pdf, pages=args.pages, language=args.language)
            result = find_text(document, args.query, limit=args.limit)
            result.setdefault("truncated", False)
            result["pages_checked"] = [page["page"] for page in document["pages"]]
            result["page_issues"] = [{"page": page["page"], "status": page["status"],
                                      "warnings": page["warnings"]}
                                     for page in document["pages"]
                                     if page["status"] in ("error", "needs_review")]
            write_document(result, None, args.pdf)
            return int(any(p["status"] == "error" for p in result["page_issues"]))
        if args.action == "verify":
            result = verify_excerpt(args.pdf, expected_sha=args.sha256, page=args.page,
                                    start=args.start, end=args.end, quote=args.quote,
                                    language=args.language)
            write_document(result, None, args.pdf)
            return 0 if result["verified"] else 1
        document = load_document(args.document)
        result = read_document(document, args.pages) if args.action == "read" else find_text(document, args.query)
        write_document(result, None, args.document)
        return 0
    except (ReaderError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"pdf-reader: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
