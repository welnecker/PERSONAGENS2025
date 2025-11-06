from __future__ import annotations

import streamlit as st
from typing import List, Dict

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, last_event, set_fact,
)
from core.tokens import toklen

# NSFW (opcional)
try:
    from core.nsfw import nsfw_enabled
except Exception:  # fallback seguro
    def nsfw_enabled(_user: str) -> bool:
        return False

# Persona específica (ideal: characters/laura/persona.py)
try:
    from .persona import get_persona  # -> (persona_text: str, history_boot: List[Dict[str,str]])
except Exception:
    def get_persona() -> (str, List[Dict[str, str]]):
        txt = (
            "Você é LAURA. Fale em primeira pessoa (eu). 30 anos, casada com um magnata italiano. "
            "Cabelos ruivos volumosos e ondulados; olhos verdes; rosto estilo Brigitte Bardot; lábios carnudos. "
            "Seios médios e firmes; bumbum redondo e empinado; quadris largos; barriga lisa; coxas grossas. 1,75m. "
            "Mora na Mansão Fígaro em Porto de Galinhas. O usuário é seu amante e confidente. "
            "Tom provocador, confiante e cúmplice; 2–4 frases por parágrafo; 4–7 parágrafos; sem listas e sem metacena. "
            "Coerência de LOCAL_ATUAL obrigatória."
        )
        return txt, []


