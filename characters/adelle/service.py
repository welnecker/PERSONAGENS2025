# characters/adelle/service.py
from __future__ import annotations

import re, time, random
from typing import List, Dict, Tuple
import streamlit as st

from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
from core.ultra import critic_review, polish
from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, last_event, set_fact
)
from core.tokens import toklen
import json

# === Tool Calling: definição de ferramentas disponíveis para Adelle ===
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_mission_briefing",
            "description": "Retorna o resumo da missão atual e os fatos canônicos sobre os alvos (família Roytmann).",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_fact",
            "description": "Salva ou atualiza um fato canônico (chave/valor) para a missão de Adelle.",
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
            "name": "save_intel",
            "description": "Salva uma nova peça de inteligência (intel) sobre um alvo ou evento, com um rótulo claro.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Rótulo curto e descritivo da inteligência (ex: 'pietro_encontro_secreto')."},
                    "content": {"type": "string", "description": "O texto completo da inteligência a ser salva."}
                },
                "required": ["label", "content"]
            }
        }
    }
]

def _current_user_key() -> str:
    """
    Devolve SEMPRE a mesma chave de usuário para Adelle.
    Se não tiver login, usa 'anon::adelle'.
    """
    uid = str(st.session_state.get("user_id", "") or "").strip()
    if not uid:
        return "anon::adelle"
    return f"{uid}::adelle"

# ====== MEMÓRIA TEMÁTICA (gravar e recuperar) ======
# (A lógica de gravação automática e extração de entidades será adaptada para o contexto de espionagem)

def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
    """
    Execução das ferramentas chamadas via Tool Calling.
    Retorna SEMPRE string (conteúdo que será repassado ao modelo como `tool` message).
    """
    try:
        if name == "get_mission_briefing":
            return self._build_memory_pin(usuario_key, st.session_state.get("user_id","") or "")
        
        if name == "set_fact":
            k = (args or {}).get("key", "")
            v = (args or {}).get("value", "")
            if not k: return "ERRO: chave ('key') não informada."
            set_fact(usuario_key, k, v, {"fonte": "tool_call"})
            clear_user_cache(usuario_key)
            return f"OK: Fato salvo: {k}={v}"

        if name == "save_intel":
            label = (args.get("label") or "").strip()
            content = (args.get("content") or "").strip()
            if not label or not content:
                return "ERRO: 'label' e 'content' são obrigatórios para salvar inteligência."
            
            fact_key = f"adelle.intel.{label}"
            set_fact(usuario_key, fact_key, content, {"fonte": "tool_call"})
            clear_user_cache(usuario_key)
            return f"OK: Inteligência salva em {fact_key}"

        return f"ERRO: ferramenta desconhecida: {name}"
    except Exception as e:
        return f"ERRO: {e}"

# NSFW (opcional)
try:
    from core.nsfw import nsfw_enabled
except Exception:
    def nsfw_enabled(_user: str) -> bool: return False

# Persona específica (characters/adelle/persona.py)
try:
    from .persona import get_persona
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é Adelle Roitman — A Diplomata Exilada. Fale em primeira pessoa (eu). "
            "Tom de poder, controle e sedução estratégica. 4–7 parágrafos."
        )
        return txt, []

# === Janela por modelo e orçamento (sem alterações) ===
MODEL_WINDOWS = {"anthropic/claude-3.5-haiku": 200_000, "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": 128_000, "together/Qwen/Qwen2.5-72B-Instruct": 32_000, "deepseek/deepseek-chat-v3-0324": 32_000}
DEFAULT_WINDOW = 32_000
def _get_window_for(model: str) -> int: return MODEL_WINDOWS.get((model or "").strip(), DEFAULT_WINDOW)
def _budget_slices(model: str) -> tuple[int, int, int]:
    win = _get_window_for(model)
    hist = max(8_000, int(win * 0.60)); meta = int(win * 0.20); outb = int(win * 0.20)
    return hist, meta, outb
