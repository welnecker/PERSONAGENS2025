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

# NSFW (dinâmico)
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False

# Persona específica da Laura
try:
    from .persona import get_persona  # -> (persona_text, history_boot)
except Exception:
    def get_persona():
        txt = (
            "Você é LAURA. 2–4 frases por parágrafo; 4–7 parágrafos. "
            "Tom caloroso e lúdico; mencione 1 traço físico no começo; sem listas; "
            "respeite LOCAL_ATUAL; não faz programa sexual."
        )
        return txt, []


class LauraService(BaseCharacter):
    id: str = "laura"
    display_name: str = "Laura"

    # ===== API exigida por BaseCharacter =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::laura"

        # ---- memória e local ----
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # ---- foco sensorial rotativo (evitar repetição) ----
        pool = [
            "cabelos ruivos", "olhos castanhos", "sardas no colo",
            "seios/areolas", "quadril", "coxas", "bumbum", "respiração",
            "voz/timbre", "mãos/toque"
        ]
        idx = int(st.session_state.get("laura_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["laura_attr_idx"] = idx
        foco = pool[idx]

        length_hint = "COMPRIMENTO: gere 4–7 parágrafos; cada um com 2–4 frases naturais."
        sensory_hint = (
            f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, integre 1–2 pistas envolvendo **{foco}**, "
            "sempre a serviço da ação/emoção (nunca em lista)."
        )
        no_program_hint = (
            "REGRA SOCIAL: Laura é dançarina de boate e NÃO faz programa sexual. "
            "Recuse propostas de sexo por dinheiro com gentileza e redirecione para afeto/conexão."
        )

        # ---- NSFW dinâmico ----
        nsfw_on = False
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False
        try:
            override = (get_fact(usuario_key, "nsfw_override", "") or "").lower()
            if override in ("on", "true", "1", "yes", "sim"):
                nsfw_on = True
            elif override in ("off", "false", "0", "no", "nao", "não"):
                nsfw_on = False
        except Exception:
            pass

        if nsfw_on:
            nsfw_rules = (
                "NSFW ON — Conteúdo permitido somente com ADULTOS, sempre CONSENSUAL. "
                "Você pode descrever partes corporais adultas (seios e mamilos, coxas, nádegas, "
                "vulva, clitóris, pênis, glande, escroto) e sensações (calor, umidade, pressão, textura), "
                "incluindo fluidos (saliva, suor, lubrificação, sêmen). "
                "Sem violência, sem coerção, sem menores, sem incesto, sem bestialidade, sem sexo por dinheiro. "
                "Confirme conforto antes de intensificar e mantenha linguagem sensorial respeitosa."
            )
        else:
            nsfw_rules = (
                "NSFW OFF — Não descreva ato sexual explícito. "
                "Flerte, insinuação e fade-to-black são permitidos."
            )

        system_block = "\n\n".join([persona_text, length_hint, sensory_hint, no_program_hint, nsfw_rules])

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
        Compacta memórias/fatos relevantes e instrui nome preferencial do usuário.
        """
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}

        blocos: List[str] = []
        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = parceiro or user_display

        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")
        if "virgem" in f:
            blocos.append(f"virgem={bool(f['virgem'])}")
        if f.get("primeiro_encontro"):
            blocos.append(f"primeiro_encontro={f['primeiro_encontro']}")

        try:
            ev = last_event(usuario_key, "primeira_vez")
        except Exception:
            ev = None
        if ev:
            ts = ev.get("ts")
            quando = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)
            blocos.append(f"primeira_vez@{quando}")

        mem_str = "; ".join(blocos) if blocos else "—"

        return (
            "MEMÓRIA_PIN: "
            f"NOME_USUARIO={nome_usuario}. FATOS={{ {mem_str} }}. "
            "Se o usuário perguntar seu próprio nome, responda com NOME_USUARIO. "
            "Nunca invente outro nome; confirme com 1 frase em caso de dúvida."
        )

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
            "**Laura** — dançarina (não faz programa), respostas longas (4–7 parágrafos) com foco sensorial rotativo; "
            "NSFW controlado por memórias (toggle no sidebar)."
        )
