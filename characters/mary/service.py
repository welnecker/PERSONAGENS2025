# characters/mary/service.py
from __future__ import annotations
from typing import List, Dict, Any, Tuple

import re

from core.common.base_service import BaseCharacter
from core.common import SidebarSection  # você pode adicionar campos depois, se quiser
from core.personas import get_persona
from core.service_router import route_chat_strict
from core.repositories import save_interaction, get_history_docs
from core.tokens import toklen


def _montar_historico(usuario_key: str, history_boot: List[Dict[str, str]], limite_tokens: int = 120_000) -> List[Dict[str, str]]:
    """
    Versão mínima local do montador de histórico.
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
    Serviço mínimo e autônomo para Mary.
    Não depende de core.service.
    """

    slug: str = "mary"
    display_name: str = "Mary"

    # Se quiser campos no sidebar de Mary, defina aqui
    def get_sidebar_schema(self) -> List[SidebarSection]:
        return []  # Mary não tem campos específicos (por enquanto)

    def reply(self, user: str, model: str) -> str:
        """
        Gera a resposta da Mary usando apenas persona + histórico.
        """
        # Persona e few-shots da Mary vindos do módulo existente
        persona_text, history_boot = get_persona("Mary")

        # Chave de histórico para Mary permanece apenas pelo usuário (sem sufixo)
        usuario_key = self.session_user_key  # BaseCharacter fornece isso (user_id)
        if not usuario_key:
            usuario_key = "anon"

        # Monta histórico compacto
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

        # Persiste para o histórico desta persona
        try:
            save_interaction(usuario_key, user, resposta, f"{provider}:{used_model}")
        except Exception:
            pass

        return resposta


