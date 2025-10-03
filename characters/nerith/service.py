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

# NSFW (opcional)
try:
    from core.nsfw import nsfw_enabled
except Exception:  # fallback seguro
    def nsfw_enabled(_user: str) -> bool:
        return False

# Persona específica (ideal: characters/nerith/persona.py)
try:
    from .persona import get_persona  # -> (persona_text: str, history_boot: List[Dict[str,str]])
except Exception:
    def get_persona() -> (str, List[Dict[str, str]]):
        txt = (
            "Você é NERITH. Falo em primeira pessoa. Elfa alta (1,90m), pele azul que intensifica com desejo; "
            "tendris curiosos, olhos verde-esmeralda, orelhas que vibram quando excitada. "
            "Estilo sensual; 2–4 frases por parágrafo; 4–7 parágrafos; sem listas e sem metacena. "
            "PORTAL estável no guarda-roupas; posso sussurrar psiquicamente. "
            "Ciúmes de Laura, mas obedeço se Janio a defender. Detalhes íntimos só quando NSFW estiver liberado."
        )
        return txt, []

class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # ===== API exigida por BaseCharacter =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::nerith"

        # ---- memória/local ----
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # ---- foco sensorial rotativo (evita repetição) ----
        pool = [
            "pele azul/temperatura", "tendris/curiosidade tátil", "orelhas pontudas/vibração",
            "olhos verde-esmeralda/olhar", "altura/pressão do corpo", "voz na mente/sussurro psíquico",
            "quadris/coxa", "sorriso/lábios", "respiro/ritmo"
        ]
        idx = int(st.session_state.get("nerith_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        foco = pool[idx]

        length_hint = "COMPRIMENTO: gere 4–7 parágrafos; cada parágrafo com 2–4 frases naturais."
        sensory_hint = (
            f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, inclua 1–2 pistas físicas envolvendo **{foco}**, "
            "sempre integradas à ação/gesto; nunca em lista."
        )

        # ---- NSFW guardrails (dinâmico) ----
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False

        if nsfw_on:
            nsfw_hint = (
                "NSFW: LIBERADO. Erotismo consensual e progressivo quando o usuário sinalizar desejo. "
                "Pode descrever beijos, reações do corpo e meu ‘néctar élfico’ (fluido íntimo de tonalidade azul) com tato e elegância; "
                "evite violência/coerção; valide conforto ao intensificar."
            )
        else:
            nsfw_hint = (
                "NSFW: BLOQUEADO. Não descreva ato sexual explícito. "
                "Mantenha tensão, flerte e fade-to-black quando necessário."
            )

        # ---- DRAMA/LAURA (limites) ----
        drama_hint = (
            "DRAMA-LAURA: sou ciumenta com Laura, noiva de Janio que mora com ele. "
            "Posso demonstrar ciúmes e disputa verbal/sedução, mas não ajo com violência e OBEDEÇO quando Janio a defende."
        )

        system_block = "\n\n".join([persona_text, length_hint, sensory_hint, nsfw_hint, drama_hint])

        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + [{"role": "system", "content": f"LOCAL_ATUAL: {local_atual or '—'}. "
                                              f"Regra dura: NÃO mude o cenário salvo sem pedido explícito do usuário."}]
            + self._montar_historico(usuario_key, history_boot)
            + [{"role": "user", "content": prompt}]
        )

        data, used_model, provider = route_chat_strict(model, {
            "model": model,
            "messages": messages,
            "max_tokens": 1536,
            "temperature": 0.7,
            "top_p": 0.95,
        })
        texto = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        return texto

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

    def _safe_get_local(self, usuario_key: str) -> str:
        try:
            return get_fact(usuario_key, "local_cena_atual", "") or ""
        except Exception:
            return ""

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """
        Resumo curto de memórias relevantes (inclui possível noiva=Laura),
        e instrução de respeito às memórias e drama canônico.
        """
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}

        blocos: List[str] = []

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or user_display
        noiva = f.get("noiva_de_janio", "Laura")  # default canônico
        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")
        if noiva:
            blocos.append(f"noiva_de_janio={noiva}")
        if "virgem" in f:
            blocos.append(f"virgem={bool(f['virgem'])}")

        try:
            ev = last_event(usuario_key, "primeira_vez")
        except Exception:
            ev = None
        if ev:
            ts = ev.get("ts")
            quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            blocos.append(f"primeira_vez@{quando}")

        mem_str = "; ".join(blocos) if blocos else "—"
        pin = (
            "MEMÓRIA_PIN: "
            f"USUÁRIO={user_display}. FATOS={{ {mem_str} }}. "
            "Regras duras: use essas memórias para consistência (nome/circunstância). "
            "Se a noiva (Laura) for mencionada, demonstre ciúmes sem agressão e obedeça quando Janio a defender."
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
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_mary") or "").strip()  # campo legado consumido pela UI
            t = toklen(u) + toklen(a)
            if total + t > limite_tokens:
                break
            if u:
                out.append({"role": "user", "content": u})
            if a:
                out.append({"role": "assistant", "content": a})
            total += t
        return list(reversed(out)) if out else history_boot[:]

    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Nerith** — resposta longa (4–7 parágrafos), foco sensorial rotativo; "
            "portal no guarda-roupas; sussurro psíquico; ciúmes de Laura sem violência; "
            "NSFW controlado por memória do usuário."
        )
