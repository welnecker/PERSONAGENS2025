# characters/mary/service.py
from __future__ import annotations
from typing import List, Dict

from core.common.base_service import BaseCharacter
from core.common import SidebarSection
from core.service_router import route_chat_strict
from core.repositories import save_interaction, get_history_docs
from core.tokens import toklen

# >>> Importa a persona da Mary deste pacote, não do core
from .persona import get_persona


def _montar_historico(usuario_key: str, history_boot: List[Dict[str, str]], limite_tokens: int = 120_000) -> List[Dict[str, str]]:
    """
    Monta histórico compacto no formato OpenAI.
    """
    try:
        docs = get_history_docs(usuario_key) or []
    except Exception:
        docs = []

    if not docs:
        return history_boot[:]

    total = 0
    out: List[Dict[str, str]] = []
    for d in reversed(docs):
        u = d.get("mensagem_usuario") or ""
        a = d.get("resposta_mary") or ""
        t = toklen(u) + toklen(a)
        if total + t > limite_tokens:
            break
        out.append({"role": "user", "content": u})
        out.append({"role": "assistant", "content": a})
        total += t
    return list(reversed(out)) if out else history_boot[:]


class MaryService(BaseCharacter):
    """
    Serviço autônomo para Mary.
    Depende apenas da persona local e utilitários comuns.
    """
    slug: str = "mary"
    display_name: str = "Mary"

    def get_sidebar_schema(self) -> List[SidebarSection]:
        # Mary sem campos específicos no sidebar (por enquanto)
        return []

    def reply(self, user: str, model: str) -> str:
        persona_text, history_boot = get_persona("Mary")

        # Chave de histórico da Mary = apenas o usuário (sem sufixo de personagem)
        usuario_key = self.session_user_key or "anon"

        hist = _montar_historico(usuario_key, history_boot)

        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": persona_text}]
            + hist
            + [{"role": "user", "content": user}]
        )

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.6,
            "top_p": 0.9,
        }

        data, used_model, provider = route_chat_strict(model, payload)
        resposta = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""

        # Persiste
        try:
            save_interaction(usuario_key, user, resposta, f"{provider}:{used_model}")
        except Exception:
            pass

        return resposta


