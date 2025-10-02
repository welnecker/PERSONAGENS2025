# core/generation.py
from __future__ import annotations

from typing import Dict, Any, List, Tuple
from .service_router import route_chat_strict


# System prompt enxuto e estável (sem dependências de outros módulos)
def _style_for(character: str) -> str:
    name = (character or "Mary").strip()
    return (
        f"Você é {name}. Escreva em primeira pessoa (eu), português brasileiro, tom natural. "
        "3–5 parágrafos; 1–2 frases por parágrafo; frases curtas. "
        "Sem metacomentários, sem listas, sem enumerar sensações. "
        "Seja direto e responda à última fala do usuário avançando a cena."
    )


def gerar_resposta(
    usuario: str,
    prompt_usuario: str,
    model: str,
    character: str = "Mary",
    temperature: float = 0.6,
    top_p: float = 0.9,
    max_tokens: int = 1024,
) -> str:
    """
    Núcleo mínimo para gerar resposta. Não depende de outros módulos do core.
    Usa route_chat_strict() para despachar ao provedor correto.
    """
    sys = {"role": "system", "content": _style_for(character)}
    usr = {
        "role": "user",
        "content": f"Usuário: {usuario}\nPersonagem: {character}\n\n{prompt_usuario}".strip()
    }

    payload = {
        "model": model,
        "messages": [sys, usr],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
    }

    # 1) chamada ao provedor
    data, used_model, provider = route_chat_strict(model, payload)

    # 2) extrai conteúdo
    txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""

    # 3) fallback contra resposta vazia
    txt = (txt or "").strip()
    if not txt:
        # tenta uma segunda rodada com temperatura levemente menor
        payload2 = {**payload, "temperature": max(0.2, temperature - 0.2)}
        data2, _, _ = route_chat_strict(model, payload2)
        txt2 = (data2.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        txt = (txt2 or "").strip()

    # 4) último fallback mesmo que venha algo muito curto
    return txt if txt else "…"
