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
    # persona específica da Nerith/Narith
    from .persona import get_persona  # -> (persona_text: str, history_boot: List[Dict[str,str]])
except Exception:
    def get_persona() -> (str, List[Dict[str, str]]):
        txt = (
            "Você é NERITH (Narith). Fale em primeira pessoa. Sensual direta, sem listas. "
            "Pele azul, orelhas pontudas que vibram ao estímulo; tendrils buscam calor; "
            "descrição sensorial com consentimento; 1–2 frases por parágrafo; 3–5 parágrafos."
        )
        return txt, []


class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

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

    def _memory_pin(self, usuario_key: str) -> Dict[str, str]:
        """Constrói 'system' com memórias canônicas (coerência para Nerith)."""
        try:
            facts = get_facts(usuario_key) or {}
        except Exception:
            facts = {}

        parceiro = (facts.get("parceiro_atual") or "").strip()
        virgem = facts.get("virgem", None)
        # campos que você quiser exclusivos da Nerith podem ser salvos/lembrados aqui
        # ex.: "ancoras_portal", "tendrils_ok", etc. — se existirem:
        tendrils_ok = facts.get("tendrils_ok", None)

        primeira = None
        try:
            ev = last_event(usuario_key, "primeira_vez")
        except Exception:
            ev = None
        if ev:
            ts = ev.get("ts")
            quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts or "")
            onde = ev.get("local") or "—"
            primeira = f"{quando} @ {onde}"

        linhas = ["MEMÓRIA_PIN: respeite e não contradiga as memórias abaixo."]
        if parceiro:
            linhas.append(f"- parceiro_atual: {parceiro}")
        if virgem is not None:
            linhas.append(f"- virgem: {bool(virgem)}")
        if tendrils_ok is not None:
            linhas.append(f"- tendrils_ok: {bool(tendrils_ok)}")
        if primeira:
            linhas.append(f"- primeira_vez: {primeira}")

        return {"role": "system", "content": "\n".join(linhas)}

    # ===== API exigida por BaseCharacter =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::nerith"

        # LOCAL_ATUAL
        local_atual = get_fact(usuario_key, "local_cena_atual", "") or "—"
        local_pin = {
            "role": "system",
            "content": f"LOCAL_PIN: {local_atual}. Regra dura: NÃO mude o cenário salvo pedido explícito do usuário."
        }

        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": persona_text}, local_pin, self._memory_pin(usuario_key)]
            + self._montar_historico(usuario_key, history_boot)
            + [{"role": "user", "content": f"LOCAL_ATUAL: {local_atual}\n\n{prompt}"}]
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
        container.markdown("**Nerith** — sensualidade direta, sem listas; consentimento sempre.")
