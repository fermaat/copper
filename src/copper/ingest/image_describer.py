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

from copper.prompts import render_prompt

# The actual prompt text is loaded from YAML; see src/copper/prompts/image.visual.yaml.
_DESCRIBE_PROMPT_NAME = "image.visual"


@dataclass
class ImageDescriber:
    """Describes raw image bytes via a multimodal provider."""

    provider: str
    model: str
    base_url: str = "http://localhost:11434"
    timeout: int = 120

    def describe(self, image_bytes: bytes, context_hint: str = "") -> str | None:
        """Describe an image.

        Returns:
        - text: a real description.
        - "" : the model classified the image as DECORATIVE (skip silently).
        - None: the underlying call failed (HTTP error, timeout, etc.).
        """
        if self.provider == "ollama":
            return self._describe_ollama(image_bytes, context_hint)
        logger.warning(f"[image] Provider '{self.provider}' not supported for image description")
        return None

    def _describe_ollama(self, image_bytes: bytes, context_hint: str) -> str | None:
        import httpx

        b64 = base64.b64encode(image_bytes).decode()
        prompt = render_prompt(_DESCRIBE_PROMPT_NAME)
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
            return None

        # Model signalled the image is decorative — skip it.
        if text.upper().startswith("DECORATIVE"):
            logger.info("[image] → DECORATIVE (skipped)")
            return ""

        preview = text.replace("\n", " ")[:150]
        logger.info(f"[image] → {preview}{'…' if len(text) > 150 else ''}")
        return text
