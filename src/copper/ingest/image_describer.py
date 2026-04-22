"""
Image-to-text describer for multimodal ingestion.

Sends raw image bytes to a vision-capable model and returns a short
description. Used by PDFPlugin to enrich page content with descriptions
of diagrams, tables rendered as images, maps, and infographics.

Decorative or purely flavour images are filtered upstream by pdfplumber
heuristics (size) and by the model itself (which returns "DECORATIVE"
when asked to skip non-informative content).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from core_utils.logger import logger

_DESCRIBE_PROMPT = (
    "Describe the informational content of this image in one short paragraph "
    "(max 3 sentences). Focus on text, diagrams, tables, charts, maps, or "
    "symbols that convey mechanical/factual information. "
    "If the image is purely decorative (border, portrait without labels, "
    "flavour illustration, stylised icon), reply with EXACTLY the single "
    "word: DECORATIVE"
)


@dataclass
class ImageDescriber:
    """Describes raw image bytes via a multimodal provider."""

    provider: str
    model: str
    base_url: str = "http://localhost:11434"
    timeout: int = 120

    def describe(self, image_bytes: bytes, context_hint: str = "") -> str:
        """Return a description or empty string if the image is decorative/failed."""
        if self.provider == "ollama":
            return self._describe_ollama(image_bytes, context_hint)
        logger.warning(f"[image] Provider '{self.provider}' not supported for image description")
        return ""

    def _describe_ollama(self, image_bytes: bytes, context_hint: str) -> str:
        import httpx

        b64 = base64.b64encode(image_bytes).decode()
        prompt = _DESCRIBE_PROMPT
        if context_hint:
            prompt = f"Surrounding page text:\n{context_hint[:400]}\n\n{prompt}"

        try:
            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "images": [b64],
                    "stream": False,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = (resp.json().get("response") or "").strip()
        except Exception as exc:
            logger.warning(f"[image] Description failed: {exc}")
            return ""

        # Model signalled the image is decorative — skip it.
        if text.upper().startswith("DECORATIVE"):
            return ""
        return text