def _safe_max_output(win: int, prompt_tokens: int) -> int:
    alvo = int(win * 0.20); sobra = max(0, win - prompt_tokens - 256)
    return max(512, min(alvo, sobra))

# =========================
# Cache leve (sem alterações)
# =========================
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))
_cache_facts: Dict[str, Dict] = {}; _cache_history: Dict[str, List[Dict]] = {}; _cache_timestamps: Dict[str, float] = {}
def _purge_expired_cache():
    now = time.time()
    for k in list(_cache_facts.keys()):
        if now - _cache_timestamps.get(f"facts_{k}", 0) >= CACHE_TTL: _cache_facts.pop(k, None); _cache_timestamps.pop(f"facts_{k}", None)
    for k in list(_cache_history.keys()):
        if now - _cache_timestamps.get(f"hist_{k}", 0) >= CACHE_TTL: _cache_history.pop(k, None); _cache_timestamps.pop(f"hist_{k}", None)
def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None); _cache_timestamps.pop(f"facts_{user_key}", None)
    _cache_history.pop(user_key, None); _cache_timestamps.pop(f"hist_{user_key}", None)
def cached_get_facts(user_key: str) -> Dict:
    _purge_expired_cache(); now = time.time()
    if user_key in _cache_facts and (now - _cache_timestamps.get(f"facts_{user_key}", 0) < CACHE_TTL): return _cache_facts[user_key]
    try: f = get_facts(user_key) or {}
    except Exception: f = {}
    _cache_facts[user_key] = f; _cache_timestamps[f"facts_{user_key}"] = now
    return f
def cached_get_history(user_key: str) -> List[Dict]:
    _purge_expired_cache(); now = time.time()
    if user_key in _cache_history and (now - _cache_timestamps.get(f"hist_{user_key}", 0) < CACHE_TTL): return _cache_history[user_key]
    try: docs = get_history_docs(user_key) or []
    except Exception: docs = []
    _cache_history[user_key] = docs; _cache_timestamps[f"hist_{user_key}"] = now
    return docs

# === Preferências do usuário (adaptado para Adelle) ===
def _read_prefs(facts: Dict) -> Dict:
    prefs = {
        "abordagem": str(facts.get("adelle.pref.abordagem", "") or "calculista").lower(), # calculista | agressiva | sedutora
        "ritmo_trama": str(facts.get("adelle.pref.ritmo_trama", "") or "moderado").lower(), # lento | moderado | rapido
        "tamanho_resposta": str(facts.get("adelle.pref.tamanho_resposta", "") or "media").lower(),
    }
    return prefs
def _prefs_line(prefs: Dict) -> str:
    return (
        f"PREFERÊNCIAS DE ABORDAGEM: abordagem={prefs.get('abordagem','calculista')}; ritmo_trama={prefs.get('ritmo_trama','moderado')}; "
        f"tamanho_resposta={prefs.get('tamanho_resposta','media')}. "
        "Use tensão psicológica e manipulação; evite romance clichê."
    )

# === Mini-sumarizadores (sem alterações) ===
def _heuristic_summarize(texto: str, max_bullets: int = 10) -> str:
    texto = re.sub(r"\s+", " ", (texto or "").strip()); sent = re.split(r"(?<=[\.\!\?])\s+", texto)
    sent = [s.strip() for s in sent if s.strip()]; return " • " + "\n • ".join(sent[:max_bullets])
def _llm_summarize(model: str, user_chunk: str) -> str:
    seed = "Resuma em 6–10 frases telegráficas, apenas fatos da missão (decisões, nomes, locais, tempo, itens, rumo da trama). Proibido diálogo literal."
    try:
        data, _, _ = route_chat_strict(model, {"model": model, "messages": [{"role": "system", "content": seed}, {"role": "user", "content": user_chunk}], "max_tokens": 220, "temperature": 0.2, "top_p": 0.9})
        txt = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
        return txt.strip() or _heuristic_summarize(user_chunk)
    except Exception: return _heuristic_summarize(user_chunk)

