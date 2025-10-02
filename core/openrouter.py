# core/openrouter.py
from typing import Any, Dict, List, Tuple
import os, httpx

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODELS = [
    "deepseek/deepseek-chat-v3-0324",
    "anthropic/claude-3.5-haiku",
    "qwen/qwen3-max",
    "nousresearch/hermes-3-llama-3.1-405b",
]

def chat(model: str, messages: List[Dict[str, str]], **kwargs: Any) -> Tuple[Dict[str, Any], str, str]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OpenRouter: falta OPENROUTER_API_KEY.")

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": kwargs.get("max_tokens", 1024),
        "temperature": kwargs.get("temperature", 0.7),
        "top_p": kwargs.get("top_p", 0.95),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.environ.get("OR_REFERER", "http://localhost"),
        "X-Title": os.environ.get("OR_TITLE", "PERSONAGENS2025"),
    }

    timeout = float(os.environ.get("LLM_HTTP_TIMEOUT", "60"))
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(OPENROUTER_BASE_URL, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            used = body["model"]
            return data, used, "openrouter"
    except httpx.HTTPError as e:
        raise RuntimeError(f"Falha OpenRouter: {e}") from e
