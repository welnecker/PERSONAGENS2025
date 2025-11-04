# characters/nerith/service.py
# VERSÃO ESTÁVEL • Boot garantido • Histórico e memória consistentes
from __future__ import annotations

import time, json
from datetime import datetime, timedelta
from typing import List, Dict, Tuple
import streamlit as st

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs, delete_user_history,
    get_facts, get_fact, last_event, set_fact,
)
from core.tokens import toklen

# ===== NSFW (opcional) =====
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False

# ===== Persona específica =====
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é **Nerith**, elfa onírica de pele azul e orelhas pontudas, "
            "dominante no charme e confiante. Fale em primeira pessoa (eu). "
            "Integre 1–2 pistas sensoriais no 1º/2º parágrafo; 4–7 parágrafos; 2–4 frases por parágrafo; sem listas."
        )
        # history_boot: mensagens system/ou assistant curtas usadas como fala inicial
        return txt, [{"role": "assistant", "content": "A porta do guarda-roupas range… a luz azul me revela. Eu te encontrei."}]

# ===== Cache leve =====
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))  # segundos
_cache_facts: Dict[str, Dict] = {}
_cache_hist: Dict[str, List[Dict]] = {}
_cache_ts: Dict[str, float] = {}

def _purge_cache():
    now = time.time()
    for k in list(_cache_facts.keys()):
        if now - _cache_ts.get(f"facts_{k}", 0) >= CACHE_TTL:
            _cache_facts.pop(k, None)
            _cache_ts.pop(f"facts_{k}", None)
    for k in list(_cache_hist.keys()):
        if now - _cache_ts.get(f"hist_{k}", 0) >= CACHE_TTL:
            _cache_hist.pop(k, None)
            _cache_ts.pop(f"hist_{k}", None)

def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None)
    _cache_ts.pop(f"facts_{user_key}", None)
    _cache_hist.pop(user_key, None)
    _cache_ts.pop(f"hist_{user_key}", None)

def cached_get_facts(user_key: str) -> Dict:
    _purge_cache()
    now = time.time()
    if user_key in _cache_facts and (now - _cache_ts.get(f"facts_{user_key}", 0) < CACHE_TTL):
        return _cache_facts[user_key]
    try:
        f = get_facts(user_key) or {}
    except Exception:
        f = {}
    _cache_facts[user_key] = f
    _cache_ts[f"facts_{user_key}"] = now
    return f

def cached_get_history(user_key: str, limit: int = 50) -> List[Dict]:
    _purge_cache()
    now = time.time()
    if user_key in _cache_hist and (now - _cache_ts.get(f"hist_{user_key}", 0) < CACHE_TTL):
        return _cache_hist[user_key]
    try:
        docs = get_history_docs(user_key, limit=limit) or []
    except Exception:
        docs = []
    _cache_hist[user_key] = docs
    _cache_ts[f"hist_{user_key}"] = now
    return docs

# ===== Ferramentas (Tool-Calling) =====
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_memory_pin",
            "description": "Retorna fatos canônicos curtos da Nerith para este usuário.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_fact",
            "description": "Salva ou atualiza um fato canônico (chave/valor) para Nerith.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_fact",
            "description": "Busca um fato específico da memória (chave).",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"]
            }
        }
    },
]