# ===== Blocos de system (slots) (sem alterações na estrutura) =====
def _build_system_block(persona_text: str, rolling_summary: str, sensory_focus: str, nsfw_hint: str, scene_loc: str, entities_line: str, evidence: str, prefs_line: str = "", scene_time: str = "") -> str:
    continuity = f"Cenário atual: {scene_loc or '—'}" + (f" — Momento: {scene_time}" if scene_time else "")
    sensory = f"FOCO_SENSORIAL: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{sensory_focus}**, sempre integradas à ação."
    length = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; sem listas; sem metacena."
    rules = "CONTINUIDADE: não mude tempo/lugar sem pedido explícito. Use MEMÓRIA e ENTIDADES como fonte de verdade. Se um nome/local não estiver salvo, não invente; convide o usuário a confirmar."
    safety = "LIMITES: adultos; consentimento; nada ilegal."
    evidence_block = f"EVIDÊNCIA RECENTE (falas do usuário): {evidence or '—'}"
    return "\n\n".join([persona_text, prefs_line, length, sensory, nsfw_hint, rules, f"MEMÓRIA (canon curto): {rolling_summary or '—'}", f"ENTIDADES: {entities_line or '—'}", f"CONTINUIDADE: {continuity}", evidence_block, safety])

# ===== Robustez de chamada (sem alterações) =====
def _looks_like_cloudflare_5xx(err_text: str) -> bool:
    if not err_text: return False
    s = err_text.lower(); return ("cloudflare" in s) and any(code in s for code in ["500", "502", "503", "504"])
def _robust_chat_call(model: str, messages: List[Dict[str, str]], *, max_tokens: int = 1536, temperature: float = 0.7, top_p: float = 0.95, fallback_models: List[str] | None = None, tools: List[Dict] | None = None) -> Tuple[Dict, str, str]:
    attempts = 3; last_err = ""
    for i in range(attempts):
        try:
            payload = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p}
            if tools: payload["tools"] = tools
            if st.session_state.get("json_mode_on", False): payload["response_format"] = {"type": "json_object"}
            return route_chat_strict(model, payload)
        except Exception as e:
            last_err = str(e)
            if _looks_like_cloudflare_5xx(last_err) or "OpenRouter 502" in last_err: time.sleep((0.7 * (2 ** i)) + random.uniform(0, .4)); continue
            break
    if fallback_models:
        for fb in fallback_models:
            try:
                payload_fb = {"model": fb, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "top_p": top_p}
                if tools: payload_fb["tools"] = tools
                if st.session_state.get("json_mode_on", False): payload_fb["response_format"] = {"type": "json_object"}
                return route_chat_strict(fb, payload_fb)
            except Exception as e2: last_err = str(e2)
    synthetic = {"choices": [{"message": {"content": "O provedor de IA parece instável. Por favor, reformule sua última ação em uma linha para eu continuar a cena."}}]}
    return synthetic, model, "synthetic-fallback"

# ===== Utilidades de memória/entidades (adaptado para Adelle) =====
_ENTITY_KEYS = ("alvo_principal", "local_seguro", "contato_governo", "proximo_passo_missao")
def _entities_to_line(f: Dict) -> str:
    parts = []
    for k in _ENTITY_KEYS:
        v = str(f.get(f"adelle.entity.{k}", "") or "").strip()
        if v: parts.append(f"{k}={v}")
    return "; ".join(parts) if parts else "—"

