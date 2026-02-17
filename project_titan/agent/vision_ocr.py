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

            if tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
            self._pytesseract = pytesseract
        except Exception:
            self._pytesseract = None

        if self.use_easyocr:
            try:
                import easyocr  # type: ignore[import-untyped]

                self._easy_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
            except Exception:
                self._easy_reader = None

    def _preprocess(self, image_crop: Any) -> Any | None:
        """Pré-processa o recorte para maximizar OCR de dígitos.

        Pipeline:
        - Conversão para grayscale
        - Upscale 2x (interpolação cúbica)
        - Blur suave
        - Threshold OTSU binário
        - Fechamento morfológico leve
        """
        if image_crop is None:
            return None
        if self._cv2 is None or self._np is None:
            return image_crop

        cv2 = self._cv2

        try:
            frame = image_crop
            if len(frame.shape) == 3:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                gray = frame

            h, w = gray.shape[:2]
            if h <= 0 or w <= 0:
                return None

            upscaled = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
            blurred = cv2.GaussianBlur(upscaled, (3, 3), 0)
            _, thresh = cv2.threshold(
                blurred,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU,
            )

            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            processed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
            return processed
        except Exception:
            return image_crop

    def _ocr_with_tesseract(self, image: Any) -> str:
        if self._pytesseract is None:
            return ""
        try:
            config = "--psm 7 -c tessedit_char_whitelist=0123456789.$,"
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
        """Converte texto OCR para float, removendo ruído comum."""
        if not text:
            return None

        cleaned = text.strip().replace(" ", "")
        cleaned = cleaned.replace("O", "0").replace("o", "0")
        cleaned = cleaned.replace("S", "5").replace("s", "5")
        cleaned = cleaned.replace("$", "")

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

        if value < 0:
            return None
        if value > 1_000_000:
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

        processed = self._preprocess(image_crop)
        if processed is None:
            return effective_fallback

        text = self._ocr_with_tesseract(processed)
        if not text and self.use_easyocr:
            text = self._ocr_with_easyocr(processed)

        value = self._parse_numeric_text(text)
        if value is None:
            return effective_fallback

        if key is not None:
            self._last_values[key] = value
        return value
