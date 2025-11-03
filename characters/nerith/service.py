# characters/nerith/service.py
# NerithService — com continuidade como Mary (histórico orçado + resumo rolante + lore + mem pin)
from __future__ import annotations

import re, time, json, random
from typing import List, Dict, Tuple
from datetime import datetime
import streamlit as st

from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, last_event, set_fact,
)
from core.tokens import toklen

# ===== LORE opcional (mesma API usada pela Mary) =====
try:
    from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
except Exception:
    lore_topk = None
    def lore_save(*_a, **_k): return None

# ===== NSFW (opcional) =====
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool:
        return False

# ===== Persona =====
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é **NERITH**, elfa de pele azulada, olhos esmeralda e presença dominante e terna. "
            "Fale em 1ª pessoa, conduzindo com confiança e charme. 4–7 parágrafos; 2–4 frases cada."
        )
        return txt, []

# ===== Tool Calling =====
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_memory_pin",
            "description": "Retorna fatos canônicos curtos persistidos para Nerith (linha compacta).",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_fact",
            "description": "Salva/atualiza um fato canônico (chave/valor) para Nerith.",
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
            "description": "Busca um fato específico na memória de Nerith.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"]
            }
        }
    },
]

# ===== Janelas por modelo (mesmas heurísticas da Mary) =====
MODEL_WINDOWS = {
    "anthropic/claude-3.5-haiku": 200_000,
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": 128_000,
    "together/Qwen/Qwen2.5-72B-Instruct": 32_000,
    "deepseek/deepseek-chat-v3-0324": 32_000,
    "inclusionai/ling-1t": 64_000,
}
DEFAULT_WINDOW = 32_000

def _get_window_for(model: str) -> int:
    return MODEL_WINDOWS.get((model or "").strip(), DEFAULT_WINDOW)

def _budget_slices(model: str) -> tuple[int, int, int]:
    win = _get_window_for(model)
    hist = max(8_000, int(win * 0.60))
    meta = int(win * 0.20)
    outb = int(win * 0.20)
    return hist, meta, outb

def _safe_max_output(win: int, prompt_tokens: int) -> int:
    alvo = int(win * 0.20)
    sobra = max(0, win - prompt_tokens - 256)
    return max(512, min(alvo, sobra))

# ===== Cache leve (facts/history) =====
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))
_cache_facts: Dict[str, Dict] = {}
_cache_history: Dict[str, List[Dict]] = {}
_cache_timestamps: Dict[str, float] = {}

def _purge_expired_cache():
    now = time.time()
    for k in list(_cache_facts.keys()):
        if now - _cache_timestamps.get(f"facts_{k}", 0) >= CACHE_TTL:
            _cache_facts.pop(k, None); _cache_timestamps.pop(f"facts_{k}", None)
    for k in list(_cache_history.keys()):
        if now - _cache_timestamps.get(f"hist_{k}", 0) >= CACHE_TTL:
            _cache_history.pop(k, None); _cache_timestamps.pop(f"hist_{k}", None)

def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None)
    _cache_timestamps.pop(f"facts_{user_key}", None)
    _cache_history.pop(user_key, None)
    _cache_timestamps.pop(f"hist_{user_key}", None)

def cached_get_facts(user_key: str) -> Dict:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_facts and (now - _cache_timestamps.get(f"facts_{user_key}", 0) < CACHE_TTL):
        return _cache_facts[user_key]
    try:
        f = get_facts(user_key) or {}
    except Exception:
        f = {}
    _cache_facts[user_key] = f
    _cache_timestamps[f"facts_{user_key}"] = now
    return f

def cached_get_history(user_key: str) -> List[Dict]:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_history and (now - _cache_timestamps.get(f"hist_{user_key}", 0) < CACHE_TTL):
        return _cache_history[user_key]
    try:
        docs = get_history_docs(user_key) or []
    except Exception:
        docs = []
    _cache_history[user_key] = docs
    _cache_timestamps[f"hist_{user_key}"] = now
    return docs

