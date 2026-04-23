from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import google.generativeai as genai
from dotenv import load_dotenv
from paddleocr import PaddleOCR

from backend.app_logging import configure_logging


logger = logging.getLogger(__name__)

ALLOWED_EXTS = {".jpeg", ".jpg", ".png", ".pdf", ".docx"}


@dataclass(frozen=True)
class OcrResult:
    file_path: str
    ocr_text: str
    gemini_raw_text: str
    gemini_json: Optional[dict[str, Any]]
    preprocessing_start_time: Optional[datetime]
    preprocessing_end_time: Optional[datetime]
    ocr_start_time: Optional[datetime]
    ocr_end_time: Optional[datetime]
    gemini_start_time: Optional[datetime]
    gemini_end_time: Optional[datetime]
    preprocessing_latency: Optional[float]
    ocr_latency: Optional[float]
    gemini_latency: Optional[float]


_PADDLE_OCR = None


def _get_ocr():
    global _PADDLE_OCR

    if _PADDLE_OCR is None:
        logger.info("[OCR engine] Initializing PaddleOCR...")

        _PADDLE_OCR = PaddleOCR(use_angle_cls=True, lang="en")

        logger.info("[OCR engine] Initialization DONE ✅")

    return _PADDLE_OCR


def _extract_text_from_image(
    image_path: str,
) -> tuple[str, bool, datetime, datetime, datetime, datetime]:
    """
    OCR for raster images (.png/.jpg/.jpeg).

    If the image is low quality, we preprocess it (resize/denoise/CLAHE/sharpen/adaptive threshold)
    and run OCR on the enhanced variants, selecting the best text output.
    """
    from backend.agents.preprocess_2 import preprocess_image_for_ocr

    def _result_to_text(result) -> str:
        lines: list[str] = []
        for line in result:
            for word in line:
                lines.append(word[1][0])
        return "\n".join(lines).strip()

    logger.info("[OCR pipeline] Starting OCR for image: %s", image_path)

    # Step 1: Preprocess
    preprocess_start_time = datetime.utcnow()
    try:
        decision = preprocess_image_for_ocr(image_path)
        preprocess_end_time = datetime.utcnow()
        logger.info(
            "[OCR pipeline → image] Preprocess completed successfully ✅ (%s candidate(s))",
            len(decision),
        )
    except Exception as e:
        preprocess_end_time = datetime.utcnow()
        logger.error("[OCR pipeline → image] Preprocess failed ❌: %s", e)
        raise

    logger.info(
        "[OCR pipeline → image] Loading PaddleOCR for extraction..."
    )

   
    ocr = _get_ocr()
   
    # Detect if enhanced image used
    deblurred_applied = any(str(cand) != str(image_path) for cand in decision)
    if deblurred_applied:
        logger.info(
            "[OCR pipeline → image] Enhanced (sharpened/deblurred) variant detected ✅"
        )

    best_text = ""
    last_error: Optional[Exception] = None

    # Step 2: OCR on candidates
    ocr_start_time = datetime.utcnow()
    for i, candidate_path in enumerate(decision, start=1):
        try:
            logger.info(
                "[OCR pipeline → PaddleOCR] Running OCR (%s/%s): %s",
                i,
                len(decision),
                candidate_path,
            )

            result = ocr.ocr(candidate_path, cls=True)
            text = _result_to_text(result)

            logger.info(
                "[OCR pipeline → PaddleOCR] OCR success ✅ (chars extracted: %s)",
                len(text),
            )

            if len(text) > len(best_text):
                best_text = text
                logger.debug(
                    "[OCR pipeline] New best candidate selected (length: %s)",
                    len(best_text),
                )

        except Exception as e:
            logger.error(
                "[OCR pipeline → PaddleOCR] OCR failed ❌ for %s: %s",
                candidate_path,
                e,
            )
            last_error = e

    # Step 3: Cleanup
    for candidate_path in decision:
        if str(candidate_path) != str(image_path):
            try:
                Path(candidate_path).unlink(missing_ok=True)
            except OSError as e:
                logger.warning(
                    "[OCR pipeline → cleanup] Failed to remove temp file: %s (%s)",
                    candidate_path,
                    e,
                )
            else:
                logger.debug(
                    "[OCR pipeline → cleanup] Removed temp file: %s",
                    candidate_path,
                )

    # Step 4: Final result
    ocr_end_time = datetime.utcnow()
    if best_text:
        logger.info(
            "[OCR pipeline] OCR completed successfully 🎉 (best text length: %s)",
            len(best_text),
        )
        return (
            best_text,
            deblurred_applied,
            preprocess_start_time,
            preprocess_end_time,
            ocr_start_time,
            ocr_end_time,
        )

    if last_error is not None:
        logger.error("[OCR pipeline] OCR failed completely ❌")
        raise last_error

    logger.warning("[OCR pipeline] No text extracted ⚠️")
    return (
        "",
        deblurred_applied,
        preprocess_start_time,
        preprocess_end_time,
        ocr_start_time,
        ocr_end_time,
    )


def _extract_text_from_pdf(pdf_path: str) -> str:
    logger.info(
        "[OCR pipeline → PDF] Opening PDF with PyMuPDF, rendering pages to images, then OCR: %s",
        pdf_path,
    )
    try:
        import numpy as np
        import pymupdf  # PyMuPDF
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Missing PDF dependencies (pymupdf/numpy): {e}") from e

    doc = pymupdf.open(pdf_path)
    try:
        parts: list[str] = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img = img[:, :, :3]
            parts.append(_extract_text_from_numpy_rgb(img))
        return "\n".join([p for p in parts if p]).strip()
    finally:
        doc.close()


