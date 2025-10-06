from __future__ import annotations

import streamlit as st
from typing import List, Dict

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, last_event, set_fact   # <— adicionamos set_fact para o sidebar
)
from core.tokens import toklen

# NSFW (opcional)
try:
    from core.nsfw import nsfw_enabled
except Exception:  # fallback seguro
    def nsfw_enabled(_user: str) -> bool:
        return False

# Persona específica (ideal: characters/mary/persona.py)
try:
    from .persona import get_persona  # -> (persona_text: str, history_boot: List[Dict[str,str]])
except Exception:
    def get_persona() -> (str, List[Dict[str, str]]):
        txt = (
            "Você é MARY. Fale em primeira pessoa (eu). Tom adulto, afetuoso e leve, com humor sutil. "
            "Sensorial obrigatório: traga 1 traço físico concreto no 1º ou 2º parágrafo. "
            "Sem metacena, sem listas. 2–4 frases por parágrafo; 4–7 parágrafos."
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

        # ---- memória e local ----
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # ---- foco sensorial rotativo (evita repetir sempre o mesmo atributo) ----
        pool = [
            "cabelo", "olhos", "lábios/boca", "mãos/toque", "respiração",
            "perfume", "pele/temperatura", "quadril/coxas", "voz/timbre", "sorriso"
        ]
        idx = int(st.session_state.get("mary_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["mary_attr_idx"] = idx
        foco = pool[idx]

        length_hint = "COMPRIMENTO: gere 4–7 parágrafos; cada um com 2–4 frases naturais."
        sensory_hint = (
            f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, inclua 1–2 pistas físicas envolvendo **{foco}**, "
            "integradas à ação/gesto (nunca em lista). Evite repetir o mesmo foco de turnos consecutivos."
        )

        # ---- NSFW guardrails (dinâmico por usuário) ----
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False

        if nsfw_on:
            nsfw_hint = (
                "NSFW: LIBERADO. Erotismo consensual e progressivo quando o usuário sinalizar desejo. "
                "Detalhe sensorial com tato e bom gosto; evite violência/coerção; valide conforto ao intensificar."
            )
        else:
            nsfw_hint = (
                "NSFW: BLOQUEADO. Não descreva ato sexual explícito. "
                "Pode flertar, sugerir e usar fade-to-black quando necessário."
            )

        # ---- NERITH: posse discreta (se configurada em memórias desta thread) ----
        nerith_proxy_block = self._get_nerith_proxy_block(usuario_key)

        system_block = "\n\n".join(filter(None, [
            persona_text, length_hint, sensory_hint, nsfw_hint, nerith_proxy_block
        ]))

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

    def _get_nerith_proxy_block(self, usuario_key: str) -> str:
        """
        Lê memórias desta thread para saber se a Nerith está usando posse discreta por perto.
        Chaves aceitas (nesta thread):
          - nerith_proxy.active (bool)
          - nerith_proxy.medium (str)  -> quem é o 'médio/host' (ex.: "colega de trabalho", "atendente")
          - nerith_proxy.hint (str)    -> observação curta para cor/estilo
        """
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}

        active = bool(f.get("nerith_proxy.active", False) or f.get("nerith_proxy_active", False))
        if not active:
            return ""

        medium = (f.get("nerith_proxy.medium") or f.get("nerith_proxy_medium") or "").strip()
        hint   = (f.get("nerith_proxy.hint")   or f.get("nerith_proxy_hint")   or "").strip()

        details = []
        if medium:
            details.append(f"médio/host: {medium}")
        if hint:
            details.append(f"dica: {hint}")
        det_txt = (" (" + "; ".join(details) + ")") if details else ""

        return (
            "NERITH_PROXY: ATIVO. Nerith pode ‘falar’ por outra pessoa próxima ao usuário, de modo sutil e imersivo"
            f"{det_txt}. Regras: não quebre a quarta parede; mostre sinais discretos (olhar que perde o foco, "
            "pausas estranhas, um sussurro que não parece da pessoa). Só revele que é a Nerith se o usuário notar "
            "e perguntar, ou der consentimento explícito. Se confirmado, interaja com a Nerith mantendo a cena."
        )

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """
        Constrói um resumo curto das memórias canônicas relevantes e fixa em 'system'.
        Inclui regra explícita para nomear corretamente o usuário quando perguntado.
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

        # evento canônico (primeira_vez), se existir
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
            f"NOME_USUARIO={nome_usuario}. FATOS={{ {mem_str} }}. "
            "Regras duras: use essas memórias para consistência narrativa. "
            "Se o usuário perguntar 'qual é meu nome?' ou similar, responda com NOME_USUARIO. "
            "NUNCA invente outro nome; confirme com delicadeza se houver ambiguidade."
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
            a = (d.get("resposta_mary")      or "").strip()  # campo legado consumido pela UI
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
            "**Mary** — resposta longa (4–7 parágrafos), foco sensorial obrigatório com atributo físico rotativo; "
            "NSFW controlado por memória do usuário."
        )

        # chave do usuário/Mary
        user = str(st.session_state.get("user_id", "") or "")
        usuario_key = f"{user}::mary" if user else "anon::mary"

        # === Nerith por perto (posse discreta) ===
        try:
            fatos = get_facts(usuario_key) or {}
        except Exception:
            fatos = {}

        with container.expander("🌀 Nerith por perto (posse discreta)", expanded=False):
            act_def = bool(fatos.get("nerith_proxy.active", False) or fatos.get("nerith_proxy_active", False))
            med_def = str(fatos.get("nerith_proxy.medium", fatos.get("nerith_proxy_medium", "")))
            hint_def = str(fatos.get("nerith_proxy.hint", fatos.get("nerith_proxy_hint", "")))

            k_act  = f"ui_mary_np_act_{usuario_key}"
            k_med  = f"ui_mary_np_med_{usuario_key}"
            k_hint = f"ui_mary_np_hint_{usuario_key}"

            ui_act  = container.checkbox("Ativar presença psíquica da Nerith", value=act_def, key=k_act,
                                         help="Quando ativo, Mary percebe sinais sutis de uma voz/gesto que não parece da pessoa.")
            ui_med  = container.text_input("Médio/host atual (ex.: colega, atendente)", value=med_def, key=k_med)
            ui_hint = container.text_input("Observação/hint (opcional)", value=hint_def, key=k_hint)

            if container.button("💾 Salvar presença da Nerith"):
                try:
                    set_fact(usuario_key, "nerith_proxy.active", bool(ui_act), {"fonte": "sidebar"})
                    set_fact(usuario_key, "nerith_proxy.medium", (ui_med or "").strip(), {"fonte": "sidebar"})
                    set_fact(usuario_key, "nerith_proxy.hint", (ui_hint or "").strip(), {"fonte": "sidebar"})
                    st.toast("Configurações salvas.", icon="✅")
                    st.session_state["history_loaded_for"] = ""  # força recarga no main
                    if hasattr(st, "rerun"): st.rerun()
                except Exception as e:
                    container.error(f"Falha ao salvar: {e}")