# ===== UI: aviso de poda/resumo =====
def _mem_drop_warn(report: dict) -> None:
    if not report: return
    summarized = int(report.get("summarized_pairs", 0))
    trimmed    = int(report.get("trimmed_pairs", 0))
    hist_tokens = int(report.get("hist_tokens", 0))
    hist_budget = int(report.get("hist_budget", 0))
    if summarized or trimmed:
        msg = []
        if summarized: msg.append(f"**{summarized}** turnos antigos **foram resumidos**")
        if trimmed:    msg.append(f"**{trimmed}** turnos verbatim **foram podados**")
        txt = " e ".join(msg)
        st.info(
            f"⚠️ Memória ajustada: {txt}. (histórico: {hist_tokens}/{hist_budget} tokens). "
            "Se notar esquecimentos, peça um **‘recap curto’** ou fixe fatos na **Memória Canônica**.",
            icon="⚠️",
        )

# ===== Helpers de resumo (como Mary) =====
def _heuristic_summarize(texto: str, max_bullets: int = 10) -> str:
    texto = re.sub(r"\s+", " ", (texto or "").strip())
    sent = re.split(r"(?<=[\.\!\?])\s+", texto)
    sent = [s.strip() for s in sent if s.strip()]
    return " • " + "\n • ".join(sent[:max_bullets])

def _llm_summarize(model: str, user_chunk: str) -> str:
    seed = (
        "Resuma em 6–10 frases telegráficas, somente fatos duráveis (decisões, nomes, locais, tempo, "
        "gestos/itens fixos e rumo da cena). Proibido diálogo literal."
    )
    try:
        data, used_model, provider = route_chat_strict(model, {
            "model": model,
            "messages": [
                {"role": "system", "content": seed},
                {"role": "user", "content": user_chunk}
            ],
            "max_tokens": 220,
            "temperature": 0.2,
            "top_p": 0.9
        })
        txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        return txt.strip() or _heuristic_summarize(user_chunk)
    except Exception:
        return _heuristic_summarize(user_chunk)

