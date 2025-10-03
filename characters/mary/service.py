# characters/mary/service.py
from __future__ import annotations

import streamlit as st
from typing import List, Dict

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, last_event
)
from core.tokens import toklen

try:
    # Persona específica da Mary (ideal se existir characters/mary/persona.py)
    from .persona import get_persona  # -> (persona_text: str, history_boot: List[Dict[str,str]])
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

    # ===== API exigida por BaseCharacter =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::mary"

        # --- pinos de memória e local ---
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": persona_text}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + ([{"role": "system", "content": f"LOCAL_ATUAL: {local_atual or '—'}. "
                                               f"Regra dura: NÃO mude o cenário salvo sem pedido explícito do usuário."}])
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

    # ===== utilidades internas =====
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

    def _safe_get_local(self, usuario_key: str) -> str:
        try:
            return get_fact(usuario_key, "local_cena_atual", "") or ""
        except Exception:
            return ""

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """
        Constrói um resumo curto das memórias canônicas relevantes e fixa em 'system'.
        """
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}

        blocos: List[str] = []
        if f.get("parceiro_atual"):
            blocos.append(f"parceiro_atual={f['parceiro_atual']}")
        if "virgem" in f:
            blocos.append(f"virgem={bool(f['virgem'])}")
        if f.get("primeiro_encontro"):
            blocos.append(f"primeiro_encontro={f['primeiro_encontro']}")

        # evento canônico (primeira_vez), se existir
        try:
            ev = last_event(usuario_key, "primeira_vez")
        except Exception:
            ev = None
        if ev:
            ts = ev.get("ts")
            quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            blocos.append(f"primeira_vez@{quando}")

        # PIN só se houver algo útil
        mem_str = "; ".join(blocos)
        pin = (
            "MEMÓRIA_PIN: "
            f"USUÁRIO={user_display}. "
            f"FATOS={{ {mem_str} }}. "
            "Regras duras: use essas memórias para consistência. "
            "Se perguntarem 'qual é meu nome?', responda com parceiro_atual quando fizer sentido. "
            "NÃO contradiga memórias fixas."
        )
        return pin

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
            a = d.get("resposta_mary") or ""  # campo legado consumido pela UI
            t = toklen(u) + toklen(a)
            if total + t > limite_tokens:
                break
            out.append({"role": "user", "content": u})
            out.append({"role": "assistant", "content": a})
            total += t
        return list(reversed(out)) if out else history_boot[:]

    def render_sidebar(self, container) -> None:
        container.markdown("**Mary** — madura, leve, flerte com humor. 1–2 frases por parágrafo.")
