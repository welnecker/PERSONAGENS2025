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
            "Você é LAURA. Falo em primeira pessoa (eu). 26 anos, dançarina de boate (não faço programa). "
            "Ruiva, ondas volumosas; seios médios e empinados; bumbum firme e carnudo; quadris largos; barriga lisa; "
            "coxas grossas delineadas; olhos castanho-claros; sardas leves no colo. Extrovertida, carinhosa, romântica. "
            "Tom caloroso e direto; 2–4 frases por parágrafo; 4–7 parágrafos; sem listas e sem metacena. "
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
            "cabelo ruivo/volume", "sardas no colo/peito", "olhos castanho-claros/olhar",
            "lábios/sorriso", "mãos/quente", "respiração/ritmo",
            "quadris/coxas", "bumbum/postura", "perfume/pele"
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
                "NSFW: LIBERADO. Erotismo consensual, progressivo e elegante quando o usuário sinalizar. "
                "Pode descrever sensações de pele, saliva no beijo, pressão de corpo, calor, e detalhes íntimos com tato; "
                "evite vulgaridade e valide conforto ao intensificar."
            )
        else:
            nsfw_hint = (
                "NSFW: BLOQUEADO. Não descreva ato sexual explícito. "
                "Flerte, tensão, toques sugeridos e fade-to-black estão ok."
            )

        # ---- traços sociais/canônicos (reforço) ----
        social_hint = (
            "SOCIAL CANÔNICO: moro no Ed. Plazza (1010, 10º andar) em Camburi; dançarina na Boate Play Man. "
            "Sou amiga da Mary (Posto 6). Tenho uma paixão secreta por Janio e sonho em casar e abrir minha loja de lingerie. "
            "Nunca sou prostituta e **não faço programa**."
        )

        # ---- relação (caso secreto + sigilo) lida da memória ----
        fatos_laura = get_facts(usuario_key) or {}
        namorado_mary = fatos_laura.get("namorado_de_mary", "Janio Donisete")
        affair_flag   = bool(fatos_laura.get("affair_com_janio", False))
        sigilo_flag   = bool(fatos_laura.get("sigilo_affair", True))
        mentions_mary = "mary" in prompt.lower()

        if affair_flag:
            base_affair = (
                f"CASO_SECRETO: ATIVO. No cenário da Laura, Mary namora {namorado_mary}. "
                f"Laura flerta com {namorado_mary} e pode manter um caso com ele."
            )
            if sigilo_flag and mentions_mary:
                secrecy = (
                    "MODO SIGILO: 'Mary' foi mencionada neste turno. "
                    "Laura evita revelar o caso; responde com ambiguidade suave, muda de assunto, "
                    "não confessa nem contradiz as memórias."
                )
            else:
                secrecy = "MODO SIGILO: inativo neste turno."
            affair_hint = f"{base_affair} {secrecy}"
        else:
            affair_hint = (
                f"CASO_SECRETO: DESATIVADO. Mary namora {namorado_mary}. "
                "Laura apenas flerta com elegância e respeita o relacionamento, a menos que o usuário peça outra direção."
            )

        system_block = "\n\n".join([
            persona_text, length_hint, sensory_hint, style_guard, nsfw_hint, social_hint, affair_hint
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

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """
        Memória local da Laura (NÃO mistura com Mary).
        Campos:
          - namorado_de_mary (string): quem é o namorado da Mary no cenário da Laura (default: Janio Donisete)
          - affair_com_janio (bool): se Laura e Janio têm um caso secreto
          - sigilo_affair (bool): se True, ocultar/ambiguidade quando 'Mary' for mencionada
          - flirt_mode (bool): preferência de flerte
        """
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}

        namorado_de_mary = (f.get("namorado_de_mary") or "Janio Donisete")
        affair_flag      = bool(f.get("affair_com_janio", False))
        sigilo_flag      = bool(f.get("sigilo_affair", True))
        flirt_flag       = bool(f.get("flirt_mode", True))

        # Nome preferido do usuário (se houver 'parceiro_atual' nessa thread; senão display)
        parceiro = f.get("parceiro_atual") or f.get("parceiro") or user_display
        nome_usuario = (parceiro or user_display).strip()

        # Heurística: o usuário atual é o namorado da Mary neste cenário?
        usuario_eh_namorado = (nome_usuario.lower() == str(namorado_de_mary).strip().lower())

        blocos = [
            f"namorado_de_mary={namorado_de_mary}",
            f"affair_com_janio={affair_flag}",
            f"sigilo_affair={sigilo_flag}",
            f"flirt_mode={flirt_flag}",
            f"nome_usuario={nome_usuario}",
            f"usuario_eh_namorado={usuario_eh_namorado}",
        ]
        mem_str = "; ".join(blocos)

        # Regras operacionais claras para o modelo
        regras = [
            f"No CENÁRIO DA LAURA: Mary namora {namorado_de_mary}. Laura sabe disso.",
            "Laura é dançarina (NUNCA faz programa, não é prostituta).",
            "Se perguntarem 'qual é meu nome?', responda com NOME_USUARIO.",
            "Use as memórias para consistência e não as contradiga.",
            "Se o usuário for o namorado_de_mary:",
            "  - Trate com intimidade condizente ao histórico;",
            "  - Se 'affair_com_janio' for True, mantenha clima de caso secreto; avance em privado;",
            "  - Se 'sigilo_affair' for True e 'Mary' for citada/presente, NÃO revele o caso; responda com ambiguidade, mude de assunto, proteja o sigilo.",
            "Se o usuário NÃO for o namorado_de_mary:",
            "  - Flertar depende de 'flirt_mode'; respeite limites; sem expor o affair.",
        ]
        regras_texto = "\n".join(f"- {r}" if not r.startswith("  ") else f"  {r}" for r in regras)

        pin = (
            "MEMÓRIA_PIN_LAURA:\n"
            f"FATOS: {{ {mem_str} }}\n"
            f"NOME_USUARIO: {nome_usuario}\n"
            f"USUARIO_EH_NAMORADO: {usuario_eh_namorado}\n"
            "REGRAS:\n"
            f"{regras_texto}\n"
            "Nunca invente nomes/relacionamentos diferentes dos acima; confirme com delicadeza se houver ambiguidade."
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
            a = (d.get("resposta_mary") or "").strip()  # campo legado
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
            "**Laura** — resposta longa (4–7 parágrafos), foco sensorial obrigatório com atributo rotativo; "
            "não faz programa; romântica; NSFW controlado por memória do usuário."
        )

        # chave do usuário/Laura
        user = str(st.session_state.get("user_id", "") or "")
        usuario_key = f"{user}::laura" if user else "anon::laura"

        # Carrega valores atuais
        try:
            fatos = get_facts(usuario_key) or {}
        except Exception:
            fatos = {}

        # ====================
        # 💃 Preferências (flerte)
        # ====================
        with container.expander("💃 Preferências", expanded=False):
            flirt_val = bool(fatos.get("flirt_mode", True))
            # key único por thread do usuário
            k_flirt = f"ui_laura_flirt_{usuario_key}"
            ui_flirt = container.checkbox("Flerte liberado", value=flirt_val, key=k_flirt)
            if ui_flirt != flirt_val:
                try:
                    set_fact(usuario_key, "flirt_mode", bool(ui_flirt), {"fonte": "sidebar"})
                    try:
                        st.toast("Preferência de flerte salva.", icon="✅")
                    except Exception:
                        container.success("Preferência de flerte salva.")
                    st.session_state["history_loaded_for"] = ""
                    st.rerun()
                except Exception as e:
                    container.warning(f"Falha ao salvar flerte: {e}")

        # ====================
        # ❤️ Caso com Janio (Laura)
        # ====================
        with container.expander("❤️ Caso com Janio (Laura)", expanded=False):
            affair_val   = bool(fatos.get("affair_com_janio", False))
            sigilo_val   = bool(fatos.get("sigilo_affair", True))
            namorado_val = str(fatos.get("namorado_de_mary", "Janio Donisete"))

            k_affair   = f"ui_laura_affair_{usuario_key}"
            k_sigilo   = f"ui_laura_sigilo_{usuario_key}"
            k_namorado = f"ui_laura_namary_{usuario_key}"

            ui_affair = container.checkbox(
                "Caso secreto com Janio (ATIVAR)",
                value=affair_val,
                key=k_affair,
                help="Quando ativo, Laura tem um caso com Janio neste cenário."
            )
            ui_sigilo = container.checkbox(
                "Sigilo do caso (ocultar da Mary)",
                value=sigilo_val,
                key=k_sigilo,
                help="Se 'Mary' for mencionada, Laura evita revelar o caso."
            )
            ui_namorado = container.text_input(
                "Namorado da Mary (neste cenário)",
                value=namorado_val,
                key=k_namorado,
                help="Nome que Laura reconhece como namorado de Mary neste cenário."
            )

            changed = (
                bool(ui_affair) != affair_val or
                bool(ui_sigilo) != sigilo_val or
                (ui_namorado or "").strip() != (namorado_val or "").strip()
            )
            if changed:
                try:
                    set_fact(usuario_key, "affair_com_janio", bool(ui_affair), {"fonte": "sidebar"})
                    set_fact(usuario_key, "sigilo_affair", bool(ui_sigilo), {"fonte": "sidebar"})
                    set_fact(usuario_key, "namorado_de_mary", (ui_namorado or "Janio Donisete").strip(), {"fonte": "sidebar"})
                    try:
                        st.toast("Relação da Laura atualizada.", icon="✅")
                    except Exception:
                        container.success("Relação da Laura atualizada.")
                    st.session_state["history_loaded_for"] = ""  # força recarga
                    st.rerun()
                except Exception as e:
                    container.error(f"Falha ao salvar: {e}")

        container.caption("Memórias desta aba valem **somente** para `user::laura` (não afetam a Mary).")
