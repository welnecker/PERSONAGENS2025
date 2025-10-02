# characters/laura/service.py
from __future__ import annotations

import streamlit as st
from typing import List, Dict

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import save_interaction, get_history_docs
from core.tokens import toklen

try:
    # persona específica da Laura (ideal)
    from .persona import get_persona  # -> retorna (persona_text: str, history_boot: List[Dict[str,str]])
except Exception:  # fallback simples se o arquivo não existir
    def get_persona() -> (str, List[Dict[str, str]]):
        txt = (
            "Você é LAURA. Fale em primeira pessoa. Tom caloroso, direto e grato. "
            "1–2 frases por parágrafo; 3–5 parágrafos. Sem metacena. "
            "Foque em gesto, respiração, proximidade e convites (não ordens)."
        )
        return txt, []


class LauraService(BaseCharacter):
    id: str = "laura"
    display_name: str = "Laura"

    def _load_persona(self) -> (str, List[Dict[str, str]]):
        return get_persona()

    def _get_user_prompt(self) -> str:
        # tenta ler o input do chat do Streamlit (ajusta aos nomes mais comuns)
        return (
            st.session_state.get("chat_input")
            or st.session_state.get("user_input")
            or st.session_state.get("last_user_message")
            or st.session_state.get("prompt")
            or ""
        ).strip()

    def _montar_historico(self, usuario_key: str, history_boot: List[Dict[str, str]], limite_tokens: int = 120_000) -> List[Dict[str, str]]:
        docs = get_history_docs(usuario_key)
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

    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::laura"

        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": persona_text}]
            + self._montar_historico(usuario_key, history_boot)
            + [{"role": "user", "content": prompt}]
        )

        data, used_model, provider = route_chat_strict(model, {
            "model": model,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.6,
            "top_p": 0.9,
        })
        texto = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        return texto

    # Sidebar opcional (pode ser customizada depois)
    def render_sidebar(self, container) -> None:
        container.markdown("**Laura** — foco em gesto, respiração e carinho.")
