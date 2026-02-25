"""Titan OCR — leitura numérica robusta para mesas PPPoker/LDPlayer.

Responsável por extrair valores monetários (pot, stack, call) de recortes
de imagem com foco em baixa latência e tolerância a ruído visual.

Estratégia de robustez:
1. Pré-processamento (grayscale + upscale + threshold binário).
2. OCR com whitelist estrita ``0123456789.$,``.
3. Sanitização agressiva para remover lixo (ex.: ``A50`` -> ``50``).
4. Fallback para último valor válido por chave (ou ``0.0``).
"""

from __future__ import annotations

import re
from typing import Any


class TitanOCR:
    """OCR numérico com fallback seguro para uso em loop de decisão."""

    def __init__(
        self,
        *,
        use_easyocr: bool = False,
        tesseract_cmd: str | None = None,
    ) -> None:
        self.use_easyocr = bool(use_easyocr)
        self._easy_reader: Any | None = None
        self._last_values: dict[str, float] = {}

        self._cv2: Any | None = None
        self._np: Any | None = None
        self._pytesseract: Any | None = None

        self._load_backends(tesseract_cmd=tesseract_cmd)

    def _load_backends(self, tesseract_cmd: str | None = None) -> None:
        try:
            import cv2  # type: ignore[import-untyped]
            self._cv2 = cv2
        except Exception:
            self._cv2 = None

        try:
            import numpy as np
            self._np = np
        except Exception:
            self._np = None

        try:
            import pytesseract  # type: ignore[import-untyped]

            # Resolve tesseract binary path
            cmd = tesseract_cmd
            if not cmd:
                import shutil, platform

                if platform.system() == "Windows":
                    _candidates = [
                        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                    ]
                    for _c in _candidates:
                        import os
                        if os.path.isfile(_c):
                            cmd = _c
                            break

                if not cmd and shutil.which("tesseract"):
                    cmd = shutil.which("tesseract")

            if cmd:
                pytesseract.pytesseract.tesseract_cmd = cmd
            self._pytesseract = pytesseract
        except Exception:
            self._pytesseract = None

        if self.use_easyocr:
            try:
                import easyocr  # type: ignore[import-untyped]

                self._easy_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            except Exception:
                self._easy_reader = None

    def _build_candidates(self, image_crop: Any) -> list[Any]:
        """Build all preprocessing candidates for OCR.

        Returns a list of binary images, each representing a different
        preprocessing strategy.  The caller tries OCR on each and picks
        the best result.

        Strategies (in order):
        0. Yellow-only HSV mask (PPPoker gold text)
        1. Yellow + strict-white HSV mask (combined)
        2. Strict-white-only HSV mask
        3. CLAHE + OTSU
        4. Adaptive Gaussian threshold
        5. Fixed threshold at 140
        """
        if image_crop is None:
            return []
        if self._cv2 is None or self._np is None:
            return [image_crop] if image_crop is not None else []

        cv2 = self._cv2
        np = self._np
        candidates: list[Any] = []

        try:
            frame = image_crop

            # ── Colour isolation strategies ───────────────────────
            if len(frame.shape) == 3:
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

                # Yellow/gold text mask — PPPoker pot numbers
                yellow_mask = cv2.inRange(hsv, (10, 80, 80), (35, 255, 255))
                # Strict white text mask — PPPoker stack & button numbers
                white_mask = cv2.inRange(hsv, (0, 0, 220), (180, 30, 255))
                # Combined: catches both yellow and white text
                combined_mask = cv2.bitwise_or(yellow_mask, white_mask)

                h, w = yellow_mask.shape[:2]
                scale = 4
                kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))

                for mask in [yellow_mask, combined_mask, white_mask]:
                    up = cv2.resize(
                        mask, (w * scale, h * scale),
                        interpolation=cv2.INTER_NEAREST,
                    )
                    up = cv2.dilate(up, kernel, iterations=1)
                    candidates.append(up)

            # ── Grayscale strategies ──────────────────────────────
            if len(frame.shape) == 3:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                gray = frame

            h, w = gray.shape[:2]
            if h <= 0 or w <= 0:
                return candidates

            scale = 3
            upscaled = cv2.resize(
                gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC
            )
            blurred = cv2.GaussianBlur(upscaled, (3, 3), 0)

            # Strategy 3: CLAHE + OTSU
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
            enhanced = clahe.apply(blurred)
            _, thresh1 = cv2.threshold(
                enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )

            # Strategy 4: Adaptive threshold
            thresh2 = cv2.adaptiveThreshold(
                blurred, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                blockSize=15,
                C=-5,
            )

            # Strategy 5: Fixed threshold
            _, thresh3 = cv2.threshold(blurred, 140, 255, cv2.THRESH_BINARY)

            for thresh_img in [thresh1, thresh2, thresh3]:
                k = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
                cleaned = cv2.morphologyEx(thresh_img, cv2.MORPH_CLOSE, k)
                white_frac = float(np.mean(cleaned > 127))
                if white_frac < 0.3:
                    cleaned = cv2.bitwise_not(cleaned)
                candidates.append(cleaned)

            # Strategy 6: Aggressive CLAHE for low-contrast button text
            # PPPoker buttons have lighter text on coloured buttons (very
            # low contrast).  High clipLimit + OTSU can extract it.
            clahe_agg = cv2.createCLAHE(clipLimit=10.0, tileGridSize=(2, 2))
            up_6x = cv2.resize(
                gray, (w * 6, h * 6), interpolation=cv2.INTER_CUBIC
            )
            enhanced_agg = clahe_agg.apply(up_6x)
            _, thresh_agg = cv2.threshold(
                enhanced_agg, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
            white_frac_agg = float(np.mean(thresh_agg > 127))
            if white_frac_agg < 0.3:
                thresh_agg = cv2.bitwise_not(thresh_agg)
            candidates.append(thresh_agg)

            return candidates
        except Exception:
            return [image_crop]

    def _ocr_with_tesseract(self, image: Any) -> str:
        if self._pytesseract is None:
            return ""
        try:
            config = "--psm 7 -c tessedit_char_whitelist=0123456789.$,Kk"
            text = self._pytesseract.image_to_string(image, config=config)
            return text.strip()
        except Exception:
            return ""

    def _ocr_with_easyocr(self, image: Any) -> str:
        if self._easy_reader is None:
            return ""
        try:
            results = self._easy_reader.readtext(
                image,
                detail=0,
                allowlist="0123456789.$,",
                paragraph=False,
            )
            if not results:
                return ""
            return " ".join(str(item) for item in results).strip()
        except Exception:
            return ""

    @staticmethod
    def _parse_numeric_text(text: str) -> float | None:
        """Converte texto OCR para float, removendo ruído comum.

        Handles PPPoker-specific formatting:
        - "K" suffix for thousands (e.g., "5.4K" → 5400)
        - Comma as decimal or thousands separator
        - Common OCR misreads (O→0, S→5)
        """
        if not text:
            return None

        cleaned = text.strip().replace(" ", "")
        cleaned = cleaned.replace("O", "0").replace("o", "0")
        cleaned = cleaned.replace("S", "5").replace("s", "5")
        cleaned = cleaned.replace("$", "")

        # Detect K/k suffix (thousands multiplier) before stripping
        has_k = cleaned.upper().endswith("K")
        if has_k:
            cleaned = cleaned[:-1]

        # Mantém só dígitos e separadores
        cleaned = re.sub(r"[^0-9.,]", "", cleaned)
        if not cleaned:
            return None

        # Normalização de separadores
        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(",", ".")
        elif "," in cleaned and "." in cleaned:
            # Ex.: 1,234.56 -> remove separadores de milhar
            cleaned = cleaned.replace(",", "")

        # Captura primeiro número válido
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if match is None:
            return None

        try:
            value = float(match.group(0))
        except ValueError:
            return None

        if has_k:
            value *= 1000

        if value < 0:
            return None
        if value > 100_000_000:
            return None
        return value

    def read_numeric_region(
        self,
        image_crop: Any,
        *,
        key: str | None = None,
        fallback: float = 0.0,
    ) -> float:
        """Lê um recorte numérico e retorna ``float`` com fallback seguro.

        Tries ALL preprocessing candidates with OCR and picks the best
        result — the valid number with the fewest digits (least noisy).
        This multi-attempt approach is critical because PPPoker uses
        different text colours (yellow pot, white stack) on varied
        backgrounds, and no single preprocessing strategy works for all.

        Args:
            image_crop: Recorte BGR/gray da região de interesse.
            key: Identificador lógico (``pot``, ``stack``, ``call_amount``),
                 usado para manter último valor válido por métrica.
            fallback: Valor de fallback quando OCR falha.

        Returns:
            Valor numérico reconhecido; se falhar retorna último válido
            da chave (se existir) ou ``fallback``.
        """
        effective_fallback = float(fallback)
        if key is not None and key in self._last_values:
            effective_fallback = self._last_values[key]

        if image_crop is None:
            return effective_fallback

        candidates = self._build_candidates(image_crop)
        if not candidates:
            return effective_fallback

        # Try each candidate; collect all valid results.
        # To limit latency, stop after finding 3 successful readings.
        results: list[float] = []
        max_attempts = 3
        for candidate in candidates:
            if len(results) >= max_attempts:
                break
            value = self._try_ocr(candidate)
            if value is not None and value > 0:
                results.append(value)
                continue
            # Also try inverted polarity
            if self._cv2 is not None and candidate is not None:
                try:
                    inv = self._cv2.bitwise_not(candidate)
                    value_inv = self._try_ocr(inv)
                    if value_inv is not None and value_inv > 0:
                        results.append(value_inv)
                except Exception:
                    pass

        if not results:
            return effective_fallback

        # Pick best result using a scoring system:
        # 1. Prefer values closest to last known value (temporal coherence)
        # 2. Otherwise prefer shortest digit count (least noisy OCR)
        # 3. Break ties by preferring values seen more than once (consensus)
        best = results[0]
        if key is not None and key in self._last_values:
            last = self._last_values[key]
            # Temporal coherence: prefer value closest to last known
            best = min(results, key=lambda v: abs(v - last))
        else:
            # Count occurrences for consensus
            from collections import Counter
            rounded = Counter(round(v, 1) for v in results)
            # Sort by (-frequency, digit_count) to prefer consensus + shorter
            best = min(
                results,
                key=lambda v: (-rounded[round(v, 1)], len(str(int(v)))),
            )

        if key is not None:
            self._last_values[key] = best
        return best

    def _try_ocr(self, image: Any) -> float | None:
        """Attempt OCR on a preprocessed image, return parsed value or None."""
        if image is None:
            return None
        text = self._ocr_with_tesseract(image)
        if not text and self.use_easyocr:
            text = self._ocr_with_easyocr(image)
        return self._parse_numeric_text(text)
