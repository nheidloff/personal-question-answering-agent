from __future__ import annotations

from typing import Any
import json

import requests

from .config import Settings


class ModelClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        payload = {
            "model": self._settings.model_embed_model,
            "input": texts,
        }
        if self._settings.debug:
            print("\n--- Embed Payload ---")
            print(json.dumps(payload, indent=2))
            print("-" * 21 + "\n")

        response = requests.post(
            f"{self._settings.model_base_url}/embeddings",
            json=payload,
            timeout=300,
        )
        response.raise_for_status()
        data = response.json().get("data", [])
        return [entry["embedding"] for entry in data]

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        payload: dict[str, Any] = {
            "model": self._settings.model_chat_model,
            "messages": messages,
            "temperature": temperature,
        }
        if self._settings.debug:
            print("\n--- Chat Payload ---")
            print(json.dumps(payload, indent=2))
            print("-" * 20 + "\n")

        response = requests.post(
            f"{self._settings.model_base_url}/chat/completions",
            json=payload,
            timeout=300,
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        if not choices:
            raise RuntimeError("No chat choices")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("Empty answer")
        return content
