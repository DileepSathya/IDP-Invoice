import logging
import os
import tempfile

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _imread_bgr(path: str):
    """
    Load a BGR image. Tries cv2.imread first; if None, uses imdecode (helps on Windows when the path
    has non-ASCII characters or imread fails for other reasons).
    """
    img = cv2.imread(path)
    if img is not None:
        return img
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if decoded is not None:
            logger.warning(
                "[Image preprocess] cv2.imread failed; loaded via imdecode — file: %s",
                path,
            )
        return decoded
    except OSError as e:
        logger.error("[Image preprocess] Could not read image bytes: %s — %s", path, e)
        return None


def preprocess_image_for_ocr(image_path):
    """
    Return a list of candidate image paths for OCR.

    The caller can run OCR on the original and (optionally) an enhanced variant.
    """
    logger.info(
        "[Image preprocess] Checking readability (OpenCV) before OCR — location: "
        "backend.agents.preprocess_2.preprocess_image_for_ocr | file: %s",
        image_path,
    )
    img = _imread_bgr(image_path)
    if img is None:
        raise ValueError(
            f"Failed to read image (cv2.imread and imdecode both failed): {image_path}"
        )

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # ---------- 1. SHARPNESS SCORE ----------
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()

    # Normalize sharpness (tune these values)
    sharpness_score = min(laplacian_var / 100, 10)

    # ---------- 2. CONTRAST SCORE ----------
    contrast = gray.std()
    contrast_score = min(contrast / 25, 10)

    # ---------- FINAL READABILITY SCORE ----------
    readability_score = (0.7 * sharpness_score) + (0.3 * contrast_score)

    logger.info(
        "[Image preprocess] Readability score: %.2f / 10 (threshold 6.0 — below means sharpen).",
        readability_score,
    )

    # ---------- CONDITION ----------
    if readability_score < 6:
        logger.info(
            "[Image preprocess] Score is low; applying sharpen kernel, then writing a temporary "
            "file in the system temp folder (not next to your original — avoids double-processing "
            "by the folder watcher).",
        )

        kernel = np.array([[0,-1,0],
                        [-1,5,-1],
                        [0,-1,0]])

        img = cv2.filter2D(img, -1, kernel)

        # Write outside to_be_processed: saving next to the original retriggers the folder watcher
        # (e.g. watch_raw.py) and runs OCR / extraction twice.
        _, ext = image_path.rsplit(".", 1) if "." in image_path else (image_path, "png")
        fd, new_img_path = tempfile.mkstemp(suffix=f".{ext}", prefix="idp_sharp_")
        os.close(fd)
        logger.info(
            "[Image preprocess] Saved sharpened candidate for OCR at: %s",
            new_img_path,
        )
        if not cv2.imwrite(new_img_path, img):
            enc_ok, buf = cv2.imencode(".png", img)
            if not enc_ok:
                raise ValueError(f"Failed to write sharpened image to: {new_img_path}")
            buf.tofile(new_img_path)
            logger.warning(
                "[Image preprocess] cv2.imwrite failed; wrote PNG bytes via imencode — %s",
                new_img_path,
            )

        return [new_img_path]
    logger.info(
        "[Image preprocess] Image is clear enough; OCR will run on the original file only.",
    )

    return [image_path]