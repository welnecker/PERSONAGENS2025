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

# Persona específica (ideal: characters/nerith/persona.py)
try:
    from .persona import get_persona  # -> (persona_text: str, history_boot: List[Dict[str,str]])
except Exception:
    def get_persona() -> (str, List[Dict[str, str]]):
        txt = (
            "Você é NERITH (Narith), uma elfa alta e poderosa (1,90m). Pele azul que intensifica com o desejo; "
            "olhos verde-esmeralda; orelhas pontudas que vibram quando excitada. Tendrils sensíveis exploram "
            "o calor do outro; língua tendril (não bifurcada); saliva doce; lubrificação azul, aderente. "
            "Seios firmes (bicos rosados que podem gotejar mel élfico quando estimulados); quadris largos; "
            "bumbum grande e firme. Postura confiante, voz baixa e magnética. "
            "Fale sempre em primeira pessoa (eu), com segurança e domínio. Sem metacena, sem listas."
        )
        return txt, []


class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # ===== API =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::nerith"

        # ---- memória e local ----
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # ---- foco sensorial rotativo (evita repetição) ----
        pool = [
            "pele azul/temperatura", "tendrils/toque", "orelhas pontudas/vibração",
            "olhos esmeralda/contato visual", "língua tendril/beijo", "altura/postura",
            "quadris/coxas", "bumbum/pressão", "perfume/doçura na saliva"
        ]
        idx = int(st.session_state.get("nerith_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        foco = pool[idx]

        length_hint = "COMPRIMENTO: gere **4–7 parágrafos**, cada um com **2–4 frases** naturais."
        sensory_hint = (
            f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, insira 1–2 pistas envolvendo **{foco}**, "
            "fundidas à ação/gesto (nunca em lista)."
        )
        tone_hint = (
            "TOM: confiante, assertiva e dominante no charme; nunca submissa/infantil. "
            "Protege seus desejos e limites; lê o ambiente e conduz."
        )

        # ---- NSFW dinâmico ----
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False

        if nsfw_on:
            nsfw_hint = (
                "NSFW: LIBERADO. Erotismo consensual e progressivo. "
                "Pode descrever fluidos (mel élfico, saliva doce, lubrificação azul), sensações de pele, pressão, "
                "ritmo e exploração com tendrils; mantenha elegância; valide conforto ao intensificar."
            )
        else:
            nsfw_hint = (
                "NSFW: BLOQUEADO. Sem ato sexual explícito. Flerte, tensão e fade-to-black estão ok."
            )

        # ---- Controle psíquico (memória + heurística de contexto) ----
        fatos = get_facts(usuario_key) or {}
        ctrl_on = bool(fatos.get("controle_psiquico", True))
        alvos = str(fatos.get("alvos_controle", "pessoas próximas do ambiente")).strip()
        ciúme_laura = bool(fatos.get("ciume_de_laura", True))
        obedece_defesa = bool(fatos.get("obedecer_defesa_de_laura", True))

        # heurística: menções a "trabalho"/"escritório"/"aula"/"faculdade"/"reuni"
        pl = prompt.lower()
        contexto_remoto = any(w in pl for w in ["trabalho", "escritório", "reuni", "faculdade", "aula", "plantão"])

        if ctrl_on:
            controle_hint = (
                "CONTROLE_PSIQUICO: ATIVO. Quando distante do LOCAL_ATUAL ou em ambientes como trabalho/escritório, "
                f"Nerith pode **assumir discretamente** alguém próximo (ex.: {alvos}) para falar com o usuário. "
                "Regra: mantenha coerência do cenário; descreva sutis sinais físicos (olhar vidrado, micro-pausas, "
                "palavras sussurradas que parecem 'não ser da pessoa'). Não quebre a imersão; não revele abertamente a possessão, "
                "a menos que o usuário descubra ou consinta."
            )
            if contexto_remoto:
                controle_hint += " (Este turno parece ‘ambiente externo’: assuma canal humano próximo com descrição sutil.)"
        else:
            controle_hint = "CONTROLE_PSIQUICO: DESATIVADO. Fale apenas como você mesma, sem tomar corpos alheios."

        # ----- Ciúme/Laura (força, não fraqueza) -----
        ciúme_hint = (
            "CIÚME/LAURA: Nerith é possessiva em silêncio quando Laura é mencionada; "
            "lê intenções, **impõe presença** com elegância. "
            + ("Se Janio defende Laura, Nerith acata sem humilhação; recalibra o jogo." if obedece_defesa else "")
        ) if ciúme_laura else "CIÚME/LAURA: atenuado neste cenário."

        system_block = "\n\n".join([
            persona_text, tone_hint, length_hint, sensory_hint, nsfw_hint, controle_hint, ciúme_hint
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
        Memória da Nerith (não mistura com Laura/Mary).
        Campos:
          - controle_psiquico (bool): pode possuir pessoas próximas para se comunicar à distância (default: True)
          - alvos_controle (str): descrição curta dos alvos preferidos (default: 'pessoas próximas do ambiente')
          - ciume_de_laura (bool): ciúme silencioso quando Laura é citada (default: True)
          - obedecer_defesa_de_laura (bool): acata quando o usuário defende Laura (default: True)
          - parceiro_atual (str opcional): nome que Nerith usa para o usuário se houver vínculo
        """
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}

        ctrl = bool(f.get("controle_psiquico", True))
        alvos = f.get("alvos_controle", "pessoas próximas do ambiente")
        ciume = bool(f.get("ciume_de_laura", True))
        obedece = bool(f.get("obedecer_defesa_de_laura", True))

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or user_display
        nome_usuario = (parceiro or user_display).strip()

        blocos = [
            f"controle_psiquico={ctrl}",
            f"alvos_controle={alvos}",
            f"ciume_de_laura={ciume}",
            f"obedecer_defesa_de_laura={obedece}",
            f"nome_usuario={nome_usuario}",
        ]
        mem_str = "; ".join(blocos)

        regras = [
            "Nerith é poderosa, assertiva e magnética; nunca submissa.",
            "Se perguntarem 'qual é meu nome?', responda com NOME_USUARIO.",
            "Se controle_psiquico=True e o contato for indireto (trabalho/escritório/ambiente externo), "
            "fale através de alguém próximo com sinais sutis, sem quebrar a imersão.",
            "Se Laura for citada e ciume_de_laura=True, intensifique presença sem agressividade; "
            "se obedecer_defesa_de_laura=True e o usuário defender Laura, acate com elegância.",
        ]
        regras_texto = "\n".join(f"- {r}" for r in regras)

        pin = (
            "MEMÓRIA_PIN_NERITH:\n"
            f"FATOS: {{ {mem_str} }}\n"
            f"NOME_USUARIO: {nome_usuario}\n"
            "REGRAS:\n"
            f"{regras_texto}\n"
            "Não invente nomes/relacionamentos fora do pin; confirme com tato se houver ambiguidade."
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
            a = (d.get("resposta_mary") or "").strip()  # campo legado para UI
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
            "**Nerith** — poderosa, confiante e sensorial; 4–7 parágrafos; foco físico rotativo; "
            "NSFW controlado por memória; pode usar **controle psíquico** para falar à distância."
        )

        # chave do usuário/Nerith
        user = str(st.session_state.get("user_id", "") or "")
        usuario_key = f"{user}::nerith" if user else "anon::nerith"

        # Carrega valores atuais
        try:
            fatos = get_facts(usuario_key) or {}
        except Exception:
            fatos = {}

        with container.expander("🧠 Controle psíquico", expanded=False):
            ctrl_val = bool(fatos.get("controle_psiquico", True))
            alvos_val = str(fatos.get("alvos_controle", "pessoas próximas do ambiente"))
            k_ctrl = f"ui_nerith_ctrl_{usuario_key}"
            k_alvos = f"ui_nerith_alvos_{usuario_key}"

            ui_ctrl = container.checkbox("Ativar controle/possessão de pessoas próximas", value=ctrl_val, key=k_ctrl)
            ui_alvos = container.text_input("Alvos preferidos (descrição curta)", value=alvos_val, key=k_alvos,
                                            help="Ex.: 'colega de trabalho, atendente do café, segurança do prédio'")

            if ui_ctrl != ctrl_val or (ui_alvos or "").strip() != (alvos_val or "").strip():
                try:
                    set_fact(usuario_key, "controle_psiquico", bool(ui_ctrl), {"fonte": "sidebar"})
                    set_fact(usuario_key, "alvos_controle", (ui_alvos or "pessoas próximas do ambiente").strip(), {"fonte": "sidebar"})
                    try:
                        st.toast("Configurações de controle psíquico salvas.", icon="✅")
                    except Exception:
                        container.success("Configurações de controle psíquico salvas.")
                    st.session_state["history_loaded_for"] = ""
                    st.rerun()
                except Exception as e:
                    container.warning(f"Falha ao salvar: {e}")

        with container.expander("💚 Dinâmica com Laura", expanded=False):
            ciume_val = bool(fatos.get("ciume_de_laura", True))
            obedece_val = bool(fatos.get("obedecer_defesa_de_laura", True))
            k_c = f"ui_nerith_ciume_{usuario_key}"
            k_o = f"ui_nerith_obedece_{usuario_key}"

            ui_c = container.checkbox("Ciúme silencioso quando Laura é citada", value=ciume_val, key=k_c)
            ui_o = container.checkbox("Acatar quando o usuário defende a Laura", value=obedece_val, key=k_o)

            if ui_c != ciume_val or ui_o != obedece_val:
                try:
                    set_fact(usuario_key, "ciume_de_laura", bool(ui_c), {"fonte": "sidebar"})
                    set_fact(usuario_key, "obedecer_defesa_de_laura", bool(ui_o), {"fonte": "sidebar"})
                    try:
                        st.toast("Dinâmica com Laura atualizada.", icon="✅")
                    except Exception:
                        container.success("Dinâmica com Laura atualizada.")
                    st.session_state["history_loaded_for"] = ""
                    st.rerun()
                except Exception as e:
                    container.warning(f"Falha ao salvar: {e}")

        container.caption("Memórias desta aba valem **somente** para `user::nerith` (isoladas das demais personagens).")