_TARGET_PAT = re.compile(r"\b(alvo|contato)\s+([A-ZÀ-Üa-zà-ü0-9][\wÀ-ÖØ-öø-ÿ'’\- ]{1,40})\b", re.I)
_LOCATION_PAT = re.compile(r"\b(local|ponto de encontro|esconderijo)\s+em\s+([^,]{1,50})\b", re.I)
def _extract_and_store_entities(usuario_key: str, user_text: str, assistant_text: str) -> None:
    """Extrai entidades da missão e persiste."""
    f = cached_get_facts(usuario_key) or {}
    text_to_scan = (user_text or "") + "\n" + (assistant_text or "")
    
    m_target = _TARGET_PAT.search(text_to_scan)
    if m_target:
        name = re.sub(r"\s+", " ", m_target.group(2)).strip()
        cur = str(f.get("adelle.entity.alvo_principal", "") or "").strip()
        if not cur: set_fact(usuario_key, "adelle.entity.alvo_principal", name, {"fonte": "extracted"}); clear_user_cache(usuario_key)

    m_loc = _LOCATION_PAT.search(text_to_scan)
    if m_loc:
        loc = m_loc.group(2).strip()
        cur = str(f.get("adelle.entity.local_seguro", "") or "").strip()
        if not cur: set_fact(usuario_key, "adelle.entity.local_seguro", loc, {"fonte": "extracted"}); clear_user_cache(usuario_key)

# ===== Aviso de memória (sem alterações) =====
def _mem_drop_warn(report: dict) -> None:
    if not report: return
    summarized = int(report.get("summarized_pairs", 0)); trimmed = int(report.get("trimmed_pairs", 0))
    if summarized or trimmed:
        msg = []
        if summarized: msg.append(f"**{summarized}** turnos antigos **foram resumidos**")
        if trimmed: msg.append(f"**{trimmed}** turnos verbatim **foram podados**")
        st.info(f"⚠️ Memória ajustada: {' e '.join(msg)}. Se notar esquecimentos, peça um **‘recap da missão’**.", icon="⚠️")

