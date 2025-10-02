# core/together.py
from typing import Any, Dict, List, Tuple
import os, httpx

TOGETHER_BASE_URL = "https://api.together.xyz/v1/chat/completions"

DEFAULT_MODELS = [
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
    "together/Qwen/Qwen2.5-72B-Instruct",
    "together/Qwen/QwQ-32B",
]

def chat(model: str, messages: List[Dict[str, str]], **kwargs: Any) -> Tuple[Dict[str, Any], str, str]:
    api_key = os.environ.get("TOGETHER_API_KEY", "")
    if not api_key:
        raise RuntimeError("Together: falta TOGETHER_API_KEY.")

    body: Dict[str, Any] = {
        "model": model.replace("together/", "", 1),
        "messages": messages,
        "max_tokens": kwargs.get("max_tokens", 1024),
        "temperature": kwargs.get("temperature", 0.7),
        "top_p": kwargs.get("top_p", 0.95),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    timeout = float(os.environ.get("LLM_HTTP_TIMEOUT", "60"))
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(TOGETHER_BASE_URL, json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
            used = "together/" + body["model"]
            return data, used, "together"
    except httpx.HTTPError as e:
        raise RuntimeError(f"Falha Together: {e}") from e