# ===== Class =====
class NerithService(BaseCharacter):
    id: str = "nerith"
    display_name: str = "Nerith"

    # ------------------- API -------------------
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        persona_text, history_boot = self._load_persona()
        usuario_key = f"{user}::nerith"

        # histórico mínimo (só para decidir boot)
        existing_history = cached_get_history(usuario_key)

        # fatos atuais
        f0 = cached_get_facts(usuario_key)
        local_registrado = (f0.get("local_cena_atual") or "").lower()
        portal_registrado = str(f0.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")

        # Boot (sem prompt)
        if not prompt:
            if existing_history:
                # devolve última fala se existir
                try:
                    last = existing_history[-1]
                    return last.get("assistant_message") or last.get("resposta_nerith") or last.get("assistant") or "..."
                except Exception:
                    return "..."
            boot_text = (
                history_boot[0].get("content", "")
                if (history_boot and len(history_boot) > 0)
                else "A porta do guarda-roupas se abre sozinha. A luz azul me revela. Eu te encontrei."
            )
            save_interaction(usuario_key, "", boot_text, "system:boot")
            if portal_registrado or local_registrado == "elysarix":
                set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "boot-preserva"})
            else:
                set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "boot"})
            clear_user_cache(usuario_key)
            return boot_text

        # tool-calling toggles
        tool_calling_on = st.session_state.get("tool_calling_on", False)
        tools = TOOLS if tool_calling_on else None
        max_iterations = 3 if tool_calling_on else 1

        # intenção (gravidez/escolha de mundo)
        state_msgs = self._apply_world_choice_intent(usuario_key, prompt)

        # comando manual de local
        user_location = self._check_user_location_command(prompt)
        if user_location:
            set_fact(usuario_key, "local_cena_atual", user_location, {"fonte": "user_command"})
            clear_user_cache(usuario_key)

        # estado atual
        local_atual = self._safe_get_local(usuario_key)
        f_all = cached_get_facts(usuario_key)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        portal_aberto = str(f_all.get("portal_aberto", "")).lower() in ("true", "1", "yes", "sim")

        # proteção para "continue"
        pl = prompt.lower().strip()
        continue_pre_msgs: List[Dict[str, str]] = []
        if pl in ("continue", "continua", "segue", "prossegue", "continua nerith", "continue nerith"):
            if portal_aberto:
                continue_pre_msgs.append({"role": "system",
                    "content": "PEDIDO_CURTO: o usuário só disse para continuar. NÃO mude o cenário. Continue exatamente de onde parou em ELYSARIX."})
            else:
                continue_pre_msgs.append({"role": "system",
                    "content": "PEDIDO_CURTO: o usuário só disse para continuar. NÃO mude o cenário. Continue exatamente de onde parou."})

        # se portal já aberto mas local não é Elysarix → força
        if portal_aberto and (not local_atual or local_atual.lower() != "elysarix"):
            local_atual = "Elysarix"
            set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "reidrata_depois_toggle"})
            clear_user_cache(usuario_key)

        # parâmetros com fallback
        def _safe_int(v, d=1):
            try: return int(v or d)
            except Exception: return d
        dreamworld_detail_level = _safe_int(f_all.get("dreamworld_detail_level"), 1)
        guide_assertiveness = _safe_int(f_all.get("guide_assertiveness"), 1)

        # foco sensorial rotativo
        foco = self._get_sensory_focus()

        # NSFW
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False
        nsfw_hint = (
            "NSFW: LIBERADO. Erotismo consensual e progressivo; descreva com elegância."
            if nsfw_on else
            "NSFW: BLOQUEADO. Flerte, tensão e fade-to-black."
        )

        # Hints principais
        length_hint = "COMPRIMENTO: gere 4–7 parágrafos, cada um com 2–4 frases naturais."
        sensory_hint = f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, insira 1–2 pistas envolvendo **{foco}**, fundidas à ação."
        tone_hint = "TOM: confiante, assertiva e dominante no charme; nunca submissa/infantil."

        # Demais hints (mantidos do seu service anterior)
        pubis_hint = self._get_pubis_hint(prompt, nsfw_on)
        controle_hint = self._get_controle_hint(f_all, prompt)
        ciume_hint = self._get_ciume_hint(f_all)
        ferrao_hint = self._get_ferrao_hint()
        elysarix_hint = self._get_elysarix_hint(f_all)
        if portal_aberto:
            elysarix_hint += "\n⚠️ Já estamos em Elysarix — não repita a travessia nem a introdução. Continue a cena do ponto atual."

        # ===== ROLLING SUMMARY + ENTIDADES + EVIDENCE (como Mary) =====
        rolling = self._get_rolling_summary(usuario_key)  # nerith.rs.v2
        entities_line = self._entities_to_line(f_all)

        docs = cached_get_history(usuario_key) or []
        evidence = self._compact_user_evidence(docs, max_chars=320)

        # ===== System principal =====
        system_block = "\n\n".join([
            persona_text,
            tone_hint,
            length_hint,
            sensory_hint,
            nsfw_hint,
            ferrao_hint,
            controle_hint,
            ciume_hint,
            pubis_hint,
            elysarix_hint,
            "CONTINUIDADE: não mude tempo/lugar sem pedido explícito.",
            f"MEMÓRIA (canon curto): {rolling or '—'}",
            f"ENTIDADES: {entities_line or '—'}",
            f"EVIDÊNCIA RECENTE (resumo ultra-curto do usuário): {evidence or '—'}",
            "FERRAMENTAS: use get_memory_pin para recuperar estado, get_fact para saber se o portal já foi atravessado e set_fact para marcar portal_aberto=True ao entrar em Elysarix. Nunca repita a travessia se portal_aberto=True.",
        ])

        # ===== LORE (nerith) =====
        lore_msgs: List[Dict[str, str]] = []
        try:
            if lore_topk:
                q = (prompt or "") + "\n" + (rolling or "")
                top = lore_topk(usuario_key, q, k=4, allow_tags=None)
                if top:
                    lore_text = " | ".join(d.get("texto", "") for d in top if d.get("texto"))
                    if lore_text:
                        lore_msgs.append({"role": "system", "content": f"[LORE]\n{lore_text}"})
        except Exception:
            pass

        # ===== Histórico — orçamento por modelo (como Mary) =====
        verbatim_ultimos = int(st.session_state.get("verbatim_ultimos", 10))
        hist_msgs = self._montar_historico(
            usuario_key, history_boot, model, verbatim_ultimos=verbatim_ultimos
        )

        # ===== Mensagens finais =====
        pre_msgs: List[Dict[str, str]] = []
        if state_msgs: pre_msgs.extend(state_msgs)
        if continue_pre_msgs: pre_msgs.extend(continue_pre_msgs)

        messages: List[Dict] = (
            pre_msgs
            + [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + lore_msgs
            + [{
                "role": "system",
                "content": f"LOCAL_ATUAL: {local_atual or '—'}. Regra dura: NÃO mude o cenário salvo pedido explícito."
            }]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        # Aviso visual se houve resumo/poda
        try: _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))
        except Exception: pass

        # ===== Orçamento de saída e temperatura =====
        win = _get_window_for(model)
        try:
            prompt_tokens = sum(toklen(m.get("content","")) for m in messages if m.get("content"))
        except Exception:
            prompt_tokens = 0
        max_out = _safe_max_output(win, prompt_tokens)

        ritmo = str(f_all.get("nerith.pref.ritmo","lento") or "lento").lower()
        temperature = 0.6 if ritmo == "lento" else (0.9 if ritmo == "rapido" else 0.7)

        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]

        # ===== Loop de Tool Calling =====
        tools_to_use = TOOLS if tool_calling_on else None
        iteration = 0
        texto = ""
        while iteration < max_iterations:
            iteration += 1

            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_out,
                "temperature": temperature,
                "top_p": 0.95,
            }
            if tools_to_use:
                payload["tools"] = tools_to_use
            if st.session_state.get("json_mode_on", False):
                payload["response_format"] = {"type": "json_object"}

            adapter_id = (st.session_state.get("together_lora_id", "") or st.session_state.get("adapter_id","")).strip()
            if adapter_id and (model or "").startswith("together/"):
                payload["adapter_id"] = adapter_id

            data, used_model, provider = self._robust_chat_call(payload, fallbacks)

            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls", [])

            if not tool_calls or not tool_calling_on:
                # resposta final
                save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
                self._detect_and_update_local(usuario_key, texto, portal_aberto=portal_aberto)
                clear_user_cache(usuario_key)
                if self._detect_elysarix_scene(texto):
                    set_fact(usuario_key, "portal_aberto", "True", {"fonte": "auto_detect_portal"})
                    clear_user_cache(usuario_key)
                # LORE persist
                try:
                    if texto:
                        lore_save(usuario_key, f"[USER] {prompt}\n[NERITH] {texto}", tags=["nerith","chat"])
                except Exception:
                    pass
                # rolling summary
                try:
                    self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
                except Exception:
                    pass
                # placeholder
                try:
                    st.session_state["last_assistant_message"] = texto
                except Exception:
                    pass
                return texto

            # processar tool calls
            st.caption(f"🔧 Executando {len(tool_calls)} ferramenta(s)...")
            messages.append({
                "role": "assistant",
                "content": texto or None,
                "tool_calls": tool_calls
            })
            for tc in tool_calls:
                tool_id = tc.get("id", f"call_{iteration}")
                func_name = tc.get("function", {}).get("name", "")
                func_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}
                except Exception:
                    func_args = {}
                result = self._exec_tool_call(func_name, func_args, usuario_key, user)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": func_name,
                    "content": result
                })
                st.caption(f"  ✓ {func_name}: {result[:60]}...")

            if iteration >= max_iterations:
                st.warning("⚠️ Limite de iterações atingido. Finalizando...")

        # fallback final
        save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        return texto

    # ------------------- Robust call -------------------
    def _robust_chat_call(self, payload: Dict, fallbacks: List[str]) -> Tuple[Dict, str, str]:
        attempts = 3
        last_err = ""
        model = payload.get("model","")
        for i in range(attempts):
            try:
                return route_chat_strict(model, payload)
            except Exception as e:
                last_err = str(e)
                if "cloudflare" in last_err.lower() or "502" in last_err:
                    time.sleep((0.7 * (2 ** i)) + random.uniform(0, .4))
                    continue
                break
        for fb in fallbacks:
            try:
                payload_fb = dict(payload); payload_fb["model"] = fb
                return route_chat_strict(fb, payload_fb)
            except Exception as e2:
                last_err = str(e2)
        synthetic = {
            "choices": [{"message": {"content":
                "Amor… o provedor oscilou agora, mas mantive o cenário. Diz numa linha o que quer e eu continuo."}}]
        }
        return synthetic, model, "synthetic-fallback"

    # ------------------- Tools -------------------
    def _exec_tool_call(self, name: str, args: dict, usuario_key: str, user_display: str) -> str:
        try:
            if name == "get_memory_pin":
                return self._build_memory_pin(usuario_key, user_display)
            if name == "set_fact":
                k = (args or {}).get("key", "")
                v = (args or {}).get("value", "")
                if not k: return "ERRO: key vazia."
                set_fact(usuario_key, k, v, {"fonte": "tool_call"})
                clear_user_cache(usuario_key)
                return f"OK: {k}={v}"
            if name == "get_fact":
                k = (args or {}).get("key", "")
                if not k: return "ERRO: key vazia."
                val = get_fact(usuario_key, k, "")
                return f"{k}={val}" if val else f"{k}=<não encontrado>"
            return "ERRO: ferramenta desconhecida"
        except Exception as e:
            return f"ERRO: {e}"

    # ------------------- Histórico (Mary-style) -------------------
    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 10,
    ) -> List[Dict[str, str]]:
        """
        - Preserva últimos N turnos verbatim (N=verbatim_ultimos).
        - Antigos viram [RESUMO-*] (1–3 camadas).
        - Orçado à janela do modelo.
        - Aceita **muitos formatos** de campos do repo.
        Gera relatório em st.session_state['_mem_drop_report'].
        """
        win = _get_window_for(model)
        hist_budget, _, _ = _budget_slices(model)

        docs = cached_get_history(usuario_key)
        if not docs:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:] if history_boot else []

        pares: List[Dict[str, str]] = []

        # Aceita vários esquemas de chave:
        for d in docs:
            # usuário
            u = (
                d.get("mensagem_usuario") or d.get("user_message")
                or d.get("user") or d.get("input") or d.get("prompt") or ""
            )
            # assistente — variantes com NERITH
            a = (
                d.get("resposta_nerith") or d.get("assistant_message")
                or d.get("assistant") or d.get("output") or d.get("response") or ""
            )
            if u: pares.append({"role": "user", "content": u.strip()})
            if a: pares.append({"role": "assistant", "content": a.strip()})

        if not pares:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:] if history_boot else []

        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        antigos  = pares[:len(pares) - len(verbatim)]

        msgs: List[Dict[str, str]] = []
        summarized_pairs = 0
        trimmed_pairs = 0

        if antigos:
            summarized_pairs = len(antigos) // 2
            bloco = "\n\n".join(m["content"] for m in antigos)
            resumo = _llm_summarize(model, bloco)
            resumo_layers = [resumo]

            def _count_total_sim(resumo_texts: List[str]) -> int:
                sim_msgs = [{"role": "system", "content": f"[RESUMO-{i+1}]\n{r}"} for i, r in enumerate(resumo_texts)]
                sim_msgs += verbatim
                return sum(toklen(m["content"]) for m in sim_msgs)

            while _count_total_sim(resumo_layers) > hist_budget and len(resumo_layers) < 3:
                resumo_layers[0] = _llm_summarize(model, resumo_layers[0])

            for i, r in enumerate(resumo_layers, start=1):
                msgs.append({"role": "system", "content": f"[RESUMO-{i}]\n{r}"})

        msgs.extend(verbatim)

        def _hist_tokens(mm: List[Dict]) -> int:
            return sum(toklen(m.get("content","")) for m in mm if m.get("content"))

        while _hist_tokens(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2:
                verbatim = verbatim[2:]
                trimmed_pairs += 1
            else:
                verbatim = []
            msgs = [m for m in msgs if m["role"] == "system"] + verbatim

        hist_tokens = _hist_tokens(msgs)
        st.session_state["_mem_drop_report"] = {
            "summarized_pairs": summarized_pairs,
            "trimmed_pairs": trimmed_pairs,
            "hist_tokens": hist_tokens,
            "hist_budget": hist_budget,
        }

        return msgs if msgs else (history_boot[:] if history_boot else [])

    # ------------------- Rolling summary (nerith.rs.v2) -------------------
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
            return str(f.get("nerith.rs.v2", "") or f.get("nerith.rolling_summary", "") or "")
        except Exception:
            return ""

    def _should_update_summary(self, usuario_key: str, last_user: str, last_assistant: str) -> bool:
        try:
            f = cached_get_facts(usuario_key)
            last_summary = f.get("nerith.rs.v2", "")
            last_update_ts = float(f.get("nerith.rs.v2.ts", 0))
            now = time.time()
            if not last_summary: return True
            if now - last_update_ts > 300: return True
            if (len(last_user) + len(last_assistant)) > 100: return True
            return False
        except Exception:
            return True

    def _update_rolling_summary_v2(self, usuario_key: str, model: str, last_user: str, last_assistant: str) -> None:
        if not self._should_update_summary(usuario_key, last_user, last_assistant):
            return
        seed = (
            "Resuma a conversa recente em ATÉ 8–10 frases, apenas fatos duráveis: "
            "nomes próprios, relação, local/tempo atual, itens/gestos fixos e rumo do enredo. "
            "Proíba diálogos literais."
        )
        try:
            data, used_model, provider = route_chat_strict(model, {
                "model": model,
                "messages": [
                    {"role": "system", "content": seed},
                    {"role": "user", "content": f"USER:\n{last_user}\n\nNERITH:\n{last_assistant}"}
                ],
                "max_tokens": 180,
                "temperature": 0.2,
                "top_p": 0.9,
            })
            resumo = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "").strip()
            if resumo:
                set_fact(usuario_key, "nerith.rs.v2", resumo, {"fonte": "auto_summary"})
                set_fact(usuario_key, "nerith.rs.v2.ts", time.time(), {"fonte": "auto_summary"})
                clear_user_cache(usuario_key)
        except Exception:
            pass

    # ------------------- Entities line (opcional) -------------------
    _ENTITY_KEYS = ("ely_loc", "ely_item", "ely_lugar", "humano_lugar", "contato_ponte")
    def _entities_to_line(self, f: Dict) -> str:
        parts = []
        for k in self._ENTITY_KEYS:
            v = str(f.get(f"nerith.entity.{k}", "") or "").strip()
            if v:
                parts.append(f"{k}={v}")
        return "; ".join(parts) if parts else "—"

    # ------------------- Evidence compacta -------------------
    def _compact_user_evidence(self, docs: List[Dict], max_chars: int = 320) -> str:
        snippets: List[str] = []
        for d in reversed(docs):
            u = (
                d.get("mensagem_usuario") or d.get("user_message")
                or d.get("user") or d.get("input") or d.get("prompt") or ""
            )
            if u:
                u = re.sub(r"\s+", " ", u)
                snippets.append(u)
            if len(snippets) >= 4: break
        s = " | ".join(reversed(snippets))[:max_chars]
        return s

    # ------------------- Hints auxiliares -------------------
    def _get_sensory_focus(self) -> str:
        pool = [
            "pele azul/temperatura", "tendrils/toque", "orelhas pontudas/vibração",
            "olhos esmeralda/contato visual", "língua tendril/beijo", "altura/postura",
            "quadris/coxas", "bumbum/pressão", "perfume/doçura na saliva",
            "púbis/pêlos sensoriais"
        ]
        idx = int(st.session_state.get("nerith_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["nerith_attr_idx"] = idx
        return pool[idx]

    def _get_pubis_hint(self, prompt: str, nsfw_on: bool) -> str:
        pl = (prompt or "").lower()
        pubis_terms = {"púbis", "pubis", "púbico", "pubico", "pêlos", "pelos", "penugem", "pelinhos"}
        if not any(t in pl for t in pubis_terms): return ""
        if nsfw_on:
            return ("PUBIS_HINT: o usuário citou o púbis/pêlos. Descreva meus **pêlos/filamentos sensoriais** reagindo ao toque com linguagem elegante. **Nunca** diga que é glabro.")
        return ("PUBIS_HINT: o usuário citou o púbis/pêlos. Mencione meus **pêlos/filamentos sensoriais** sutilmente, sem ato explícito. **Nunca** diga que é glabro.")

    def _get_controle_hint(self, fatos: Dict, prompt: str) -> str:
        ctrl_on = bool(fatos.get("controle_psiquico", True))
        if not ctrl_on: return "CONTROLE_PSIQUICO: DESATIVADO."
        alvos = str(fatos.get("alvos_controle", "pessoas próximas")).strip()
        pl = (prompt or "").lower()
        contexto_remoto = any(w in pl for w in ["trabalho","escritorio","escritório","reuni","faculdade","aula"])
        hint = (f"CONTROLE_PSIQUICO: ATIVO. Quando distante, posso falar através de {alvos} de modo natural; indícios sutis.")
        if contexto_remoto: hint += " (Ambiente externo detectado: fale através de um humano próximo.)"
        return hint

    def _get_ciume_hint(self, fatos: Dict) -> str:
        ciume_laura = bool(fatos.get("ciume_de_laura", True))
        obedece_defesa = bool(fatos.get("obedecer_defesa_de_laura", True))
        if not ciume_laura: return "CIÚME/LAURA: atenuado."
        hint = "CIÚME/LAURA: presença elegante e assertiva quando Laura é mencionada"
        if obedece_defesa: hint += "; acate com elegância se ele a defender."
        return hint

    def _get_ferrao_hint(self) -> str:
        return (
            "FERRAO_ONIRICO: quando transamos de quatro e você topa, posso usar meu ferrão. "
            "Sensaçao quente e pulsante; peça consentimento explícito antes. Em Elysarix, pode engravidar."
        )

    def _get_elysarix_hint(self, fatos: Dict) -> str:
        gravidez = bool(fatos.get("gravidez_elysarix", False))
        mundo = str(fatos.get("mundo_escolhido", "") or "").strip().lower()
        portal_on = bool(fatos.get("portal_ativo", True))
        if mundo in {"humano","elysarix"} and not portal_on:
            return f"ELYSARIX: MUNDO_ESCOLHIDO={mundo.upper()} • PORTAL=ENCERRADO. Portal só existe como lembrança."
        elif gravidez and portal_on:
            return "ELYSARIX: Gravidez confirmada. Escolha de mundo disponível; explique consequências."
        else:
            return "ELYSARIX: Sem escolha ativa. Portal disponível conforme regras."

    # ------------------- Local/cena -------------------
    def _detect_elysarix_scene(self, texto: str) -> bool:
        if not texto: return False
        low = texto.lower()
        gatilhos = [
            "duas luas", "elysarix", "floresta de cristal", "lago de águas cristalinas",
            "portal atrás de nós", "retornar para o mundo humano", "um dia inteiro equivale a um minuto",
            "sob as duas luas"
        ]
        return any(g in low for g in gatilhos)

    def _detect_and_update_local(self, usuario_key: str, assistant_msg: str, portal_aberto: bool = False):
        msg_lower = (assistant_msg or "").lower()

        if portal_aberto:
            gatilhos_volta_explicit = [
                "atravessamos o portal de volta",
                "o portal se fecha atrás de nós",
                "decidimos voltar para o quarto",
                "voltamos para o quarto humano",
                "voltamos pro quarto humano",
                "voltamos para o mundo humano",
                "voltamos pro mundo humano",
            ]
            if any(g in msg_lower for g in gatilhos_volta_explicit):
                set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "auto_detect_explicit"})
                clear_user_cache(usuario_key)
            return

        if any(phrase in msg_lower for phrase in [
            "bem-vindo a elysarix", "bem-vinda a elysarix", "chegamos em elysarix",
            "entramos em elysarix", "portal se fecha atrás", "você está em elysarix", "estamos em elysarix"
        ]):
            set_fact(usuario_key, "local_cena_atual", "Elysarix", {"fonte": "auto_detect"})
            clear_user_cache(usuario_key)
            return

        if any(phrase in msg_lower for phrase in [
            "voltamos para o quarto", "de volta ao mundo humano", "atravessamos o portal de volta",
            "laura ainda dorme", "você está de volta"
        ]):
            set_fact(usuario_key, "local_cena_atual", "quarto", {"fonte": "auto_detect"})
            clear_user_cache(usuario_key)
            return

    def _check_user_location_command(self, prompt: str) -> str | None:
        pl = (prompt or "").lower()
        if any(w in pl for w in ["estamos em elysarix", "estou em elysarix", "chegamos em elysarix", "ficamos em elysarix"]):
            return "Elysarix"
        if any(w in pl for w in ["estamos no quarto", "estou no quarto", "voltamos para casa", "voltamos pro quarto", "ficar no quarto"]):
            return "quarto"
        return None

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}
        blocos: List[str] = []

        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = (parceiro or user_display).strip()
        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")

        # relacionamento base
        casal = bool(f.get("nerith.relacao.casal", True))
        blocos.append(f"casal={casal}")

        # entidades
        ent_line = self._entities_to_line(f)
        if ent_line and ent_line != "—":
            blocos.append(f"entidades=({ent_line})")

        # evento marcante (se existir)
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
            "MEMÓRIA_PIN_NERITH: "
            f"NOME_USUARIO={nome_usuario}. FATOS={{ {mem_str} }}. "
            "Use ENTIDADES como fonte de verdade; se ausente, não invente."
        )
        return pin

    # ------------------- Persona/base -------------------
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

    # ------------------- Sidebar -------------------
    def render_sidebar(self, sidebar):
        st.session_state.setdefault("json_mode_on", True)
        st.session_state.setdefault("tool_calling_on", True)
        st.session_state.setdefault("together_lora_id", "")
        st.session_state.setdefault("adapter_id", "")

        sidebar.subheader("⚙️ Configurações Nerith")
        st.session_state["json_mode_on"] = sidebar.checkbox(
            "JSON Mode", value=bool(st.session_state.get("json_mode_on", True))
        )
        st.session_state["tool_calling_on"] = sidebar.checkbox(
            "Tool-Calling", value=bool(st.session_state.get("tool_calling_on", True))
        )
        st.session_state["together_lora_id"] = sidebar.text_input(
            "Adapter ID (Together LoRA) — opcional", value=st.session_state.get("together_lora_id", "")
        )
        st.session_state["adapter_id"] = sidebar.text_input(
            "Adapter ID (genérico) — opcional", value=st.session_state.get("adapter_id", "")
        )
        sidebar.markdown("---")