def _extract_text_from_numpy_rgb(image_rgb) -> str:
    ocr = _get_ocr()
    result = ocr.ocr(image_rgb, cls=True)

    lines: list[str] = []
    for line in result:
        for word in line:
            lines.append(word[1][0])
    return "\n".join(lines).strip()


def _extract_text_from_docx(docx_path: str) -> str:
    logger.info(
        "[OCR pipeline → Word] Reading paragraphs from DOCX (no OCR): %s",
        docx_path,
    )
    from docx import Document  # python-docx

    doc = Document(docx_path)
    text = "\n".join(p.text for p in doc.paragraphs if p.text and p.text.strip())
    return text.strip()


def _gemini_extract_invoice_json(ocr_text: str) -> tuple[str, Optional[Any]]:
    load_dotenv()

    logger.info("[Gemini] Start extraction")

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("[Gemini] API key missing → skipping extraction")
        return "", None

    model_name = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"

    logger.info(
        "[Gemini] Request → model=%s | ocr_chars=%s",
        model_name,
        len(ocr_text),
    )

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
    except Exception as e:
        logger.error("[Gemini] Initialization failed ❌: %s", e)
        raise

    # Prompt handling
    prompt_template = os.environ.get("GEMINI_PROMPT")
    if prompt_template and "{ocr_text}" in prompt_template:
        prompt = prompt_template.replace("{ocr_text}", ocr_text)
    elif prompt_template:
        prompt = f"{prompt_template}\n\nOCR TEXT:\n{ocr_text}"
    else:
        prompt = f"""
Extract structured invoice data from the OCR text below.

Return STRICT JSON format with these fields:

invoice
seller
buyer
service
HSN
qnty
amount
total_amount
address
bank_name
bank_address
account_number
account_holder_name

Rules:
- If a field is not present return null
- Do not add explanations
- Return only JSON

OCR TEXT:
{ocr_text}
"""

    # API call
    try:
        logger.info("[Gemini] Calling API...")
        response = model.generate_content(prompt)
        raw = (response.text or "").strip()
        logger.info("[Gemini] Response received ✅ (chars=%s)", len(raw))
    except Exception as e:
        logger.error("[Gemini] API call failed ❌: %s", e)
        raise

    # Cleanup markdown
    cleaned = raw
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()

    # Parse JSON
    try:
        parsed = json.loads(cleaned)
        logger.info("[Gemini] JSON parsed successfully ✅")
        return raw, parsed
    except Exception:
        logger.warning(
            "[Gemini] Invalid JSON → returning raw output (chars=%s)",
            len(raw),
        )
        return raw, None


def process_file(file_path: str) -> OcrResult:
    configure_logging()
    p = Path(file_path)
    ext = p.suffix.lower()
    logger.info(
        "[OCR pipeline] Starting invoice processing pipeline for: %s (type: %s)",
        p.resolve(),
        ext or "(no extension)",
    )
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"Unsupported file type: {ext}. Allowed: {sorted(ALLOWED_EXTS)}")

    # For raster images, the next logs are from preprocess_2 (OpenCV) before any PaddleOCR line.
    if ext in {".jpeg", ".jpg", ".png"}:
        (
            ocr_text,
            deblurred_applied,
            preprocessing_start_time,
            preprocessing_end_time,
            ocr_start_time,
            ocr_end_time,
        ) = _extract_text_from_image(str(p))
    elif ext == ".pdf":
        preprocessing_start_time = None
        preprocessing_end_time = None
        ocr_start_time = datetime.utcnow()
        ocr_text = _extract_text_from_pdf(str(p))
        ocr_end_time = datetime.utcnow()
        deblurred_applied = False
    else:  # .docx
        preprocessing_start_time = None
        preprocessing_end_time = None
        ocr_start_time = datetime.utcnow()
        ocr_text = _extract_text_from_docx(str(p))
        ocr_end_time = datetime.utcnow()
        deblurred_applied = False

    logger.info(
        "[OCR pipeline] Step 1 complete — Extracted text length: %s characters.",
        len(ocr_text or ""),
    )
    logger.info(
        "[OCR pipeline] Step 2/2 — Structured data extraction (Gemini) from OCR text.",
    )
    gemini_start_time = datetime.utcnow()
    gemini_raw, gemini_json = _gemini_extract_invoice_json(ocr_text)
    gemini_end_time = datetime.utcnow()
    if isinstance(gemini_json, dict):
        additional_fields = gemini_json.get("additional_fields")
        if not isinstance(additional_fields, dict):
            additional_fields = {}
            gemini_json["additional_fields"] = additional_fields
        additional_fields["deblurred_applied"] = deblurred_applied
    r = OcrResult(
        file_path=str(p),
        ocr_text=ocr_text,
        gemini_raw_text=gemini_raw,
        gemini_json=gemini_json,
        preprocessing_start_time=preprocessing_start_time,
        preprocessing_end_time=preprocessing_end_time,
        ocr_start_time=ocr_start_time,
        ocr_end_time=ocr_end_time,
        gemini_start_time=gemini_start_time,
        gemini_end_time=gemini_end_time,
        preprocessing_latency=(
            (preprocessing_end_time - preprocessing_start_time).total_seconds()
            if preprocessing_start_time and preprocessing_end_time
            else None
        ),
        ocr_latency=(
            (ocr_end_time - ocr_start_time).total_seconds()
            if ocr_start_time and ocr_end_time
            else None
        ),
        gemini_latency=(gemini_end_time - gemini_start_time).total_seconds(),
    )

    logger.info(
        "[OCR pipeline] Finished pipeline for: %s",
        p.resolve(),
    )
    return r

