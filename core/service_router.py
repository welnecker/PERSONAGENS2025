# core/service_router.py
from __future__ import annotations

import os
from typing import Dict, List, Tuple, Any

from .openrouter import chat as openrouter_chat, DEFAULT_MODELS as OR_MODELS
from .together import chat as together_chat, DEFAULT_MODELS as TG_MODELS


# ==================== Provedores & Modelos (para UI) ====================

def available_providers() -> List[Tuple[str, bool, str]]:
    """
    Retorna [(nome, configurado, detalhe)].
    Lista OpenRouter e Together; 'configurado' reflete presença da chave.
    """
    have_or = bool(os.environ.get("OPENROUTER_API_KEY"))
    have_tg = bool(os.environ.get("TOGETHER_API_KEY"))
    return [
        ("OpenRouter", have_or, "OK" if have_or else "sem chave"),
        ("Together",   have_tg, "OK" if have_tg else "sem chave"),
    ]


def list_models(provider: str | None = None) -> List[str]:
    """
    Modelos sugeridos para UI. Se provider=None, concatena ambos.
    provider deve ser "OpenRouter" ou "Together" (sensível a maiúsculas).
    """
    if provider == "Together":
        return TG_MODELS[:]
    if provider == "OpenRouter":
        return OR_MODELS[:]
    return OR_MODELS[:] + TG_MODELS[:]


# ==================== Normalização de resposta ====================

def _extract_text(raw: Dict[str, Any]) -> str:
    """
    Extrai o texto da resposta em diferentes formatos possíveis
    (OpenRouter/OpenAI-like e Together-like).
    """
    if not isinstance(raw, dict):
        return ""

    # OpenAI/OpenRouter-like
    try:
        choices = raw.get("choices") or []
        if choices and isinstance(choices[0], dict):
            ch0 = choices[0]
            # 1) mensagem.content
            msg = ch0.get("message") or {}
            if isinstance(msg, dict):
                txt = msg.get("content") or ""
                if txt:
                    return str(txt)
            # 2) text direto
            txt = ch0.get("text") or ""
            if txt:
                return str(txt)
    except Exception:
        pass

    # Together-like: {"output":{"choices":[{"message":{"content":"..."}}]}}
    try:
        out = raw.get("output") or {}
        choices = out.get("choices") or []
        if choices and isinstance(choices[0], dict):
            ch0 = choices[0]
            msg = ch0.get("message") or {}
            if isinstance(msg, dict):
                txt = msg.get("content") or ""
                if txt:
                    return str(txt)
            txt = ch0.get("text") or ""
            if txt:
                return str(txt)
    except Exception:
        pass

    return ""


def _normalize_chat_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Força o shape compatível com OpenAI:
    {'choices':[{'message':{'content': <texto>}}]}
    """
    txt = _extract_text(raw) or ""
    return {"choices": [{"message": {"content": txt}}]}


# ==================== Roteamento de chamadas ====================

def chat(model: str, messages: List[Dict[str, str]], **kwargs: Any) -> Tuple[Dict[str, Any], str, str]:
    """
    Envia ao provedor conforme o prefixo do modelo:
      - começa com 'together/' → Together
      - senão → OpenRouter
    Retorna (data_json_raw, used_model, provider_tag).
    """
    if model.startswith("together/"):
        return together_chat(model, messages, **kwargs)
    return openrouter_chat(model, messages, **kwargs)


# ---------- Compatibilidade com código legado ----------
def route_chat_strict(model: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
    """
    Compat: aceita payload no formato antigo:
      {
        "model": "...",
        "messages": [...],
        "max_tokens": int,
        "temperature": float,
        "top_p": float,
        # (opcionais) presence_penalty, frequency_penalty, stop
      }
    Ignora chaves extras desconhecidas.
    Sempre retorna normalizado (choices[0].message.content).
    """
    msgs = payload.get("messages", []) or []
    kwargs = {
        "max_tokens":       payload.get("max_tokens", 1024),
        "temperature":      payload.get("temperature", 0.7),
        "top_p":            payload.get("top_p", 0.95),
        "presence_penalty": payload.get("presence_penalty"),
        "frequency_penalty":payload.get("frequency_penalty"),
        "stop":             payload.get("stop"),
    }
    # remove None para não poluir chamadas
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    raw, used, provider_tag = chat(model, msgs, **kwargs)
    return _normalize_chat_result(raw), used, provider_tag
