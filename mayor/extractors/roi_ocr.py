"""Free, local reader: OCR of user-calibrated screen regions (Tesseract).

No region positions are built in: the SC3 HUD layout depends on version,
resolution and open panels, and has to be measured on a real screenshot
(`python -m mayor calibrate`). Regions are fractions of the frame:
    {"funds": [x0, y0, x1, y1], ...}   with 0..1 coordinates.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PIL import Image, ImageOps

from ..state import OBSERVED, REJECTED, SPEC_BY_NAME, UNKNOWN, CityState, Field, normalize_value

Roi = tuple[float, float, float, float]


def roi_box(img: Image.Image, roi: Roi) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = roi
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError(f"ROI must be 0<=x0<x1<=1, 0<=y0<y1<=1, got {roi}")
    return round(x0 * img.width), round(y0 * img.height), round(x1 * img.width), round(y1 * img.height)


def crop_roi(img: Image.Image, roi: Roi) -> Image.Image:
    return img.crop(roi_box(img, roi))


def preprocess(crop: Image.Image, scale: int = 3) -> Image.Image:
    g = ImageOps.grayscale(crop)
    g = g.resize((g.width * scale, g.height * scale), Image.LANCZOS)
    return ImageOps.autocontrast(g)


def tesseract_available() -> tuple[bool, str]:
    try:
        import pytesseract
    except ImportError:
        return False, "pytesseract not installed (pip install pytesseract)"
    try:
        return True, f"tesseract {pytesseract.get_tesseract_version()}"
    except Exception as e:  # binary missing / not on PATH
        return False, f"tesseract binary not found ({e.__class__.__name__}); install it and/or set pytesseract.tesseract_cmd"


class RoiOcrExtractor:
    name = "ocr"

    def __init__(self, rois: dict[str, Roi], tesseract_cmd: str | None = None):
        unknown = [k for k in rois if k not in SPEC_BY_NAME]
        if unknown:
            raise ValueError(f"ROIs for unknown fields: {unknown}")
        self.rois = rois
        if tesseract_cmd:
            import pytesseract
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        self.ok, self.status = tesseract_available()

    def _ocr(self, img: Image.Image) -> tuple[str, float]:
        import pytesseract
        data = pytesseract.image_to_data(img, config="--psm 7", output_type=pytesseract.Output.DICT)
        words = [(w, float(c)) for w, c in zip(data["text"], data["conf"]) if str(w).strip() and float(c) >= 0]
        if not words:
            return "", 0.0
        text = " ".join(w for w, _ in words)
        conf = sum(c for _, c in words) / len(words) / 100.0
        return text, conf

    def extract(self, img: Image.Image) -> CityState:
        st = CityState()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.rois:
            return st
        if not self.ok:
            st.warnings.append(f"ocr skipped: {self.status}")
            return st
        for name, roi in self.rois.items():
            text, conf = self._ocr(preprocess(crop_roi(img, roi)))
            if not text:
                st.set(name, Field(status=UNKNOWN, source=f"ocr:{name}", evidence="no text found in region"))
                continue
            value, err = normalize_value(name, text)
            if err:
                st.set(name, Field(status=REJECTED, source=f"ocr:{name}", evidence=f"ocr read {text!r}: {err}"))
            else:
                st.set(name, Field(value, OBSERVED, round(conf, 2), f"ocr:{name}", f"ocr read {text!r}", now))
        return st