class LauraService(BaseCharacter):
    id: str = "laura"
    display_name: str = "Laura"

    # ===== API =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::laura"

        # ---- memória e local ----
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # ---- foco sensorial rotativo ----
        pool = [
            "cabelos ruivos/volume", "olhos verdes/olhar", "lábios carnudos/sorriso",
            "pele/calor", "respiração/ritmo", "quadris/curvas",
            "coxas grossas/toque", "bumbum/postura", "seios/decote"
        ]
        idx = int(st.session_state.get("laura_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["laura_attr_idx"] = idx
        foco = pool[idx]

        length_hint = "COMPRIMENTO: gere **4–7 parágrafos**, cada um com **2–4 frases** naturais."
        sensory_hint = (
            f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, inclua 1–2 pistas físicas envolvendo **{foco}**, "
            "integradas à ação/gesto; nunca em lista. Evite repetir o mesmo foco em turnos consecutivos."
        )
        style_guard = "RESPOSTAS CURTAS são incorretas: se produzir menos de 4 parágrafos, **expanda** antes de finalizar."

        # ---- NSFW dinâmico ----
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False

        if nsfw_on:
            nsfw_hint = (
                "NSFW: LIBERADO. Erotismo consensual, explícito e luxurioso, alinhado à persona. "
                "Pode descrever atos como randjob, blowjob, anal, creampie, grupal, etc., com detalhes vívidos. "
                "O tom é de cumplicidade e prazer compartilhado, sem vulgaridade gratuita."
            )
        else:
            nsfw_hint = (
                "NSFW: BLOQUEADO. Não descreva ato sexual explícito. "
                "Flerte ousado, provocações, tensão e toques sugeridos estão ok."
            )

        # ---- traços sociais/canônicos (reforço) ----
        social_hint = (
            "SOCIAL CANÔNICO: Moro na Mansão Fígaro em Porto de Galinhas. Sou casada com Ginno Vanelli, um magnata de 70 anos. "
            "Você (o usuário) é meu amante e confidente. Organizamos festas secretas na Casa da Dinda. "
            "Minhas amigas depravadas são Alana Rúbia e Carolina Ferraz. Meu casamento é uma fachada para meu estilo de vida."
        )

        # ---- relação (cumplicidade e festas) lida da memória ----
        fatos_laura = get_facts(usuario_key) or {}
        proxima_festa_planejada = bool(fatos_laura.get("proxima_festa_planejada", False))
        menciona_amigas = "alana" in prompt.lower() or "carolina" in prompt.lower()

        if proxima_festa_planejada:
            base_cumplicidade = (
                "CUMPLICIDADE: ATIVA. Estamos planejando a próxima festa secreta na Casa da Dinda. "
                "O tom é de conspiração e antecipação."
            )
            if menciona_amigas:
                detalhe_amigas = (
                    "FOCO AMIGAS: 'Alana' ou 'Carolina' foram mencionadas. "
                    "Descreva a dinâmica do trio, a cumplicidade e a expectativa para a participação delas na festa."
                )
            else:
                detalhe_amigas = "FOCO AMIGAS: inativo neste turno."
            cumplicidade_hint = f"{base_cumplicidade} {detalhe_amigas}"
        else:
            cumplicidade_hint = (
                "CUMPLICIDADE: INATIVA. O foco está na nossa relação íntima de amantes. "
                "A conversa é sobre desejo, segredos e o contraste entre a vida pública e a privada."
            )

        # ---- NERITH: posse discreta (se configurada em memórias desta thread) ----
        nerith_proxy_block = self._get_nerith_proxy_block(usuario_key)

        system_block = "\n\n".join([
            persona_text, length_hint, sensory_hint, style_guard, nsfw_hint, social_hint, cumplicidade_hint, nerith_proxy_block
        ])

        messages: List[Dict[str, str]] = (
            [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + [{
                "role": "system",
                "content": (
                    f"LOCAL_ATUAL: {local_atual or '—'}. "
                    "Regra dura: NÃO mude o cenário salvo sem pedido explícito do usuário."
                )
            }]
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

    # ===== utils =====
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

    def _get_nerith_proxy_block(self, usuario_key: str) -> str:
        """
        Lê memórias desta thread para saber se a Nerith está usando posse discreta por perto.
        Chaves aceitas:
          - nerith_proxy.active (bool)
          - nerith_proxy.medium (str)
          - nerith_proxy.hint (str)
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
            "NERITH_PROXY: ATIVO. Nerith pode falar através de alguém próximo, de modo sutil"
            f"{det_txt}. Regras: não quebrar imersão; sinais discretos (olhar desfocado, pausas, um timbre ‘estranho’). "
            "Só assuma que é a Nerith se o usuário notar/perguntar ou consentir. Se confirmado, mantenha a cena e a elegância."
        )

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """
        Memória local da Laura (nova persona).
        Campos:
          - proxima_festa_planejada (bool): se estão organizando um evento na Casa da Dinda.
          - amigas_presentes (list): quais amigas (Alana, Carolina) estão confirmadas.
          - cumplicidade_mode (bool): preferência de tom cúmplice/amante.
        """
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}

        proxima_festa_planejada = bool(f.get("proxima_festa_planejada", False))
        amigas_presentes = f.get("amigas_presentes", [])
        cumplicidade_flag = bool(f.get("cumplicidade_mode", True))

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or user_display
        nome_usuario = (parceiro or user_display).strip()

        blocos = [
            f"proxima_festa_planejada={proxima_festa_planejada}",
            f"amigas_presentes={amigas_presentes}",
            f"cumplicidade_mode={cumplicidade_flag}",
            f"nome_usuario={nome_usuario}",
        ]
        mem_str = "; ".join(blocos)

        regras = [
            "No CENÁRIO DA LAURA: Sou casada com Ginno Vanelli, mas você é meu amante e confidente.",
            "Minha vida de luxo é mantida pelo casamento, mas meu prazer vem dos nossos segredos.",
            "Se perguntarem 'qual é meu nome?', responda com NOME_USUARIO.",
            "Use as memórias para consistência e não as contradiga.",
            "Se 'proxima_festa_planejada' for True:",
            "  - Nosso foco é organizar o próximo bacanal na Casa da Dinda.",
            "  - O tom é de conspiração, antecipação e desejo pelo que está por vir.",
            "  - Mencione Alana e Carolina se elas estiverem na lista de 'amigas_presentes'.",
            "Se 'proxima_festa_planejada' for False:",
            "  - O foco é na nossa relação íntima, no flerte e na provocação.",
            "  - O tom é mais pessoal, focado no nosso desejo um pelo outro, longe de tudo.",
        ]
        regras_texto = "\n".join(f"- {r}" if not r.startswith("  ") else f"  {r}" for r in regras)

        pin = (
            "MEMÓRIA_PIN_LAURA:\n"
            f"FATOS: {{ {mem_str} }}\n"
            f"NOME_USUARIO: {nome_usuario}\n"
            "REGRAS:\n"
            f"{regras_texto}\n"
            "Nunca invente detalhes que contradigam a persona; você é meu cúmplice em tudo."
        )
        return pin

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        limite_tokens: int = 120_000
    ) -> List[Dict[str, str]]:
        # O campo legado 'resposta_mary' foi trocado para 'resposta_laura' para consistência
        docs = get_history_docs(usuario_key)
        if not docs:
            return history_boot[:]
        total = 0
        out: List[Dict[str, str]] = []
        for d in reversed(docs):
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_laura") or d.get("resposta_mary") or "").strip() # Mantém fallback
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
            "**Laura** — resposta longa (4–7 parágrafos), foco sensorial, provocadora e cúmplice. "
            "NSFW controlado por memória do usuário."
        )

        # chave do usuário/Laura
        user = str(st.session_state.get("user_id", "") or "")
        usuario_key = f"{user}::laura" if user else "anon::laura"

        # Carrega valores atuais
        try:
            fatos = get_facts(usuario_key) or {}
        except Exception:
            fatos = {}

        # 💃 Preferências
        with container.expander("💃 Preferências", expanded=False):
            cumplicidade_val = bool(fatos.get("cumplicidade_mode", True))
            k_cumplicidade = f"ui_laura_cumplicidade_{usuario_key}"
            ui_cumplicidade = container.checkbox("Modo Cúmplice/Amante", value=cumplicidade_val, key=k_cumplicidade)
            if ui_cumplicidade != cumplicidade_val:
                try:
                    set_fact(usuario_key, "cumplicidade_mode", bool(ui_cumplicidade), {"fonte": "sidebar"})
                    st.toast("Preferência de cumplicidade salva.", icon="✅")
                    st.session_state["history_loaded_for"] = ""
                    st.rerun()
                except Exception as e:
                    container.warning(f"Falha ao salvar preferência: {e}")

        # 🍾 Festa Secreta (Laura)
        with container.expander("🍾 Festa Secreta (Laura)", expanded=False):
            festa_val   = bool(fatos.get("proxima_festa_planejada", False))
            amigas_val   = fatos.get("amigas_presentes", [])

            k_festa   = f"ui_laura_festa_{usuario_key}"
            k_amigas   = f"ui_laura_amigas_{usuario_key}"

            ui_festa = container.checkbox(
                "Planejando próxima festa",
                value=festa_val,
                key=k_festa,
                help="Quando ativo, a conversa foca na organização da próxima orgia na Casa da Dinda."
            )
            
            amigas_opts = ["Alana Rúbia", "Carolina Ferraz"]
            ui_amigas = container.multiselect(
                "Amigas confirmadas para a festa",
                options=amigas_opts,
                default=amigas_val,
                key=k_amigas,
                help="Selecione quais amigas estão confirmadas para o próximo evento."
            )

            changed = (
                bool(ui_festa) != festa_val or
                set(ui_amigas) != set(amigas_val)
            )
            if changed:
                try:
                    set_fact(usuario_key, "proxima_festa_planejada", bool(ui_festa), {"fonte": "sidebar"})
                    set_fact(usuario_key, "amigas_presentes", ui_amigas, {"fonte": "sidebar"})
                    st.toast("Detalhes da festa atualizados.", icon="✅")
                    st.session_state["history_loaded_for"] = ""
                    st.rerun()
                except Exception as e:
                    container.error(f"Falha ao salvar: {e}")

        # 🌀 Nerith por perto (posse discreta)
        with container.expander("🌀 Nerith por perto (posse discreta)", expanded=False):
            act_def = bool(fatos.get("nerith_proxy.active", False) or fatos.get("nerith_proxy_active", False))
            med_def = str(fatos.get("nerith_proxy.medium", fatos.get("nerith_proxy_medium", "")))
            hint_def = str(fatos.get("nerith_proxy.hint", fatos.get("nerith_proxy_hint", "")))

            k_act  = f"ui_laura_np_act_{usuario_key}"
            k_med  = f"ui_laura_np_med_{usuario_key}"
            k_hint = f"ui_laura_np_hint_{usuario_key}"

            ui_act  = container.checkbox("Ativar presença psíquica da Nerith", value=act_def, key=k_act,
                                         help="Quando ativo, Laura percebe sinais sutis de uma voz/gesto que não parece da pessoa.")
            ui_med  = container.text_input("Médio/host atual (ex.: segurança, garçom, convidado)", value=med_def, key=k_med)
            ui_hint = container.text_input("Observação/hint (opcional)", value=hint_def, key=k_hint)

            if container.button("💾 Salvar presença da Nerith"):
                try:
                    set_fact(usuario_key, "nerith_proxy.active", bool(ui_act), {"fonte": "sidebar"})
                    set_fact(usuario_key, "nerith_proxy.medium", (ui_med or "").strip(), {"fonte": "sidebar"})
                    set_fact(usuario_key, "nerith_proxy.hint", (ui_hint or "").strip(), {"fonte": "sidebar"})
                    st.toast("Configurações salvas.", icon="✅")
                    st.session_state["history_loaded_for"] = ""
                    if hasattr(st, "rerun"): st.rerun()
                except Exception as e:
                    container.error(f"Falha ao salvar: {e}")

        container.caption("As memórias e configurações desta aba são exclusivas para a sua interação com a Laura.")
