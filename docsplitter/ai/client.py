"""Thin async wrapper around any OpenAI-compatible vision API."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from openai import AsyncAzureOpenAI, AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from docsplitter.config import AIConfig

logger = logging.getLogger(__name__)


class AIClient:
    def __init__(self, cfg: AIConfig) -> None:
        self._cfg = cfg
        if cfg.api_version:
            self._client: AsyncOpenAI = AsyncAzureOpenAI(
                api_key=cfg.api_key,
                azure_endpoint=cfg.base_url,
                api_version=cfg.api_version,
                timeout=cfg.timeout_seconds,
                max_retries=0,
            )
        else:
            self._client = AsyncOpenAI(
                api_key=cfg.api_key or "no-key",   # some local models ignore the key
                base_url=cfg.base_url,
                timeout=cfg.timeout_seconds,
                max_retries=0,                      # tenacity handles retries
            )

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type(Exception),
    )
    async def analyse_page(
        self,
        *,
        system_prompt: str,
        user_text: str,
        images: list[Path | bytes],             # ordered list of page images
        image_format: str = "jpeg",
    ) -> dict[str, Any]:
        """
        Call the vision model with a system prompt, user text, and 1-3 page images.
        Returns the parsed JSON response dict.
        Raises ValueError if the model returns invalid JSON.
        """
        content: list[dict[str, Any]] = []

        for img in images:
            if isinstance(img, Path):
                raw = img.read_bytes()
            else:
                raw = img
            b64 = base64.b64encode(raw).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/{image_format};base64,{b64}",
                        "detail": self._cfg.image_detail,
                    },
                }
            )

        content.append({"type": "text", "text": user_text})

        logger.debug(
            "AI call: model=%s images=%d prompt_len=%d",
            self._cfg.model,
            len(images),
            len(user_text),
        )

        response = await self._client.chat.completions.create(
            model=self._cfg.model,
            max_tokens=self._cfg.max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )

        raw_text = response.choices[0].message.content or ""
        logger.debug("AI response: %s", raw_text[:200])

        # Strip markdown code fences if model ignores our instruction
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("Model returned non-JSON: %s", raw_text[:300])
            raise ValueError(f"Model returned non-JSON response: {e}") from e

    @property
    def model(self) -> str:
        return self._cfg.model
