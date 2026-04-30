"""HuggingFace Inference API embedding provider.

Default model: ``sentence-transformers/all-MiniLM-L6-v2`` (384-dim). Single
vector per call; the HF feature-extraction pipeline accepts either ``str`` or
``list[str]`` as ``inputs`` and returns a flat ``list[float]`` for a single
input or ``list[list[float]]`` for a batch. Some models wrap the single-input
response in an extra list; we unwrap that case.

Auth is resolved lazily on the first call. No automatic retries in v1; no
async variant.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Final

import httpx

_DEFAULT_BASE_URL: Final[str] = "https://api-inference.huggingface.co/pipeline/feature-extraction"


@dataclass(frozen=True, slots=True)
class HuggingFaceProvider:
    """HuggingFace Inference API embedding provider."""

    model_id: str = "sentence-transformers/all-MiniLM-L6-v2"
    dimensions: int = 384
    api_key_env: str = "HF_API_KEY"
    api_key: str | None = None
    base_url: str = _DEFAULT_BASE_URL
    timeout_seconds: float = 30.0

    @property
    def name(self) -> str:
        return f"huggingface/{self.model_id.split('/')[-1]}"

    def embed_text(self, text: str, *, _client: httpx.Client | None = None) -> list[float]:
        data = self._post_inference({"inputs": text}, client=_client)
        vec = self._unwrap_single(data)
        self._validate_dimensions(vec)
        return vec

    def embed_batch(
        self, texts: list[str], *, _client: httpx.Client | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        data = self._post_inference({"inputs": texts}, client=_client)
        if not isinstance(data, list) or not all(isinstance(row, list) for row in data):
            raise ValueError(
                f"unexpected batch response shape from {self.name}: expected list[list[float]]"
            )
        vectors: list[list[float]] = [[float(x) for x in row] for row in data]
        if len(vectors) != len(texts):
            raise ValueError(
                f"{self.name} returned {len(vectors)} embeddings for {len(texts)} inputs"
            )
        for vec in vectors:
            self._validate_dimensions(vec)
        return vectors

    def _resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        env_key = os.environ.get(self.api_key_env)
        if env_key:
            return env_key
        raise RuntimeError(
            f"HuggingFace API key not provided; set {self.api_key_env} "
            f"environment variable or pass api_key= explicitly"
        )

    def _post_inference(self, payload: dict[str, Any], *, client: httpx.Client | None) -> Any:
        api_key = self._resolve_api_key()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        url = f"{self.base_url}/{self.model_id}"
        owns_client = client is None
        active = client or httpx.Client(timeout=self.timeout_seconds)
        try:
            try:
                response = active.post(url, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                raise RuntimeError(
                    f"HuggingFace request timed out after {self.timeout_seconds}s "
                    f"for model {self.model_id}"
                ) from exc
            self._raise_for_http_error(response)
            return response.json()
        finally:
            if owns_client:
                active.close()

    @staticmethod
    def _raise_for_http_error(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"HuggingFace authentication failed (HTTP {response.status_code}); check API key"
            )
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            hint = f" (retry-after: {retry_after}s)" if retry_after else ""
            raise RuntimeError(f"HuggingFace rate limit exceeded (HTTP 429){hint}")
        raise RuntimeError(f"HuggingFace request failed: HTTP {response.status_code}")

    def _unwrap_single(self, data: Any) -> list[float]:
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], list):
            data = data[0]
        if not isinstance(data, list) or not all(isinstance(x, (int, float)) for x in data):
            raise ValueError(
                f"unexpected single-input response shape from {self.name}: expected list[float]"
            )
        return [float(x) for x in data]

    def _validate_dimensions(self, vec: list[float]) -> None:
        if len(vec) != self.dimensions:
            raise ValueError(
                f"{self.name} returned {len(vec)}-dim embedding; expected {self.dimensions}"
            )


__all__ = ["HuggingFaceProvider"]
