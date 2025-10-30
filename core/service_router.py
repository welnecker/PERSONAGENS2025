from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from .openrouter import chat as openrouter_chat, DEFAULT_MODELS as OR_MODELS
from .together import chat as together_chat, DEFAULT_MODELS as TG_MODELS

# Modelo seguro de fallback (um que você já usa)
SAFE_FALLBACK_MODEL = "deepseek/deepseek-chat-v3-0324"

# Alias opcionais (mantive, mas agora não vamos forçar pro OpenRouter)
MODEL_ALIASES: Dict[str, str] = {
    # se um dia você quiser renomear algum modelo, põe aqui
}


def available_providers() -> List[Tuple[str, bool, str]]:
    have_or = bool(os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_TOKEN"))
    have_tg = bool(os.getenv("TOGETHER_API_KEY"))
    return [
        ("OpenRouter", have_or, "OK" if have_or else "sem chave"),
        ("Together",   have_tg, "OK" if have_tg else "sem chave"),
    ]


def list_models(provider: str | None = None) -> List[str]:
    if provider == "OpenRouter":
        return OR_MODELS[:]
    if provider == "Together":
        return TG_MODELS[:]
    return OR_MODELS[:] + TG_MODELS[:]


# -------------------------------
# helper para decidir o provedor
# -------------------------------
def _provider_for(model_id: str) -> str:
    m = (model_id or "").lower().strip()

    # 1) modelos do Together por prefixo
    if m.startswith("together/"):
        return "Together"

    # 2) 👉 teu caso: deepseek-ai/... está vindo do Together
    if m.startswith("deepseek-ai/"):
        return "Together"

    # 3) moonshotai também pode estar vindo do Together no teu setup
    if m.startswith("moonshotai/"):
        return "Together"

    # 4) modelos google que você pôs
    if m.startswith("google/"):
        return "Together"

    # padrão: OpenRouter
    return "OpenRouter"


def _normalize_model_id(raw: str) -> str:
    if not raw:
        return SAFE_FALLBACK_MODEL
    low = raw.lower().strip()
    if low in MODEL_ALIASES:
        return MODEL_ALIASES[low]
    return raw


def chat(model: str, messages: List[Dict[str, str]], **kwargs: Any):
    norm_model = _normalize_model_id(model)
    provider = _provider_for(norm_model)

    if provider == "Together":
        # manda direto pro Together
        return together_chat(norm_model, messages, **kwargs)

    # senão, OpenRouter
    try:
        return openrouter_chat(norm_model, messages, **kwargs)
    except RuntimeError as e:
        msg = str(e).lower()
        if "not a valid model id" in msg or "model_not_found" in msg:
            # tenta com fallback
            return openrouter_chat(SAFE_FALLBACK_MODEL, messages, **kwargs)
        raise


def route_chat_strict(model: str, payload: Dict[str, Any]):
    norm_model = _normalize_model_id(model)
    provider = _provider_for(norm_model)

    msgs = payload.get("messages", [])
    kwargs = {
        "max_tokens": payload.get("max_tokens", 1024),
        "temperature": payload.get("temperature", 0.7),
        "top_p": payload.get("top_p", 0.95),
    }

    if provider == "Together":
        return together_chat(norm_model, msgs, **kwargs)

    try:
        return openrouter_chat(norm_model, msgs, **kwargs)
    except RuntimeError as e:
        msg = str(e).lower()
        if "not a valid model id" in msg or "model_not_found" in msg:
            return openrouter_chat(SAFE_FALLBACK_MODEL, msgs, **kwargs)
        raise
