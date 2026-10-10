from __future__ import annotations

import copy
import json
from pathlib import Path

DEFAULTS: dict = {
    "source": "window",                       # window | screen | file:PATH
    "window_title_pattern": r"sim\s*city\s*3000",
    "window_exe": None,                       # e.g. "SC3U.exe" once confirmed with `python -m mayor windows`
    "capture_method": "rect",                # rect | hwnd — `doctor` tries both on Windows
    "interval_s": 15,
    "save_frame_min_change": 0.02,            # save a PNG only if >=2% of the thumbnail changed
    "extract": ["ocr"],                       # any of: "ocr", "llm"
    "rois": {},                               # field -> [x0, y0, x1, y1] fractions; measure with `calibrate`
    "tesseract_cmd": None,                    # e.g. "C:/Program Files/Tesseract-OCR/tesseract.exe"
    "llm": {
        "provider": "gemini",                 # anthropic | gemini | openai | mock
        "model": None,                        # None -> mayor.llm.DEFAULT_MODELS
        "min_interval_s": 300,                # at most one vision call per 5 min...
        "min_change": 0.05,                   # ...and only if the screen changed since the last call
        "max_calls_per_run": 50,              # hard cap on spend per run
        "max_width": 1024,
        "mock_response_file": None,
    },
    "runs_dir": "runs",
}


def _deep_update(base: dict, upd: dict) -> dict:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: str | None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path is None and Path("config.json").exists():
        path = "config.json"
    if path:
        _deep_update(cfg, json.loads(Path(path).read_text(encoding="utf-8")))
    return cfg
