from __future__ import annotations

import os
from typing import Dict, List, Tuple, Any

from .openrouter import chat as openrouter_chat, DEFAULT_MODELS as OR_MODELS
from .together  import chat as together_chat,  DEFAULT_MODELS as TG_MODELS


def available_providers() -> List[Tuple[str, bool, str]]:
    have_or = bool(os.environ.get("OPENROUTER_API_KEY"))
    have_tg = bool(os.environ.get("TOGETHER_API_KEY"))
    return [
        ("OpenRouter", have_or, "OK" if have_or else "sem chave"),
        ("Together",   have_tg, "OK" if have_tg else "sem chave"),
    ]


def list_models(provider: str | None = None) -> List[str]:
    if provider == "Together":
        return TG_MODELS[:]
    if provider == "OpenRouter":
        return OR_MODELS[:]
    return OR_MODELS[:] + TG_MODELS[:]


# ---------- Normalização de resposta ----------
def _normalize_chat_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Garante que exista data['choices'][0]['message']['content'].
    Converte formatos alternativos (text/output_text).
    Propaga 'error' como exceção através do chamador.
    """
    if not isinstance(data, dict):
        return {"choices": [{"message": {"role": "assistant", "content": str(data)}}]}

    if "error" in data and data["error"]:
        # deixe o provedor lançar; apenas mantenha aqui para fallback
        return data

    # Together, em alguns casos, usa 'choices[0].text'
    try:
        ch0 = (data.get("choices") or [{}])[0]
        if "message" not in ch0 and "text" in ch0:
            ch0["message"] = {"role": "assistant", "content": ch0.get("text", "")}
            data["choices"][0] = ch0
    except Exception:
        pass

    # Alguns provedores retornam 'output_text'
    if not (data.get("choices") and data["choices"][0].get("message")):
        txt = data.get("output_text") or data.get("content") or ""
        if txt:
            data["choices"] = [{"message": {"role": "assistant", "content": str(txt)}}]

    # fallback final para evitar string vazia no app
    if not (data.get("choices") and data["choices"][0].get("message", {}).get("content")):
        data["choices"] = [{"message": {"role": "assistant", "content": ""}}]

    return data


def chat(model: str, messages: List[Dict[str, str]], **kwargs: Any) -> Tuple[Dict[str, Any], str, str]:
    """
    Envia ao provedor conforme o prefixo do modelo:
      - começa com 'together/' → Together
      - senão → OpenRouter
    Retorna (data_json NORMALIZADO, used_model, provider_tag).
    """
    if model.startswith("together/"):
        data, used, prov = together_chat(model, messages, **kwargs)
    else:
        data, used, prov = openrouter_chat(model, messages, **kwargs)

    # Normaliza o payload para o formato OpenAI-like
    norm = _normalize_chat_response(data)

    # Se o provedor retornou erro, estoure aqui (UI mostrará o erro real)
    if isinstance(norm, dict) and "error" in norm and norm["error"]:
        raise RuntimeError(f"{prov} error: {norm['error']}")

    return norm, used, prov


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
      }
    Ignora chaves extras.
    """
    msgs = payload.get("messages", [])
    return chat(
        model,
        msgs,
        max_tokens=payload.get("max_tokens", 1024),
        temperature=payload.get("temperature", 0.7),
        top_p=payload.get("top_p", 0.95),
    )
