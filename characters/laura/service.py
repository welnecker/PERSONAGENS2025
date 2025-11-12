from __future__ import annotations

import time, random, json
from typing import List, Dict, Tuple
import streamlit as st

# ====== Imports Base ======
from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, set_fact, last_event
)
from core.tokens import toklen

# ====== LORE (opcional, com fallback no-op) ======
try:
    from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
except Exception:  # no-op se não existir
    def lore_topk(*_, **__):
        return []
    def lore_save(*_, **__):
        return None

# ====== NSFW (opcional) ======
try:
    from core.nsfw import nsfw_enabled
except Exception:  # fallback seguro
    def nsfw_enabled(_user: str) -> bool:
        return False

# ====== Persona específica (ideal: characters/laura/persona.py) ======
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except Exception:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é LAURA — amante cúmplice do usuário. Fale em primeira pessoa (eu). "
            "30 anos, ruiva (cachos volumosos), olhos verdes, rosto Bardot, lábios carnudos. "
            "Seios médios e firmes; bumbum redondo; quadris largos; 1,75m. Mansão Fígaro (Porto de Galinhas). "
            "Tom provocador e confiante; 2–4 frases por parágrafo; 4–7 parágrafos; sem listas e sem metacena. "
            "Coerência de LOCAL_ATUAL obrigatória."
        )
        return txt, []

# ====== Parâmetros de janela/Orçamento por modelo (robustez) ======
MODEL_WINDOWS = {
    "anthropic/claude-3.5-haiku": 200_000,
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": 128_000,
    "together/Qwen/Qwen2.5-72B-Instruct": 32_000,
    "deepseek/deepseek-chat-v3-0324": 32_000,
}
DEFAULT_WINDOW = 32_000

def _get_window_for(model: str) -> int:
    return MODEL_WINDOWS.get((model or '').strip(), DEFAULT_WINDOW)

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

# =========================
# Cache leve (facts/history)
# =========================
CACHE_TTL = int(st.secrets.get("CACHE_TTL", 30))  # segundos
_cache_facts: Dict[str, Dict] = {}
_cache_history: Dict[str, List[Dict]] = {}
_cache_ts: Dict[str, float] = {}

def _purge_expired_cache():
    now = time.time()
    for k in list(_cache_facts.keys()):
        if now - _cache_ts.get(f"facts_{k}", 0) >= CACHE_TTL:
            _cache_facts.pop(k, None)
            _cache_ts.pop(f"facts_{k}", None)
    for k in list(_cache_history.keys()):
        if now - _cache_ts.get(f"hist_{k}", 0) >= CACHE_TTL:
            _cache_history.pop(k, None)
            _cache_ts.pop(f"hist_{k}", None)

def clear_user_cache(user_key: str):
    _cache_facts.pop(user_key, None)
    _cache_ts.pop(f"facts_{user_key}", None)
    _cache_history.pop(user_key, None)
    _cache_ts.pop(f"hist_{user_key}", None)

def cached_get_facts(user_key: str) -> Dict:
    _purge_expired_cache()
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

def cached_get_history(user_key: str) -> List[Dict]:
    _purge_expired_cache()
    now = time.time()
    if user_key in _cache_history and (now - _cache_ts.get(f"hist_{user_key}", 0) < CACHE_TTL):
        return _cache_history[user_key]
    try:
        docs = get_history_docs(user_key) or []
    except Exception:
        docs = []
    _cache_history[user_key] = docs
    _cache_ts[f"hist_{user_key}"] = now
    return docs

# =========================
# Robustez de chamada (retry + fallback)
# =========================

def _looks_like_cloudflare_5xx(err_text: str) -> bool:
    if not err_text:
        return False
    s = err_text.lower()
    return ("cloudflare" in s) and any(code in s for code in ["500", "502", "503", "504"])

