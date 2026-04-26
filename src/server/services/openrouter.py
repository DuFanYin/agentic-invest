import json
import os

import httpx


class OpenRouterClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.model = model or os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if os.getenv("OPENROUTER_HTTP_REFERER"):
            headers["HTTP-Referer"] = os.getenv("OPENROUTER_HTTP_REFERER", "")
        if os.getenv("OPENROUTER_APP_TITLE"):
            headers["X-Title"] = os.getenv("OPENROUTER_APP_TITLE", "")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only valid JSON. Do not include markdown, comments, or extra text."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover
            raise RuntimeError("OpenRouter response missing message content.") from exc

        if isinstance(content, list):
            content = "".join(
                chunk.get("text", "") if isinstance(chunk, dict) else str(chunk) for chunk in content
            )

        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("OpenRouter returned empty content.")

        # Validate JSON early so callers can rely on strict structured output.
        try:
            json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenRouter did not return valid JSON: {content}") from exc

        return content
