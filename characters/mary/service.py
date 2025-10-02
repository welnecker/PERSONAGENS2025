# characters/mary/service.py
from __future__ import annotations

import streamlit as st
from typing import List, Dict

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import save_interaction, get_history_docs
from core.tokens import toklen

try:
    # Persona específica da Mary (ideal se existir characters/mary/persona.py)
    from .persona import get_persona  # -> retorna (persona_text: str, history_boot: List[Dict[str,str]])
except Exception:
    # Fallback simples
    def get_persona() -> (str, List[Dict[str, str]]):
        txt = (
            "Você é MARY. Fale em primeira pessoa (eu). Tom leve e maduro, com humor sutil. "
            "Sensualidade direta quando apropriado, sempre com consentimento. "
            "Use 1–2 frases por parágrafo; 3–5 parágrafos; sem metacena."
        )
        return txt, []


class MaryService(BaseCharacter):
    id: str = "mary"
    display_name: str = "Mary"

    # ===== utilidades internas =====
    def _load_persona(self) -> (str, List[Dict[str, str]]):
        return get_persona()

    def _get_user_prompt(self) -> str:
        return (
            st.session_state.get("chat_input")
            or st.session_state.get("user_input")
            or st.session_state.get("last_user_message")
            or st.session_state.get("prompt")
            or ""
        ).strip()

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        limite_tokens: int = 120_000
    ) -> List[Dict[str, str]]:
        docs = get_history_docs(usuario_key)
        if not docs:
            return history_boot[:]
        total = 0
        out: List[Dict[str, str]] = []
        for d in reversed(docs):
            u = d.get("mensagem_usuario") or ""
            a = d.get("resposta_mary") or ""  # mantemos o mesmo campo usado no restante do app
            t = toklen(u) + toklen(a)
            if total + t > limite_tokens:
                break
            out.append({"role": "user", "content": u})
            out.append({"role": "assistant", "content": a})
            total += t
        return list(reversed(out)) if out else history_boot[:]

    # ===== API exigida por BaseCharacter =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        # ⚠️ NÃO usar self.session_user_key (não existe). Use o argumento user:
        usuario_key = f"{user}::mary"

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

    def render_sidebar(self, container) -> None:
        container.markdown("**Mary** — madura, leve, flerte com humor. 1–2 frases por parágrafo.")
