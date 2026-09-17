"""Bounded PDF parsing and TIFF conversion; originals are never modified."""

from io import BytesIO
from time import struct_time
import warnings

from PIL import Image
from pypdf import PdfReader, PdfWriter

from .policy import Rejected

MAX_PIXELS_PER_PAGE = 25_000_000
MAX_TOTAL_PIXELS = 100_000_000
MAX_NORMALIZED_BYTES = 50 * 1024 * 1024
PDF_TIMESTAMP = struct_time((1970, 1, 1, 0, 0, 0, 3, 1, 0))
Image.MAX_IMAGE_PIXELS = MAX_PIXELS_PER_PAGE


def inspect_type(body, mime):
    if mime == "application/pdf" and body.startswith(b"%PDF-"):
        return
    if mime == "image/tiff" and body[:4] in (b"II*\x00", b"MM\x00*"):
        return
    raise Rejected("unsupported_or_mismatched_media")


def normalize(body, mime, max_pages):
    inspect_type(body, mime)
    try:
        if mime == "application/pdf":
            reader = PdfReader(BytesIO(body), strict=True)
            if reader.is_encrypted:
                raise Rejected("encrypted_pdf")
            count = len(reader.pages)
            if not 1 <= count <= max_pages:
                raise Rejected("page_limit")
            # Only copy page content, not document actions, attachments, or metadata.
            return select_pages(body, list(range(1, count + 1))), count
        frames, total = [], 0
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(body)) as image:
                    if image.format != "TIFF" or not 1 <= image.n_frames <= max_pages:
                        raise Rejected("page_limit")
                    for index in range(image.n_frames):
                        image.seek(index)
                        pixels = image.width * image.height
                        total += pixels
                        if pixels > MAX_PIXELS_PER_PAGE or total > MAX_TOTAL_PIXELS:
                            raise Rejected("pixel_limit")
                        frames.append(image.convert("RGB"))
            output = BytesIO()
            # Pillow otherwise inserts the current time, changing immutable output
            # bytes if an activity retries after writing the PDF but before its marker.
            frames[0].save(output, format="PDF", save_all=True, append_images=frames[1:],
                           creationDate=PDF_TIMESTAMP, modDate=PDF_TIMESTAMP)
            if output.tell() > MAX_NORMALIZED_BYTES:
                raise Rejected("normalized_size_limit")
            return output.getvalue(), len(frames)
        finally:
            for frame in frames:
                frame.close()
    except Rejected:
        raise
    except Exception:
        raise Rejected("unreadable_document") from None


def select_pages(pdf, original_pages):
    reader, writer = PdfReader(BytesIO(pdf), strict=True), PdfWriter()
    for number in original_pages:
        page = reader.pages[number - 1]
        # Annotation actions can invoke external URLs/JavaScript in PDF readers.
        for key in ("/Annots", "/AA"):
            if key in page:
                del page[key]
        writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    if output.tell() > MAX_NORMALIZED_BYTES:
        raise Rejected("normalized_size_limit")
    return output.getvalue()