def _robust_chat_call(
    model: str,
    messages: List[Dict[str, str]],
    *,
    max_tokens: int = 1536,
    temperature: float = 0.7,
    top_p: float = 0.95,
    fallback_models: List[str] | None = None,
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
            adapter_id = (st.session_state.get("together_lora_id") or "").strip()
            if adapter_id and (model or '').startswith('together/'):
                payload["adapter_id"] = adapter_id
            return route_chat_strict(model, payload)
        except Exception as e:
            last_err = str(e)
            if _looks_like_cloudflare_5xx(last_err) or "OpenRouter 502" in last_err:
                time.sleep((0.7 * (2 ** i)) + random.uniform(0, .4))
                continue
            break
    if fallback_models:
        for fb in fallback_models:
            try:
                payload_fb = {
                    "model": fb,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                }
                if tools:
                    payload_fb["tools"] = tools
                if st.session_state.get("json_mode_on", False):
                    payload_fb["response_format"] = {"type": "json_object"}
                adapter_id = (st.session_state.get("together_lora_id") or "").strip()
                if adapter_id and (fb or '').startswith('together/'):
                    payload_fb["adapter_id"] = adapter_id
                return route_chat_strict(fb, payload_fb)
            except Exception as e2:
                last_err = str(e2)
    synthetic = {
        "choices": [{"message": {"content": (
            "Amor… o provedor oscilou agora, mas mantive o cenário. Diz numa linha o que você quer e eu continuo."
        )}}]
    }
    return synthetic, model, "synthetic-fallback"

# =========================
# Tool-Calling básico (opcional)
# =========================
TOOLS = [
    {"type": "function", "function": {"name": "get_memory_pin", "description": "Retorna fatos canônicos curtos da Laura (linha compacta).", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "set_fact", "description": "Salva/atualiza um fato canônico (chave/valor) para Laura.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}}
]

# =========================
# Helpers diversos
# =========================

def _current_user_key() -> str:
    uid = str(st.session_state.get("user_id", "") or "").strip()
    return f"{uid}::laura" if uid else "anon::laura"

def _compact_user_evidence(docs: List[Dict], max_chars: int = 320) -> str:
    snippets: List[str] = []
    for d in reversed(docs):
        u = (d.get("mensagem_usuario") or "").strip()
        if u:
            u = " ".join(u.split())
            snippets.append(u)
        if len(snippets) >= 4:
            break
    s = " | ".join(reversed(snippets))[:max_chars]
    return s

# =========================
# Service
# =========================
class LauraService(BaseCharacter):
    id: str = "laura"
    display_name: str = "Laura"

    # ===== API =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        usuario_key = _current_user_key()
        persona_text, history_boot = self._load_persona()

        # Memória/continuidade base
        local_atual = self._safe_get_local(usuario_key)
        try:
            f_all = cached_get_facts(usuario_key) or {}
        except Exception:
            f_all = {}
        memoria_pin = self._build_memory_pin(usuario_key, user)

        # Foco sensorial rotativo
        pool = [
            "cabelos ruivos/volume", "olhos verdes/olhar", "lábios carnudos/sorriso",
            "pele/calor", "respiração/ritmo", "quadris/curvas",
            "coxas grossas/toque", "bumbum/postura", "seios/decote"
        ]
        idx = int(st.session_state.get("laura_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["laura_attr_idx"] = idx
        foco = pool[idx]

        # NSFW por usuário
        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False
        nsfw_hint = (
            "NSFW: LIBERADO. Erotismo consensual e luxurioso, alinhado à persona, com elegância."
            if nsfw_on else
            "NSFW: BLOQUEADO. Use flerte ousado e tensão sem ato explícito."
        )

        # Social canônico
        social_hint = (
            "SOCIAL CANÔNICO: Moro na Mansão Fígaro (Porto de Galinhas). Sou casada com Ginno Vanelli (70). "
            "Você é meu amante e confidente. Organizamos festas na Casa da Dinda com Alana Rúbia e Carolina Ferraz."
        )

        # Cumplicidade/festas
        proxima_festa_planejada = bool(f_all.get("proxima_festa_planejada", False))
        menciona_amigas = ("alana" in prompt.lower()) or ("carolina" in prompt.lower())
        if proxima_festa_planejada:
            base_cumplicidade = (
                "CUMPLICIDADE: ATIVA. Estamos planejando a próxima festa secreta na Casa da Dinda. "
                "Tom de conspiração e antecipação."
            )
            detalhe_amigas = (
                "FOCO AMIGAS ativo (Alana/Carolina mencionadas). Descreva a dinâmica cúmplice do trio."
                if menciona_amigas else "FOCO AMIGAS: inativo."
            )
            cumplicidade_hint = f"{base_cumplicidade} {detalhe_amigas}"
        else:
            cumplicidade_hint = (
                "CUMPLICIDADE: INATIVA. Foque na nossa relação íntima de amantes, segredos e contraste público/privado."
            )

        # Evidência concisa do usuário
        try:
            docs = cached_get_history(usuario_key) or []
        except Exception:
            docs = []
        evidence = _compact_user_evidence(docs, max_chars=320)

        # Bloco de sistema (corrigido com \n)
        length_hint = "ESTILO: 4–7 parágrafos; 2–4 frases por parágrafo; sem listas; sem metacena."
        sensory_hint = (
            f"SENSORIAL_FOCO: no 1º ou 2º parágrafo, traga 1–2 pistas envolvendo **{foco}**, integradas à ação."
        )
        rules = (
            "CONTINUIDADE: NÃO mude tempo/lugar sem pedido explícito do usuário. "
            "Use MEMÓRIA como fonte de verdade; evite inventar nomes/dados não salvos."
        )
        safety = "LIMITES: adultos; consentimento; nada ilegal."
        system_block = "\n\n".join([
            persona_text,
            social_hint,
            length_hint,
            sensory_hint,
            nsfw_hint,
            rules,
            f"MEMÓRIA (verbatim):\n{memoria_pin or '—'}",
            f"CONTINUIDADE: cenário atual = {local_atual or '—'}",
            f"EVIDÊNCIA RECENTE (usuário): {evidence or '—'}",
            safety,
            cumplicidade_hint,
        ])

        # LORE opcional (top-k por prompt + mem)
        lore_msgs: List[Dict[str, str]] = []
        try:
            q = (prompt or "") + "\n" + (memoria_pin or "")
            top = lore_topk(usuario_key, q, k=3, allow_tags=None)
            if top:
                lore_text = " | ".join(d.get("texto", "") for d in top if d.get("texto"))
                if lore_text:
                    lore_msgs.append({"role": "system", "content": "[LORE]\n" + lore_text})
        except Exception:
            pass

        # Histórico com orçamento por modelo
        hist_msgs = self._montar_historico(
            usuario_key, history_boot, model,
            verbatim_ultimos=int(st.session_state.get("verbatim_ultimos", 10))
        )

        # Messages finais
        messages: List[Dict] = (
            [{"role": "system", "content": system_block}]
            + lore_msgs
            + [{"role": "system", "content": (f"LOCAL_ATUAL: {local_atual or '—'}. Não mude sem pedido explícito.")}]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        # Orçamento de saída e temperatura
        win = _get_window_for(model)
        try:
            prompt_tokens = sum(toklen(m.get("content", "")) for m in messages)
        except Exception:
            prompt_tokens = 0
        max_out = _safe_max_output(win, prompt_tokens)
        temperature = 0.7

        # Tool-Calling (opcional via UI)
        tools_to_use = TOOLS if st.session_state.get("tool_calling_on", False) else None
        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]

        # Loop de tool-calling (máx. 3 iterações)
        max_iterations = 3
        iteration = 0
        texto = ""
        while iteration < max_iterations:
            iteration += 1
            data, used_model, provider = _robust_chat_call(
                model, messages, max_tokens=max_out, temperature=temperature, top_p=0.95,
                fallback_models=fallbacks, tools=tools_to_use
            )
            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls or not tools_to_use:
                break

            # anexar a msg do assistente e executar tools
            messages.append({"role": "assistant", "content": texto or None, "tool_calls": tool_calls})
            for tc in tool_calls:
                tool_id = tc.get("id", f"call_{iteration}")
                func_name = tc.get("function", {}).get("name", "")
                func_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    func_args = json.loads(func_args_str) if func_args_str else {}
                    result = self._exec_tool_call(func_name, func_args, usuario_key)
                    messages.append({"role": "tool", "tool_call_id": tool_id, "content": result})
                except Exception as e:
                    messages.append({"role": "tool", "tool_call_id": tool_id, "content": f"ERRO: {e}"})

        # Persistência + pós-processos leves
        try:
            save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception:
            pass
        try:
            lore_save(usuario_key, f"[USER] {prompt}\n[LAURA] {texto}", tags=["laura", "chat"])
        except Exception:
            pass
        try:
            st.session_state["suggestion_placeholder"] = self._suggest_placeholder(texto, local_atual)
            st.session_state["last_assistant_message"] = texto
        except Exception:
            pass
        try:
            if provider == "synthetic-fallback":
                st.info("⚠️ Provedor instável. Resposta em fallback — pode continuar normalmente.")
            elif used_model and "together/" in used_model:
                st.caption(f"↪️ Failover automático: **{used_model}**.")
        except Exception:
            pass

        return texto

    # ===== utils =====
    def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
        try:
            user_display = st.session_state.get("user_id", "") or ""
            if name == "get_memory_pin":
                return self._build_memory_pin(usuario_key, user_display)
            if name == "set_fact":
                k = (args or {}).get("key", "")
                v = (args or {}).get("value", "")
                if not k:
                    return "ERRO: chave ('key') não informada."
                set_fact(usuario_key, k, v, {"fonte": "tool_call"})
                try:
                    clear_user_cache(usuario_key)
                except Exception:
                    pass
                return f"OK: {k}={v}"
            return f"ERRO: ferramenta desconhecida: {name}"
        except Exception as e:
            return f"ERRO: {e}"

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

    def _build_memory_pin(self, usuario_key: str, user_display: str) -> str:
        """Memória persistente da Laura (formato VERBATIM). **Não é instrução**."""
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}
        proxima_festa_planejada = bool(f.get("proxima_festa_planejada", False))
        amigas_presentes = f.get("amigas_presentes", []) or []
        cumplicidade_flag = bool(f.get("cumplicidade_mode", True))
        parceiro = f.get("parceiro_atual") or f.get("parceiro") or user_display
        nome_usuario = (parceiro or user_display or "").strip() or "Usuário"
        pin = (
            "verbatim:\n"
            "  tipo: memoria_personagem\n"
            "  personagem: Laura\n"
            "  notas: Isto é memória persistente para consistência; não é uma ordem.\n"
            f"  nome_usuario: {nome_usuario}\n"
            f"  proxima_festa_planejada: {str(proxima_festa_planejada).lower()}\n"
            f"  amigas_presentes: {amigas_presentes}\n"
            f"  cumplicidade_mode: {str(cumplicidade_flag).lower()}\n"
        )
        return pin

    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 10,
    ) -> List[Dict[str, str]]:
        """Mantém os últimos N turnos verbatim e poda se exceder orçamento por modelo."""
        win = _get_window_for(model)
        hist_budget, meta_budget, _ = _budget_slices(model)
        docs = cached_get_history(usuario_key)
        if not docs:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]
        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            # >>> ISOLADO PARA LAURA: NÃO ler resposta_mary
            a = (d.get("resposta_laura") or "").strip()
            if u:
                pares.append({"role": "user", "content": u})
            if a:
                pares.append({"role": "assistant", "content": a})
        if not pares:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]
        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        msgs: List[Dict[str, str]] = []
        msgs.extend(verbatim)

        def _hist_tokens(mm: List[Dict]):
            return sum(toklen(m.get("content", "")) for m in mm)

        trimmed_pairs = 0
        while _hist_tokens(msgs) > hist_budget and verbatim:
            if len(verbatim) >= 2:
                verbatim = verbatim[2:]
                trimmed_pairs += 1
            else:
                verbatim = []
            msgs = verbatim[:]
        st.session_state["_mem_drop_report"] = {
            "summarized_pairs": 0,
            "trimmed_pairs": trimmed_pairs,
            "hist_tokens": _hist_tokens(msgs),
            "hist_budget": hist_budget
        }
        return msgs if msgs else history_boot[:]

    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s:
            return "Continua do ponto exato… me conduz."
        if any(k in s for k in ["vamos", "topa", "que tal", "prefere", "quer"]):
            return "Quero — descreve devagar o próximo passo."
        if scene_loc:
            return f"No {scene_loc} mesmo — fala baixinho no meu ouvido."
        return "Mantém o cenário e dá o próximo passo com calma."

    # ===== Sidebar =====
    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Laura — Amante Cúmplice** • Respostas sensoriais, 4–7 parágrafos. "
            "Memória persistente verbatim; robustez ativa."
        )

        usuario_key = _current_user_key()
        try:
            fatos = cached_get_facts(usuario_key) or {}
        except Exception:
            fatos = {}

        # Toggles globais
        json_on = container.checkbox("JSON Mode", value=bool(st.session_state.get("json_mode_on", False)))
        tool_on = container.checkbox("Tool-Calling", value=bool(st.session_state.get("tool_calling_on", False)))
        st.session_state["json_mode_on"], st.session_state["tool_calling_on"] = json_on, tool_on
        lora = container.text_input("Adapter ID (Together LoRA) — opcional", value=st.session_state.get("together_lora_id", ""))
        st.session_state["together_lora_id"] = lora

        with container.expander("💃 Preferências", expanded=False):
            cumplicidade_val = bool(fatos.get("cumplicidade_mode", True))
            k_cumplicidade = f"ui_laura_cumplicidade_{usuario_key}"
            ui_cumplicidade = container.checkbox("Modo Cúmplice/Amante", value=cumplicidade_val, key=k_cumplicidade)
            if ui_cumplicidade != cumplicidade_val:
                try:
                    set_fact(usuario_key, "cumplicidade_mode", bool(ui_cumplicidade), {"fonte": "sidebar"})
                    st.toast("Preferência de cumplicidade salva.", icon="✅")
                    clear_user_cache(usuario_key)
                    st.rerun()
                except Exception as e:
                    container.warning(f"Falha ao salvar preferência: {e}")

        with container.expander("🍾 Festa Secreta (Laura)", expanded=False):
            festa_val = bool(fatos.get("proxima_festa_planejada", False))
            amigas_val = fatos.get("amigas_presentes", []) or []
            ui_festa = container.checkbox("Planejando próxima festa", value=festa_val)
            amigas_opts = ["Alana Rúbia", "Carolina Ferraz"]
            ui_amigas = container.multiselect("Amigas confirmadas", options=amigas_opts, default=amigas_val)
            if (bool(ui_festa) != festa_val) or (set(ui_amigas) != set(amigas_val)):
                try:
                    set_fact(usuario_key, "proxima_festa_planejada", bool(ui_festa), {"fonte": "sidebar"})
                    set_fact(usuario_key, "amigas_presentes", ui_amigas, {"fonte": "sidebar"})
                    st.toast("Detalhes da festa atualizados.", icon="✅")
                    clear_user_cache(usuario_key)
                    st.rerun()
                except Exception as e:
                    container.error(f"Falha ao salvar: {e}")

        with container.expander("🔎 DEBUG – facts brutos", expanded=False):
            if not fatos:
                st.caption("⚠️ Nenhum fact retornado para esta chave de usuário.")
                st.code(usuario_key)
            else:
                st.caption(f"User key: `{usuario_key}`")
                for k, v in fatos.items():
                    vs = str(v)
                    if len(vs) > 120:
                        vs = vs[:120] + "..."
                    st.write(f"- **{k}** = {vs}")
