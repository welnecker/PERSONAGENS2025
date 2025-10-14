# core/ultra.py
from __future__ import annotations
from typing import List, Dict, Tuple
import re

try:
    from core.service_router import route_chat_strict
except Exception:
    route_chat_strict = None

_CHECKLIST = (
    "- Manter LOCAL_ATUAL e tempo sem alterar.\n"
    "- Respeitar MEMÓRIA e ENTIDADES; não inventar nomes/end.\n"
    "- Estilo: 1ª pessoa (eu), 4–7 parágrafos, 2–4 frases cada, sem listas/metacena.\n"
    "- Sensorial integrado à ação (sem inventário de sensações).\n"
    "- Fluxo natural, sem perguntar demais; avance sutilmente.\n"
)

def critic_review(model: str, system_guard: str, last_user: str, draft: str) -> str:
    """
    Passa 2: crítico — retorna recomendações objetivas (curtas).
    """
    if not callable(route_chat_strict):
        return ""
    prompt = (
        "Você é um Crítico de consistência narrativa. "
        "Analise o DRAFT à luz do SYSTEM (regras) e do último USER. "
        "Devolva ajustes curtos e específicos (bullet points)."
        "\n\n[CHECKLIST]\n" + _CHECKLIST +
        "\n\n[SYSTEM]\n" + system_guard +
        "\n\n[USER]\n" + last_user +
        "\n\n[DRAFT]\n" + draft
    )
    data, used, prov = route_chat_strict(model, {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 220,
        "temperature": 0.0,
        "top_p": 0.9
    })
    return (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""

def polish(model: str, system_guard: str, last_user: str, draft: str, critic_notes: str) -> str:
    """
    Passa 3: polidor — aplica notas do crítico no texto final.
    """
    if not callable(route_chat_strict):
        return draft
    prompt = (
        "Aplique as notas do Crítico ao DRAFT com edição mínima e elegante. "
        "Preserve voz, local, tempo e estilo. Não resuma.\n\n"
        "[CHECKLIST]\n" + _CHECKLIST +
        "\n\n[SYSTEM]\n" + system_guard +
        "\n\n[USER]\n" + last_user +
        "\n\n[NOTAS_DO_CRITICO]\n" + (critic_notes or "(sem notas)") +
        "\n\n[DRAFT]\n" + draft
    )
    data, used, prov = route_chat_strict(model, {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max(512, min(2048, len(draft)//3 + 400)),
        "temperature": 0.3,
        "top_p": 0.9
    })
    return (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or draft
