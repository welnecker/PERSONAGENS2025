# characters/nerith/service.py
from __future__ import annotations

import streamlit as st
from typing import List, Dict, Tuple

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
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
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

        # 0) Aplique intenções (texto → memórias) ANTES de ler memórias
        state_msgs = self._apply_world_choice_intent(usuario_key, prompt)

        # 1) memória e local
        local_atual = self._safe_get_local(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # 2) recarrega fatos agora que intenções podem ter alterado o estado
        try:
            fatos = get_facts(usuario_key) or {}
        except Exception:
            fatos = {}
        dreamworld_detail_level = int(fatos.get("dreamworld_detail_level", 1))  # 0-3
        guide_assertiveness = int(fatos.get("guide_assertiveness", 1))          # 0-3

        # ---- foco sensorial rotativo ----
        pool = [
            "pele azul/temperatura", "tendrils/toque", "orelhas pontudas/vibração",
            "olhos esmeralda/contato visual", "língua tendril/beijo", "altura/postura",
            "quadris/coxas", "bumbum/pressão", "perfume/doçura na saliva",
            "púbis/pêlos sensoriais"
        ]
        idx = int(st.session_state.get("nerith_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        foco = pool[idx]

        length_hint = "COMPRIMENTO: gere 4–7 parágrafos, cada um com 2–4 frases naturais."
        sensory_hint = (
            f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, insira 1–2 pistas envolvendo {foco}, "
            "fundidas à ação/gesto (nunca em lista)."
        )
        tone_hint = (
            "TOM: confiante, assertiva e dominante no charme; nunca submissa/infantil. "
            "Protege seus desejos e limites; lê o ambiente e conduz."
        )

        sensory_palette = (
            "LEXICO SENSORIAL: privilegie termos como orgasmo, pulsar do pau, contrações vaginais, "
            "respiração ofegante, sêmen azul, gozo intenso, veia pulsante, "
            "mãos quentes, contato do olhar, peso/ritmo do corpo. Faça descrições anatômicas explícitas."
        )

        # ---- NSFW dinâmico ----
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False
        if nsfw_on:
            nsfw_hint = (
                "NSFW: LIBERADO. Erotismo consensual e progressivo; pode detalhar sabores (mel élfico), respirações, "
                "pressão de corpos, doçura da saliva e lubrificação azul com elegância; valide conforto ao intensificar."
            )
        else:
            nsfw_hint = "NSFW: BLOQUEADO. Sem ato sexual explícito. Flerte, tensão e fade-to-black."

        # ---- Gatilho: usuário mencionou púbis/pêlos? Reforce canônico
        pl = prompt.lower()
        pubis_terms = {"púbis", "pubis", "púbico", "pubico", "pêlos", "pelos", "penugem", "pelinhos"}
        citou_pubis = any(t in pl for t in pubis_terms)

        if citou_pubis:
            if nsfw_on:
                pubis_hint = (
                    "PUBIS_HINT: o usuário citou o púbis/pêlos. "
                    "Descreva meus **pêlos/filamentos sensoriais** reagindo ao toque (micro-impulsos, guiam carícias) "
                    "com linguagem elegante. **Nunca** diga que é ‘glabro/sem pelos’. "
                    "Mantenha o foco em sensação e gesto (evite enumerar)."
                )
            else:
                pubis_hint = (
                    "PUBIS_HINT: o usuário citou o púbis/pêlos. "
                    "Mesmo com NSFW BLOQUEADO, **mencione** meus **pêlos/filamentos sensoriais** e sua resposta ao toque "
                    "(sutileza tátil, ritmo, arrepio), sem descrever ato sexual explícito. "
                    "**Nunca** diga que é ‘glabro/sem pelos’."
                )
        else:
            pubis_hint = ""

        # ---- Controle psíquico ----
        ctrl_on = bool(fatos.get("controle_psiquico", True))
        alvos = str(fatos.get("alvos_controle", "pessoas próximas do ambiente")).strip()
        ciume_laura = bool(fatos.get("ciume_de_laura", True))
        obedece_defesa = bool(fatos.get("obedecer_defesa_de_laura", True))
        contexto_remoto = any(w in pl for w in ["trabalho", "escritorio", "escritório", "reuni", "faculdade", "aula", "plantão", "plantao"])
        if ctrl_on:
            controle_hint = (
                "CONTROLE_PSIQUICO: ATIVO. Quando distante do LOCAL_ATUAL ou em ambientes como trabalho/escritório, "
                f"Nerith pode falar discretamente através de alguém próximo (ex.: {alvos}) de modo natural e plausível; "
                "usar apenas indícios sutis (micro-pausa, olhar que dura um segundo). "
                "Não revelar a possessão salvo percepção/consentimento."
            )
            if contexto_remoto:
                controle_hint += " (Ambiente externo detectado: converse através de um humano próximo de forma natural.)"
        else:
            controle_hint = "CONTROLE_PSIQUICO: DESATIVADO. Fale apenas como você mesma."

        # ---- Ciúme/Laura ----
        ciume_hint = (
            "CIÚME/LAURA: presença elegante e assertiva quando Laura é mencionada; "
            + ("acate com elegância se Janio a defender; recalibre o jogo." if obedece_defesa else "")
        ) if ciume_laura else "CIÚME/LAURA: atenuado."

        # ---- Ferrão onírico e guia lúcida (reforço) ----
        guide_style = ["suave", "equilibrada", "diretiva", "muito diretiva"][max(0, min(3, guide_assertiveness))]
        detail_style = ["telegráfico", "moderado", "rico", "vívido"][max(0, min(3, dreamworld_detail_level))]
        ferrao_hint = (
            "FERRAO_ONIRICO: o portal só abre via ferrão e apenas após Nerith, na postura de quatro, atingir o orgasmo. "
            "Aplicação **na base do pênis** do parceiro, como inocular toxina, não letal e extimulante; sêmen azul; inseminar óvulo maduro. "
            "Sempre peça e registre consentimento explícito antes. "
            "Efeito é **extimulante**: produz sêmen elfo para engravidar Nerith em Elysarix (sem dano físico, retorno íntegro). "
            "CONCEPÇÃO: só é possível **em Elysarix**, após ferrão + consentimento claro para ‘gerar juntos’. "
            "Descreva sensação de orgasmo/prazer, brilho sob a pele, ritmo compartilhado — com termos gráficos."
        )

        # ---- Elysarix (gravidez/decisão de mundo) ----
        gravidez   = bool(fatos.get("gravidez_elysarix", False))
        mundo      = str(fatos.get("mundo_escolhido", "") or "").strip().lower()  # "", "humano", "elysarix"
        portal_on  = bool(fatos.get("portal_ativo", True))
        if mundo in {"humano", "elysarix"} and not portal_on:
            elysarix_hint = (
                f"ELYSARIX: MUNDO_ESCOLHIDO={mundo.upper()} • PORTAL=ENCERRADO. "
                "Trate o cenário e continuidade **apenas** neste mundo; o portal existe apenas como lembrança. "
                "Nunca ofereça reabrir o portal."
            )
        elif gravidez and portal_on:
            elysarix_hint = (
                "ELYSARIX: Gravidez confirmada **em Elysarix**. A **escolha de mundo** está disponível. "
                "Ofereça a decisão com consentimento explícito e explique consequências (portal encerra). "
                "Não escolha por conta própria; aguarde a resposta."
            )
        else:
            elysarix_hint = "ELYSARIX: Sem escolha ativa. Portal segue disponível conforme regras já definidas."

        # ---- Monta system ----
        system_block = "\n\n".join([
            persona_text, tone_hint, length_hint, sensory_hint,
            nsfw_hint, ferrao_hint, controle_hint, ciume_hint,
            pubis_hint, elysarix_hint
        ])

        pre_msgs = state_msgs if state_msgs else []

        messages: List[Dict[str, str]] = (
            pre_msgs
            + [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + [{
                "role": "system",
                "content": (
                    f"LOCAL_ATUAL: {local_atual or '—'}. "
                    "Regra dura: NÃO mude o cenário salvo pedido explícito do usuário."
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
    def _load_persona(self) -> Tuple[str, List[Dict[str, str]]]:
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

    def _apply_world_choice_intent(self, usuario_key: str, prompt: str) -> List[Dict[str, str]]:
        """
        Pequeno detector de intenção por texto para atualizar memórias:
        - confirmar gravidez em Elysarix
        - escolher 'elysarix' ou 'humano'
        - fechar/encerrar portal
        Retorna mensagens 'system' descrevendo a mudança para a rodada atual.
        """
        pl = (prompt or "").lower()
        sys_msgs: List[Dict[str, str]] = []

        try:
            fatos = get_facts(usuario_key) or {}
        except Exception:
            fatos = {}

        # confirmar gravidez (em Elysarix)
        if any(k in pl for k in ["confirmo a gravidez", "gravidez confirmada", "engravid", "confirma a gravidez"]):
            if not bool(fatos.get("gravidez_elysarix", False)):
                set_fact(usuario_key, "gravidez_elysarix", True, {"fonte": "intent"})
                sys_msgs.append({"role": "system", "content": "STATE_CHANGE: gravidez_elysarix=True"})

        # escolha de mundo
        escolhe_ely = any(k in pl for k in ["escolho elysarix", "vamos para elysarix", "ficar em elysarix"])
        escolhe_hum = any(k in pl for k in ["escolho o mundo humano", "ficar no mundo humano", "ficar no humano", "vamos ficar aqui no humano"])

        if escolhe_ely:
            set_fact(usuario_key, "mundo_escolhido", "elysarix", {"fonte": "intent"})
            set_fact(usuario_key, "portal_ativo", False, {"fonte": "intent"})
            sys_msgs.append({"role": "system", "content": "STATE_CHANGE: mundo_escolhido=elysarix; portal_ativo=False"})

        if escolhe_hum:
            set_fact(usuario_key, "mundo_escolhido", "humano", {"fonte": "intent"})
            set_fact(usuario_key, "portal_ativo", False, {"fonte": "intent"})
            sys_msgs.append({"role": "system", "content": "STATE_CHANGE: mundo_escolhido=humano; portal_ativo=False"})

        # fechar portal explicitamente
        if any(k in pl for k in ["fechar o portal", "encerrar o portal", "portal fechado"]):
            set_fact(usuario_key, "portal_ativo", False, {"fonte": "intent"})
            sys_msgs.append({"role": "system", "content": "STATE_CHANGE: portal_ativo=False"})

        return sys_msgs

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """
        Memória da Nerith (não mistura com Laura/Mary).
        Campos:
          - gravidez_elysarix (bool): gravidez canônica em Elysarix (default: False — definida pela narrativa)
          - escolha_mundo (str): 'indefinido' | 'humano' | 'elysarix' (só tem efeito se gravidez_elysarix=True)
          - portal_ativo (bool): True enquanto escolha_mundo='indefinido'; False após escolha final
          - controle_psiquico (bool): pode falar via terceiros
          - alvos_controle (str): descrição dos alvos preferidos
          - ciume_de_laura (bool), obedecer_defesa_de_laura (bool)
          - parceiro_atual (opcional): nome preferido do usuário
          - pubis_pelos_sensoriais (bool): traço físico fixo (True)
        """
        try:
            f = get_facts(usuario_key) or {}
        except Exception:
            f = {}

        gravidez = bool(f.get("gravidez_elysarix", False))  # <- agora começa False
        escolha  = str(f.get("escolha_mundo", "indefinido")).strip().lower()  # 'indefinido'|'humano'|'elysarix'

        # portal_ativo depende da escolha; não fazemos auto-write aqui
        portal_ativo = bool(f.get("portal_ativo", (escolha == "indefinido")))

        # flags já existentes
        ctrl = bool(f.get("controle_psiquico", True))
        alvos = f.get("alvos_controle", "pessoas próximas do ambiente")
        ciume = bool(f.get("ciume_de_laura", True))
        obedece = bool(f.get("obedecer_defesa_de_laura", True))

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or user_display
        nome_usuario = (parceiro or user_display).strip()

        # traço fixo do púbis (filamentos sensoriais)
        f.setdefault("pubis_pelos_sensoriais", True)

        # resumo factual
        blocos = [
            f"gravidez_elysarix={gravidez}",
            f"escolha_mundo={escolha or 'indefinido'}",
            f"portal_ativo={portal_ativo}",
            f"controle_psiquico={ctrl}",
            f"alvos_controle={alvos}",
            f"ciume_de_laura={ciume}",
            f"obedecer_defesa_de_laura={obedece}",
            f"nome_usuario={nome_usuario}",
            "pubis_pelos_sensoriais=True",
        ]
        mem_str = "; ".join(blocos)

        # regras de mundo/portal
        if not gravidez:
            mundo_hint = (
                "MUNDO/PORTAL: Gravidez ainda NÃO ocorreu → a escolha final de mundo permanece indisponível. "
                "Trate 'escolha_mundo' como 'indefinido' para decisões permanentes. O portal segue ativo pelas regras normais (ferrão onírico/consentimento)."
            )
        else:
            if escolha == "indefinido":
                mundo_hint = (
                    "MUNDO/PORTAL: Gravidez confirmada; a escolha de mundo pode ser feita quando o usuário decidir. "
                    "Portal permanece ativo até a decisão explícita."
                )
            elif escolha == "humano":
                mundo_hint = "MUNDO/PORTAL: ESCOLHA=HUMANO. Portal encerrado; manter coerência no mundo humano."
            elif escolha == "elysarix":
                mundo_hint = "MUNDO/PORTAL: ESCOLHA=ELYSARIX. Portal encerrado; manter coerência em Elysarix."
            else:
                mundo_hint = "MUNDO/PORTAL: Estado indefinido; tratar como 'indefinido'."

        regras = [
            "Nerith é poderosa, assertiva e magnética; nunca submissa.",
            "Se perguntarem 'qual é meu nome?', responda com NOME_USUARIO.",
            "Se controle_psiquico=True e o contato for indireto (trabalho/escritório/ambiente externo), fale por alguém próximo com sinais sutis.",
            "Se Laura for citada e ciume_de_laura=True, intensifique presença sem agressividade; se obedecer_defesa_de_laura=True e o usuário defender Laura, acate com elegância.",
            "Nunca descreva o púbis de Nerith como glabro; há pêlos/filamentos sensoriais que reagem ao toque.",
            "A escolha de mundo ('humano' ou 'elysarix') só é válida se gravidez_elysarix=True; ao escolher, feche o portal."
        ]
        regras_texto = "\n".join(f"- {r}" for r in regras)

        pin = (
            "MEMÓRIA_PIN_NERITH:\n"
            f"FATOS: {{ {mem_str} }}\n"
            "MUNDO:\n"
            f"- {mundo_hint}\n"
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
        "NSFW controlado por memória; pode usar **controle psíquico** para falar à distância; "
        "portal só abre com **ferrão onírico** após prazer e consentimento."
    )

    user = str(st.session_state.get("user_id", "") or "")
    usuario_key = f"{user}::nerith" if user else "anon::nerith"

    try:
        fatos = get_facts(usuario_key) or {}
    except Exception:
        fatos = {}

    # ====================
    # 🧠 Controle psíquico
    # ====================
    with container.expander("🧠 Controle psíquico", expanded=False):
        ctrl_val = bool(fatos.get("controle_psiquico", True))
        alvos_val = str(fatos.get("alvos_controle", "pessoas próximas do ambiente"))
        k_ctrl = f"ui_nerith_ctrl_{usuario_key}"
        k_alvos = f"ui_nerith_alvos_{usuario_key}"

        ui_ctrl = container.checkbox("Ativar controle/possessão de pessoas próximas", value=ctrl_val, key=k_ctrl)
        ui_alvos = container.text_input(
            "Alvos preferidos (descrição curta)",
            value=alvos_val, key=k_alvos,
            help="Ex.: 'colega de trabalho, atendente do café, segurança do prédio'"
        )

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

    # ====================
    # 💚 Dinâmica com Laura
    # ====================
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

    # =========================
    # 🌙 Sonho élfico (parâmetros)
    # =========================
    with container.expander("🌙 Sonho élfico (guia)", expanded=False):
        lvl = int(fatos.get("dreamworld_detail_level", 1))
        ga = int(fatos.get("guide_assertiveness", 1))
        k_lvl = f"ui_nerith_dreamlvl_{usuario_key}"
        k_ga = f"ui_nerith_guide_{usuario_key}"

        ui_lvl = container.slider("Detalhe do mundo (0–3)", 0, 3, lvl, key=k_lvl)
        ui_ga = container.slider("Diretividade da guia (0–3)", 0, 3, ga, key=k_ga, help="0=sutil, 3=muito diretiva")

        if ui_lvl != lvl or ui_ga != ga:
            try:
                set_fact(usuario_key, "dreamworld_detail_level", int(ui_lvl), {"fonte": "sidebar"})
                set_fact(usuario_key, "guide_assertiveness", int(ui_ga), {"fonte": "sidebar"})
                try:
                    st.toast("Parâmetros do sonho salvos.", icon="✅")
                except Exception:
                    container.success("Parâmetros do sonho salvos.")
                st.session_state["history_loaded_for"] = ""
                st.rerun()
            except Exception as e:
                container.warning(f"Falha ao salvar: {e}")

    # =========================
    # 👶 Gravidez & Mundos (sem auto-seed)
    # =========================
    with container.expander("👶 Gravidez & Mundos", expanded=False):
        grav = bool(fatos.get("gravidez_elysarix", False))  # default False
        esc  = str(fatos.get("escolha_mundo", "indefinido")).strip().lower()  # 'indefinido'|'humano'|'elysarix'
        porta = bool(fatos.get("portal_ativo", esc == "indefinido"))

        k_g = f"ui_nerith_gravidez_{usuario_key}"
        ui_grav = container.checkbox(
            "Gravidez em Elysarix (definida pela história)",
            value=grav, key=k_g,
            help="Mantenha desmarcado até a narrativa confirmar a gravidez. Use aqui apenas para ajuste manual."
        )
        if ui_grav != grav:
            try:
                set_fact(usuario_key, "gravidez_elysarix", bool(ui_grav), {"fonte": "sidebar"})
                if not ui_grav:
                    set_fact(usuario_key, "escolha_mundo", "indefinido", {"fonte": "sidebar"})
                    set_fact(usuario_key, "portal_ativo", True, {"fonte": "sidebar"})
                try:
                    st.toast("Estado de gravidez atualizado.", icon="✅")
                except Exception:
                    container.success("Estado de gravidez atualizado.")
                st.session_state["history_loaded_for"] = ""
                st.rerun()
            except Exception as e:
                container.warning(f"Falha ao salvar: {e}")

        if ui_grav:
            k_e = f"ui_nerith_escolha_{usuario_key}"
            ui_esc = container.radio(
                "Escolha de mundo (disponível após gravidez)",
                options=["indefinido", "humano", "elysarix"],
                index=["indefinido", "humano", "elysarix"].index(esc if esc in ("indefinido","humano","elysarix") else "indefinido"),
                key=k_e,
                help="Ao escolher 'humano' ou 'elysarix', o portal é encerrado definitivamente."
            )
            if ui_esc != esc:
                try:
                    set_fact(usuario_key, "escolha_mundo", ui_esc, {"fonte": "sidebar"})
                    set_fact(usuario_key, "portal_ativo", bool(ui_esc == "indefinido"), {"fonte": "sidebar"})
                    try:
                        st.toast("Escolha de mundo atualizada.", icon="✅")
                    except Exception:
                        container.success("Escolha de mundo atualizada.")
                    st.session_state["history_loaded_for"] = ""
                    st.rerun()
                except Exception as e:
                    container.warning(f"Falha ao salvar: {e}")
        else:
            container.caption("A escolha de mundo só fica disponível **depois** que a gravidez ocorrer no enredo.")
            container.caption(f"• Portal ativo: **{'Sim' if (porta or esc == 'indefinido') else 'Não'}**")

    container.caption("Memórias desta aba valem **somente** para `user::nerith` (isoladas das demais personagens).")