class AdelleService(BaseCharacter):
    id: str = "adelle"
    display_name: str = "Adelle"

    def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
        return _exec_tool_call(self, name, args, usuario_key)

    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt: return ""

        usuario_key = _current_user_key()
        persona_text, history_boot = self._load_persona()

        f_all = cached_get_facts(usuario_key) or {}
        prefs = _read_prefs(f_all)
        local_atual = self._safe_get_local(usuario_key)
        
        # Foco sensorial rotativo (adaptado para Adelle)
        pool = ["olhar (desafio)", "postura (poder)", "voz (controle)", "toque (teste)", "respiração (tensão)", "silêncio (pressão)"]
        idx = (int(st.session_state.get("adelle_attr_idx", -1)) + 1) % len(pool)
        st.session_state["adelle_attr_idx"] = idx
        foco = pool[idx]

        # NSFW Hint (adaptado para Adelle)
        nsfw_on = bool(nsfw_enabled(usuario_key))
        abordagem = prefs.get("abordagem", "calculista")
        if not nsfw_on:
            nsfw_hint = "NSFW: BLOQUEADO. Foco em tensão psicológica, duplo sentido e subtexto. A sedução é uma arma, não um fim."
        else:
            if abordagem == "agressiva":
                nsfw_hint = "NSFW: LIBERADO. Abordagem direta e dominante. Use linguagem imperativa e descrições de poder. O sexo é uma conquista."
            elif abordagem == "sedutora":
                nsfw_hint = "NSFW: LIBERADO. Foco em sedução e manipulação. Descreva o ato como um jogo de gato e rato, cheio de provocações."
            else: # calculista
                nsfw_hint = "NSFW: LIBERADO. Abordagem fria e estratégica. O sexo é uma ferramenta para extrair informação ou ganhar controle. Descreva os pensamentos analíticos de Adelle durante o ato."

        # Sumário, Entidades, Evidência
        rolling = self._get_rolling_summary(usuario_key)
        entities_line = _entities_to_line(f_all)
        docs = cached_get_history(usuario_key) or []
        evidence = self._compact_user_evidence(docs, max_chars=320)

        system_block = _build_system_block(
            persona_text=persona_text, rolling_summary=rolling, sensory_focus=foco, nsfw_hint=nsfw_hint,
            scene_loc=local_atual or "—", entities_line=entities_line, evidence=evidence, prefs_line=_prefs_line(prefs),
            scene_time=st.session_state.get("momento_atual", "")
        )

        # LORE (memória longa)
        lore_msgs: List[Dict[str, str]] = []
        try:
            q = (prompt or "") + "\n" + (rolling or "")
            top = lore_topk(usuario_key, q, k=4, allow_tags=["adelle", "mission"])
            if top:
                lore_text = " | ".join(d.get("texto", "") for d in top if d.get("texto"))
                if lore_text: lore_msgs.append({"role": "system", "content": f"[LORE]\n{lore_text}"})
        except Exception: pass

        # Histórico com orçamento
        verbatim_ultimos = int(st.session_state.get("verbatim_ultimos", 10))
        hist_msgs = self._montar_historico(usuario_key, history_boot, model, verbatim_ultimos=verbatim_ultimos)

        # Inteligência salva (adelle.intel.*)
        intel_block = ""
        try:
            intels = []
            for k, v in (f_all or {}).items():
                if k.startswith("adelle.intel.") and v:
                    label = k.split("adelle.intel.", 1)[-1]
                    intels.append(f"- {label}: {str(v).strip()}")
            if intels:
                joined = "\n".join(intels)[:1000]
                intel_block = "INTELIGÊNCIA COLETADA (Fonte de Verdade):\n" + joined
        except Exception: intel_block = ""

        messages: List[Dict,] = (
            [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": self._build_memory_pin(usuario_key, user)}] if self._build_memory_pin(usuario_key, user) else [])
            + ([{"role": "system", "content": intel_block}] if intel_block else [])
            + lore_msgs
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))

        # Orçamento de saída e temperatura
        win = _get_window_for(model); prompt_tokens = sum(toklen(m["content"]) for m in messages)
        base_out = _safe_max_output(win, prompt_tokens)
        size = prefs.get("tamanho_resposta", "media"); mult = 1.0 if size == "media" else (0.75 if size == "curta" else 1.25)
        max_out = max(512, int(base_out * mult))
        ritmo = prefs.get("ritmo_trama", "moderado"); temperature = 0.6 if ritmo == "lento" else (0.9 if ritmo == "rapido" else 0.7)

        # Loop de Tool Calling
        max_iterations = 3; iteration = 0; texto = ""; tool_calls = []
        tools_to_use = TOOLS if st.session_state.get("tool_calling_on", False) else None

        while iteration < max_iterations:
            iteration += 1
            data, used_model, provider = _robust_chat_call(model, messages, max_tokens=max_out, temperature=temperature, top_p=0.95, fallback_models=["together/Qwen/Qwen2.5-72B-Instruct"], tools=tools_to_use)
            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls: break
            
            st.caption(f"🔧 Executando {len(tool_calls)} ferramenta(s)...")
            messages.append({"role": "assistant", "content": texto or None, "tool_calls": tool_calls})
            
            for tc in tool_calls:
                tool_id = tc.get("id", f"call_{iteration}"); func_name = tc.get("function", {}).get("name", ""); func_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}
                    result = self._exec_tool_call(func_name, func_args, usuario_key)
                    messages.append({"role": "tool", "tool_call_id": tool_id, "content": result})
                    st.caption(f"  ✓ {func_name}: {result[:50]}...")
                except Exception as e:
                    error_msg = f"ERRO ao executar {func_name}: {str(e)}"
                    messages.append({"role": "tool", "tool_call_id": tool_id, "content": error_msg})
                    st.warning(f"⚠️ {error_msg}")

        # Ultra IA (opcional)
        if bool(st.session_state.get("ultra_ia_on", False)) and texto:
            try:
                critic_model = st.session_state.get("ultra_critic_model", model) or model
                notes = critic_review(critic_model, system_block, prompt, texto)
                texto = polish(model, system_block, prompt, texto, notes)
            except Exception: pass

        # Persistência
        save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        _extract_and_store_entities(usuario_key, prompt, texto)
        self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
        try: lore_save(usuario_key, f"[USER] {prompt}\n[ADELLE] {texto}", tags=["adelle", "mission"])
        except Exception: pass
        
        st.session_state["suggestion_placeholder"] = self._suggest_placeholder(texto, local_atual)
        st.session_state["last_assistant_message"] = texto
        return texto

    # ===== utilidades (adaptadas para Adelle) =====
    def _load_persona(self) -> Tuple[str, List[Dict[str, str]]]: return get_persona()
    def _get_user_prompt(self) -> str: return (st.session_state.get("chat_input") or "").strip()
    def _safe_get_local(self, usuario_key: str) -> str:
        try: return get_fact(usuario_key, "local_cena_atual", "") or ""
        except Exception: return ""

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """Memória canônica da missão."""
        f = cached_get_facts(usuario_key) or {}
        blocos: List[str] = []
        
        nome_agente = (f.get("nome_agente") or user_display or "Orion").strip()
        blocos.append(f"agente_infiltrado={nome_agente}")
        
        missao = f.get("adelle.missao.objetivo", "Destruir a família Roytmann")
        blocos.append(f"objetivo_missao='{missao}'")

        alvos = f.get("adelle.missao.alvos", "Florêncio, Heitor, Pietro, Neuza")
        blocos.append(f"alvos_principais='{alvos}'")

        ponto_fraco = f.get("adelle.missao.ponto_fraco", "Sophia Roytmann (filha ingênua)")
        blocos.append(f"ponto_fraco='{ponto_fraco}'")

        ent_line = _entities_to_line(f)
        if ent_line and ent_line != "—": blocos.append(f"entidades=({ent_line})")

        mem_str = "; ".join(blocos) if blocos else "—"
        pin = f"BRIEFING_MISSÃO: FATOS={{ {mem_str} }}. Regra: a missão é prioridade. Desconfiança é o padrão."
        return pin

    def _montar_historico(self, usuario_key: str, history_boot: List[Dict[str, str]], model: str, verbatim_ultimos: int = 10) -> List[Dict[str, str]]:
        # (A lógica original de _montar_historico é mantida, pois é genérica)
        hist_budget, _, _ = _budget_slices(model)
        docs = cached_get_history(usuario_key)
        if not docs: st.session_state["_mem_drop_report"] = {}; return history_boot[:]
        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip(); a = (d.get("resposta_adelle") or d.get("resposta_mary") or "").strip() # compatibilidade
            if u: pares.append({"role": "user", "content": u})
            if a: pares.append({"role": "assistant", "content": a})
        if not pares: st.session_state["_mem_drop_report"] = {}; return history_boot[:]
        keep = max(0, verbatim_ultimos * 2); verbatim = pares[-keep:] if keep else []; antigos = pares[:-len(verbatim)]
        msgs: List[Dict[str, str]] = []; summarized_pairs = 0; trimmed_pairs = 0
        if antigos:
            summarized_pairs = len(antigos) // 2; bloco = "\n\n".join(m["content"] for m in antigos); resumo = _llm_summarize(model, bloco)
            msgs.append({"role": "system", "content": f"[RESUMO-1]\n{resumo}"})
        msgs.extend(verbatim)
        def _hist_tokens(mm: List[Dict,]) -> int: return sum(toklen(m["content"]) for m in mm)
        while _hist_tokens(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2: verbatim = verbatim[2:]; trimmed_pairs += 1
            else: verbatim = []
            msgs = [m for m in msgs if m["role"] == "system"] + verbatim
        st.session_state["_mem_drop_report"] = {"summarized_pairs