# ===== Serviço =====
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # ---------- API principal ----------
    def reply(self, user: str, model: str) -> str:
        """
        Garante BOOT quando:
        - prompt vazio (app recém aberto ou resetado)
        - st.session_state["nerith_force_boot"] = True
        - não há histórico
        """
        prompt = self._get_user_prompt()
        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user or 'anon'}::nerith"

        # Historico mínimo para saber se já falou
        have_hist = bool(cached_get_history(usuario_key, limit=1))

        # ====== Força BOOT pelo botão de resgate ======
        if st.session_state.get("nerith_force_boot", False):
            st.session_state["nerith_force_boot"] = False
            try:
                delete_user_history(usuario_key)
            except Exception:
                pass
            clear_user_cache(usuario_key)
            boot_text = self._boot_text(history_boot)
            save_interaction(usuario_key, "", boot_text, "system:boot")
            # Local: só define 'quarto' se portal não estiver aberto
            fatos = cached_get_facts(usuario_key)
            portal_aberto = str(fatos.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")
            if portal_aberto:
                set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "boot-preserva"})
            else:
                set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "boot"})
            clear_user_cache(usuario_key)
            return boot_text

        # ====== Boot automático quando não há prompt OU não há histórico ======
        if not prompt or not have_hist:
            boot_text = self._boot_text(history_boot)
            # preservar Elysarix se já marcado
            fatos = cached_get_facts(usuario_key)
            portal_aberto = str(fatos.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")
            local_reg = (str(fatos.get("local_cena_atual", "") or "")).lower()
            if portal_aberto or local_reg == "elysarix":
                set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "boot-preserva"})
            else:
                set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "boot"})
            save_interaction(usuario_key, "", boot_text, "system:boot")
            clear_user_cache(usuario_key)
            return boot_text

        # ====== Estado e memórias ======
        # Intenções que mexem com mundo/portal
        state_msgs = self._apply_world_choice_intent(usuario_key, prompt)

        # Comando explícito do usuário para setar local
        explicit_local = self._check_user_location_command(prompt)
        if explicit_local:
            set_fact(usuario_key, "local_cena_atual", explicit_local, {"fonte": "user_command"})
            clear_user_cache(usuario_key)

        fatos = cached_get_facts(usuario_key)
        portal_aberto = str(fatos.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")
        local_atual = self._safe_get_local(usuario_key)

        # Se o portal está aberto, força Elysarix
        if portal_aberto and (not local_atual or local_atual.lower() != "elysarix"):
            local_atual = "Elysarix"
            set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "reidrata_depois_toggle"})
            clear_user_cache(usuario_key)

        # Parâmetros
        dreamworld_detail_level = self._safe_int(fatos.get("dreamworld_detail_level", 1), 1)
        guide_assertiveness = self._safe_int(fatos.get("guide_assertiveness", 1), 1)

        # Foco sensorial rotativo
        foco = self._get_sensory_focus()

        # NSFW
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False

        length_hint = "COMPRIMENTO: gere 4–7 parágrafos, cada um com 2–4 frases naturais."
        sensory_hint = f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{foco}**, integradas à ação."
        tone_hint = "TOM: confiante, assertiva e dominante no charme; nunca submissa/infantil."
        nsfw_hint = (
            "NSFW: LIBERADO. Erotismo consensual e progressivo; descreva com elegância (sem listas/roteiro)."
            if nsfw_on else
            "NSFW: BLOQUEADO. Flerte, tensão, e fade-to-black."
        )

        # Hints específicos
        pubis_hint = self._get_pubis_hint(prompt, nsfw_on)
        controle_hint = self._get_controle_hint(fatos, prompt)
        ciume_hint = self._get_ciume_hint(fatos)
        ferrao_hint = self._get_ferrao_hint()
        elysarix_hint = self._get_elysarix_hint(fatos)
        if portal_aberto:
            elysarix_hint += "\n⚠️ Já estamos em Elysarix — **não** repita a travessia. Continue do ponto atual."

        memoria_pin = self._build_memory_pin(usuario_key, user)

        # Pedido curto “continue…”
        pre_msgs_continue = self._pre_msgs_continue(prompt, portal_aberto)

        # System principal
        system_block = "\n\n".join([
            persona_text, tone_hint, length_hint, sensory_hint, nsfw_hint,
            ferrao_hint, controle_hint, ciume_hint, pubis_hint, elysarix_hint,
            "FERRAMENTAS: use get_memory_pin para recuperar estado persistente; get_fact para saber portal; "
            "set_fact para marcar portal_aberto=True quando a cena mudar para Elysarix. Nunca repita travessia se portal_aberto=True."
        ])

        # Histórico
        hist_msgs = self._montar_historico(usuario_key, history_boot, model, verbatim_ultimos=10)

        messages: List[Dict[str, str]] = (
            (state_msgs if state_msgs else [])
            + pre_msgs_continue
            + [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + [{"role": "system", "content": f"LOCAL_ATUAL: {local_atual or '—'}. Regra dura: NÃO mude o cenário salvo pedido explícito."}]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        # Tool-Calling opcional + robustez
        tools = TOOLS if st.session_state.get("tool_calling_on", False) else None
        max_iterations = 3 if tools else 1

        iteration = 0
        texto_final = ""
        provider = "router"
        used_model = model
        while iteration < max_iterations:
            iteration += 1
            data, used_model, provider = self._robust_chat_call(
                model, messages, max_tokens=1536, temperature=0.7, top_p=0.95, tools=tools
            )

            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls", [])

            # Sem tool calls → fim
            if not tool_calls or not tools:
                texto_final = texto or texto_final
                break

            # Com tool calls
            messages.append({
                "role": "assistant",
                "content": texto or None,
                "tool_calls": tool_calls
            })
            for tc in tool_calls:
                func_name = tc.get("function", {}).get("name", "")
                func_args_str = tc.get("function", {}).get("arguments", "{}")
                tool_id = tc.get("id", f"call_{iteration}")
                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}
                except Exception:
                    func_args = {}
                result = self._exec_tool_call(func_name, func_args, usuario_key, user)
                messages.append({"role": "tool", "tool_call_id": tool_id, "name": func_name, "content": result})

        # Safe Mode: nunca devolve vazio
        if not texto_final or not texto_final.strip():
            texto_final = (
                "Eu mantive o fio da cena e o cenário atual. Diz numa linha o próximo passo (ex.: "
                "‘Elysarix, lago de cristal’ ou ‘ficamos no quarto, luz baixa’), que eu te levo."
            )

        # Persistência + detectores de local/portal
        try:
            save_interaction(usuario_key, prompt, texto_final, f"{provider}:{used_model}")
        except Exception:
            pass

        try:
            self._detect_and_update_local(usuario_key, texto_final, portal_aberto=portal_aberto)
            if self._detect_elysarix_scene(texto_final):
                set_fact(usuario_key, "portal_aberto", "True", {"fonte": "auto_detect_portal"})
            clear_user_cache(usuario_key)
        except Exception:
            pass

        # Placeholder leve
        try:
            st.session_state["suggestion_placeholder"] = self._suggest_placeholder(texto_final, local_atual)
            st.session_state["last_assistant_message"] = texto_final
        except Exception:
            st.session_state["suggestion_placeholder"] = ""

        # Sinalizar fallback
        if provider == "synthetic-fallback":
            st.info("⚠️ Provedor instável. Resposta em fallback — pode continuar normalmente.")

        return texto_final

    # ---------- Robustez de chamada ----------
    def _robust_chat_call(
        self,
        model: str,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int = 1536,
        temperature: float = 0.7,
        top_p: float = 0.95,
        tools: List[Dict] | None = None,
    ) -> Tuple[Dict, str, str]:
        attempts = 3
        last_err = ""
        for i in range(attempts):
            try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                }
                if tools:
                    payload["tools"] = tools
                if st.session_state.get("json_mode_on", False):
                    payload["response_format"] = {"type": "json_object"}

                adapter_id = (st.session_state.get("adapter_id") or "").strip()
                if adapter_id and (model or "").startswith("together/"):
                    payload["adapter_id"] = adapter_id

                return route_chat_strict(model, payload)
            except Exception as e:
                last_err = str(e)
                time.sleep(0.7 * (2 ** i))
                continue

        # Fallback sintético
        synthetic = {
            "choices": [{
                "message": {
                    "content": (
                        "A conexão caiu, mas **mantive a continuidade**. "
                        "Diz o próximo passo em 1 linha e eu continuo do ponto exato."
                    )
                }
            }]
        }
        return synthetic, model, "synthetic-fallback"

    # ---------- Execução de tools ----------
    def _exec_tool_call(self, tool_name: str, args: Dict, usuario_key: str, user: str) -> str:
        try:
            if tool_name == "get_memory_pin":
                return self._build_memory_pin(usuario_key, user)
            if tool_name == "set_fact":
                k = (args or {}).get("key", "")
                v = (args or {}).get("value", "")
                if not k:
                    return "ERRO: chave ausente"
                set_fact(usuario_key, k, v, {"fonte": "tool_call"})
                clear_user_cache(usuario_key)
                return f"OK: {k}={v}"
            if tool_name == "get_fact":
                k = (args or {}).get("key", "")
                if not k:
                    return "ERRO: chave ausente"
                val = get_fact(usuario_key, k, "")
                return f"{k}={val}" if val else f"{k}=<não encontrado>"
            return "ERRO: ferramenta desconhecida"
        except Exception as e:
            return f"ERRO: {e}"

    # ---------- Utilidades principais ----------
    def _load_persona(self) -> Tuple[str, List[Dict[str, str]]]:
        return get_persona()

    def _boot_text(self, history_boot: List[Dict[str, str]]) -> str:
        if history_boot and history_boot[0].get("content"):
            return history_boot[0]["content"].strip()
        return "A porta do guarda-roupas range… a luz azul me revela. Eu te encontrei."

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

    def _safe_int(self, v, default: int) -> int:
        try:
            return int(v or default)
        except Exception:
            return default

    def _pre_msgs_continue(self, prompt: str, portal_aberto: bool) -> List[Dict[str, str]]:
        pl = (prompt or "").lower().strip()
        if pl in ("continue", "continua", "segue", "prossegue", "continua nerith", "continue nerith"):
            if portal_aberto:
                return [{
                    "role": "system",
                    "content": "PEDIDO_CURTO: o usuário pediu para continuar. NÃO mude o cenário. Siga exatamente de onde parou em **Elysarix**."
                }]
            else:
                return [{
                    "role": "system",
                    "content": "PEDIDO_CURTO: o usuário pediu para continuar. NÃO mude o cenário. Siga exatamente de onde parou."
                }]
        return []

    def _get_sensory_focus(self) -> str:
        pool = [
            "pele azul/temperatura", "tendrils/toque", "orelhas pontudas/vibração",
            "olhos esmeralda/contato visual", "língua tendril/beijo",
            "quadris/coxas", "bumbum/pressão", "perfume/doçura", "púbis/penugem sensorial"
        ]
        idx = int(st.session_state.get("nerith_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        return pool[idx]

    def _get_pubis_hint(self, prompt: str, nsfw_on: bool) -> str:
        pl = (prompt or "").lower()
        termos = {"púbis", "pubis", "púbico", "pubico", "pêlos", "pelos", "penugem", "pelinhos"}
        if not any(t in pl for t in termos):
            return ""
        if nsfw_on:
            return "PUBIS_HINT: descreva meus pêlos/filamentos sensoriais reagindo ao toque; linguagem elegante; nunca diga glabro."
        return "PUBIS_HINT: cite sutilmente a penugem sensorial; sem ato explícito; nunca diga glabro."

    def _get_controle_hint(self, fatos: Dict, prompt: str) -> str:
        ctrl_on = bool(fatos.get("controle_psiquico", True))
        if not ctrl_on:
            return "CONTROLE_PSIQUICO: DESATIVADO."
        alvos = str(fatos.get("alvos_controle", "pessoas próximas")).strip()
        contexto_remoto = any(w in (prompt or "").lower() for w in ["trabalho","escritorio","escritório","faculdade","aula","reuni"])
        hint = f"CONTROLE_PSIQUICO: ATIVO. À distância, posso falar através de {alvos} com indícios sutis."
        if contexto_remoto:
            hint += " (Ambiente externo detectado: use um humano próximo como canal.)"
        return hint

    def _get_ciume_hint(self, fatos: Dict) -> str:
        ciume_laura = bool(fatos.get("ciume_de_laura", True))
        obedece_defesa = bool(fatos.get("obedecer_defesa_de_laura", True))
        if not ciume_laura:
            return "CIÚME/LAURA: atenuado."
        hint = "CIÚME/LAURA: presença elegante e assertiva quando Laura é citada"
        if obedece_defesa:
            hint += "; acate com elegância se ele a defender."
        return hint

    def _get_ferrao_hint(self) -> str:
        return (
            "FERRAO_ONIRICO: quando transamos de quatro e você topa, posso usar meu ferrão. "
            "Não dói — sensação quente/pulsante; intensifica orgasmo. Pode me engravidar (apenas em Elysarix). "
            "Peça consentimento explícito antes."
        )

    def _get_elysarix_hint(self, fatos: Dict) -> str:
        gravidez = bool(fatos.get("gravidez_elysarix", False))
        mundo = str(fatos.get("mundo_escolhido", "") or "").strip().lower()
        portal_on = bool(fatos.get("portal_ativo", True))
        if mundo in {"humano", "elysarix"} and not portal_on:
            return f"ELYSARIX: MUNDO_ESCOLHIDO={mundo.upper()} • PORTAL=ENCERRADO. Portal existe apenas como lembrança; não ofereça reabrir."
        if gravidez and portal_on:
            return "ELYSARIX: Gravidez confirmada. Ofereça decisão de mundo com consequências e consentimento."
        return "ELYSARIX: Sem escolha ativa. Portal disponível conforme regras."

    def _detect_elysarix_scene(self, texto: str) -> bool:
        low = (texto or "").lower()
        gatilhos = [
            "elysarix", "duas luas", "floresta de cristal", "lago de águas cristalinas",
            "portal atrás de nós", "sob as duas luas", "retornar para o mundo humano"
        ]
        return any(g in low for g in gatilhos)

    def _detect_and_update_local(self, usuario_key: str, assistant_msg: str, portal_aberto: bool = False):
        msg_lower = (assistant_msg or "").lower()

        if portal_aberto:
            gatilhos_volta = [
                "atravessamos o portal de volta",
                "o portal se fecha atrás de nós",
                "decidimos voltar para o quarto",
                "voltamos para o quarto humano",
                "voltamos para o mundo humano",
            ]
            if any(g in msg_lower for g in gatilhos_volta):
                set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "auto_detect_explicit"})
                clear_user_cache(usuario_key)
            return

        if any(p in msg_lower for p in [
            "bem-vindo a elysarix", "bem-vinda a elysarix", "chegamos em elysarix",
            "entramos em elysarix", "você está em elysarix", "estamos em elysarix"
        ]):
            set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "auto_detect"})
            clear_user_cache(usuario_key)
            return

        if any(p in msg_lower for p in [
            "voltamos para o quarto", "de volta ao mundo humano", "atravessamos o portal de volta",
            "laura ainda dorme", "você está de volta"
        ]):
            set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "auto_detect"})
            clear_user_cache(usuario_key)
            return

    def _check_user_location_command(self, prompt: str) -> str | None:
        pl = (prompt or "").lower()
        if any(w in pl for w in ["estamos em elysarix", "estou em elysarix", "chegamos em elysarix", "para elysarix"]):
            return "Elysarix"
        if any(w in pl for w in ["estamos no quarto", "estou no quarto", "voltamos para casa", "voltamos pro quarto"]):
            return "quarto"
        return None

    # ---------- Histórico ----------
    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 10,
    ) -> List[Dict[str, str]]:
        docs = cached_get_history(usuario_key, limit=50) or []
        if not docs:
            return history_boot[:] if history_boot else []

        # Constrói pares (ordem cronológica)
        pares: List[Dict[str, str]] = []
        # muitos repositórios voltam já do mais novo pro mais velho → invertendo
        docs = list(reversed(docs))
        for d in docs:
            u = (d.get("mensagem_usuario") or d.get("user_message") or d.get("user") or d.get("input") or d.get("prompt") or "").strip()
            a = (d.get("resposta_nerith") or d.get("assistant_message") or d.get("assistant") or d.get("output") or d.get("response") or "").strip()
            if u:
                pares.append({"role": "user", "content": u})
            if a:
                pares.append({"role": "assistant", "content": a})

        if not pares:
            return history_boot[:] if history_boot else []

        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        antigos  = pares[:len(pares) - len(verbatim)]

        msgs: List[Dict[str, str]] = []
        if antigos:
            bloco = "\n\n".join(m["content"] for m in antigos)
            resumo = self._sum_bloco(model, bloco)
            if resumo:
                msgs.append({"role": "system", "content": f"[RESUMO]\n{resumo}"})

        msgs.extend(verbatim)

        # Poda se estourar muito (reserva 60% do contexto ao histórico)
        win = self._get_window_for(model)
        hist_budget = max(8000, int(win * 0.60))
        def _tok(mm): return sum(toklen(m["content"]) for m in mm)
        while _tok(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2:
                verbatim = verbatim[2:]
            else:
                verbatim = []
            msgs = [m for m in msgs if m["role"] == "system"] + verbatim

        return msgs if msgs else (history_boot[:] if history_boot else [])

    def _sum_bloco(self, model: str, texto: str) -> str:
        seed = (
            "Resuma em 6–10 frases telegráficas, apenas fatos duráveis (nomes, locais/tempo atual, "
            "decisões, objetos fixos, rumo da cena). Proibido diálogo literal."
        )
        try:
            data, _, _ = route_chat_strict(model, {
                "model": model,
                "messages": [{"role": "system", "content": seed}, {"role": "user", "content": texto[:6000]}],
                "max_tokens": 220, "temperature": 0.2, "top_p": 0.9
            })
            txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
            return txt.strip()
        except Exception:
            # heurístico simples
            s = " ".join(texto.split())
            parts = s.split(". ")
            return " • " + "\n • ".join(parts[:10])

    def _get_window_for(self, model: str) -> int:
        MODEL_WINDOWS = {
            "anthropic/claude-3.5-haiku": 200_000,
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": 128_000,
            "together/Qwen/Qwen2.5-72B-Instruct": 32_000,
            "deepseek/deepseek-chat-v3-0324": 32_000,
        }
        return MODEL_WINDOWS.get((model or "").strip(), 32_000)

    # ---------- Placeholders/UX ----------
    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s:
            return "Continua do ponto exato… me conduz."
        if any(k in s for k in ["vamos", "topa", "que tal", "prefere", "quer"]):
            return "Quero — descreve devagar o próximo passo."
        if scene_loc:
            return f"No {scene_loc} mesmo — fala baixinho no meu ouvido."
        return "Mantém o cenário e dá o próximo passo com calma."

    # ---------- Sidebar ----------
    def render_sidebar(self, sidebar) -> None:
        st.session_state.setdefault("json_mode_on", False)
        st.session_state.setdefault("tool_calling_on", True)
        st.session_state.setdefault("adapter_id", "")

        sidebar.subheader("⚙️ Nerith — Controles")
        json_on = sidebar.checkbox("JSON Mode", value=bool(st.session_state["json_mode_on"]))
        tool_on = sidebar.checkbox("Tool-Calling", value=bool(st.session_state["tool_calling_on"]))
        st.session_state["json_mode_on"] = json_on
        st.session_state["tool_calling_on"] = tool_on

        adapter_id = sidebar.text_input("Adapter ID (Together LoRA) — opcional", value=st.session_state.get("adapter_id",""))
        st.session_state["adapter_id"] = adapter_id

        sidebar.markdown("---")
        if sidebar.button("🆘 Modo Seguro (Nerith) — Forçar BOOT"):
            st.session_state["json_mode_on"] = False
            st.session_state["tool_calling_on"] = False
            st.session_state["adapter_id"] = ""
            st.session_state["nerith_force_boot"] = True
            for k in ("chat_input","user_input","last_user_message","prompt"):
                st.session_state.pop(k, None)
            sidebar.success("Ativado: JSON off, Tool-Calling off, LoRA off, BOOT forçado. Envie qualquer mensagem para receber a fala inicial.")

# ===== Funções auxiliares soltas (seu projeto aceita) =====
def _current_user_key(user: str) -> str:
    return f"{user or 'anon'}::nerith"
