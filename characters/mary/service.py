from __future__ import annotations

import re
import time
import random
from typing import List, Dict, Tuple
import streamlit as st

from core.memoria_longa import topk as lore_topk, save_fragment as lore_save
from core.ultra import critic_review, polish
from core.common.base_service import BaseCharacter
from core.service_router import route_chat_strict, list_models, available_providers
from core.repositories import (
    save_interaction, get_history_docs,
    get_facts, get_fact, last_event, set_fact
)
from core.tokens import toklen
import json

# === Tool Calling: definição de ferramentas disponíveis para a Mary ===
# Você pode ampliar livremente esta lista conforme precisar.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_memory_pin",
            "description": "Retorna um resumo curto dos fatos canônicos...",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_fact",
            "description": "Define/atualiza um fact simples na memória do usuário...",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_event",
            "description": (
                "Salva um evento narrativo importante da Mary na memória fixa "
                "(por exemplo, gravidez confirmada, viagem marcante etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": (
                            "Identificador curto para o evento, "
                            "ex.: 'gravidez_confirmada_2025-11-28'."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Texto completo do evento que deve ser preservado "
                            "como memória canônica."
                        ),
                    },
                },
                "required": ["content"],
            },
        },
    },
]


# ==============================================
# 1) Helpers de cache/infra bem genéricos
# ==============================================
def _current_user_key() -> str:
    """
    Tenta derivar uma chave interna de usuário.
    Se você tiver login, pode usar o e-mail, id do banco, etc.
    """
    u = st.session_state.get("user_id") or st.session_state.get("usuario") or "anon"
    u = str(u).strip()
    return f"user::{u}"


def cached_get_facts(usuario_key: str) -> Dict[str, any]:
    cache_key = f"facts::{usuario_key}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    try:
        f = get_facts(usuario_key) or {}
    except Exception:
        f = {}
    st.session_state[cache_key] = f
    return f


def clear_user_cache(usuario_key: str):
    """
    Limpa cache leve para forçar reload de facts/histórico após alterações.
    """
    fk = f"facts::{usuario_key}"
    if fk in st.session_state:
        del st.session_state[fk]

    hk = f"history::{usuario_key}"
    if hk in st.session_state:
        del st.session_state[hk]


def cached_get_history(usuario_key: str):
    hk = f"history::{usuario_key}"
    if hk in st.session_state:
        return st.session_state[hk]
    try:
        docs = get_history_docs(usuario_key)
    except Exception:
        docs = []
    st.session_state[hk] = docs
    return docs


# ==============================================
# 2) Preferências do usuário (sexo, ritmo, etc.)
# ==============================================
def _read_prefs(facts: Dict[str, any]) -> Dict[str, str]:
    """
    Lê as preferências salvas para Mary.
    Exemplo de fato salvo:
      - "mary.pref.nivel_sensual" = "sutil" | "media" | "alta"
      - "mary.pref.ritmo"         = "lento" | "normal" | "rapido"
      - "mary.pref.tamanho"       = "curta" | "media" | "longa"
    """
    nivel = facts.get("mary.pref.nivel_sensual", "sutil") or "sutil"
    ritmo = facts.get("mary.pref.ritmo", "normal") or "normal"
    tamanho = facts.get("mary.pref.tamanho", "media") or "media"
    return {
        "nivel_sensual": str(nivel),
        "ritmo": str(ritmo),
        "tamanho_resposta": str(tamanho),
    }


def _prefs_line(prefs: Dict[str, str]) -> str:
    return (
        f"nivel_sensual={prefs.get('nivel_sensual')}; "
        f"ritmo={prefs.get('ritmo')}; "
        f"tamanho_resposta={prefs.get('tamanho_resposta')}"
    )


def nsfw_enabled(usuario_key: str) -> bool:
    try:
        v = get_fact(usuario_key, "nsfw_on", False)
    except Exception:
        v = False
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    return s in ("1", "true", "sim", "on", "yes", "y")


# ==============================================
# 3) Tamanho de contexto por modelo (janela)
# ==============================================
# Janela padrão se não conhecer o modelo:
_DEFAULT_WINDOW = 16000


def _get_window_for(model_id: str) -> int:
    if not model_id:
        return _DEFAULT_WINDOW

    m = model_id.lower().strip()
    # Exemplos: você pode adaptar conforme seus modelos
    if "deepseek-r1" in m or "deepseek-reasoner" in m:
        return 128000
    if "deepseek-chat" in m:
        return 65536
    if "gpt-4.1" in m or "gpt-4.5" in m:
        return 128000
    if "llama-3.1-405b" in m or "llama-3.1" in m:
        return 128000
    if "qwen2.5-72b" in m:
        return 32000
    if "claude-3.5" in m:
        return 200000
    if "grok-4.1" in m:
        return 200000
    if "tng-r1t-chimera" in m:
        return 163840

    # fallback
    return _DEFAULT_WINDOW


# ==============================================
# 4) Orçamento por modelo (histórico x meta)
# ==============================================
def _budget_slices(model_id: str) -> Tuple[int, int, int]:
    """
    Retorna (hist_budget, meta_budget, safety_budget) em tokens.
    hist_budget: histórico/resumos
    meta_budget: system/persona/memória
    safety_budget: folga para resposta
    """
    win = _get_window_for(model_id)
    # regra simples: 50% histórico, 20% meta, 30% folga
    hist = int(win * 0.5)
    meta = int(win * 0.2)
    safety = win - hist - meta
    return hist, meta, safety


# ==============================================
# 5) Summarizer auxiliar para históricos longos
# ==============================================
def _llm_summarize(model_id: str, text: str) -> str:
    """
    Usa o mesmo roteador de LLMs para gerar um resumo curto.
    """
    if not text.strip():
        return ""

    # fallback de modelo para resumo (algo barato/leve)
    candidates = [
        "together/Qwen/Qwen2.5-32B-Instruct",
        "together/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "deepseek/deepseek-chat-v3-0324",
    ]
    use_model = model_id or candidates[0]
    mlow = (use_model or "").lower()
    if "grok-4.1" in mlow or "tng-r1t-chimera" in mlow:
        for c in candidates:
            if c in list_models():
                use_model = c
                break
        else:
            # se nenhum estiver em list_models, usa o primeiro mesmo
            use_model = candidates[0]

    seed = (
        "Resuma o seguinte histórico de diálogo entre Mary e o usuário em 8–12 frases curtas, "
        "focando apenas em fatos e decisões duráveis (relação, gravidez, locais, acordos, conflitos, segredos). "
        "Não repita diálogos literais; não invente fatos novos. "
        "Use português natural e mantenha coerência temporal."
    )
    body = {
        "model": use_model,
        "messages": [
            {"role": "system", "content": seed},
            {"role": "user", "content": text},
        ],
        "max_tokens": 260,
        "temperature": 0.2,
        "top_p": 0.9,
    }
    try:
        data, used, prov = route_chat_strict(use_model, body)
        msg = (data.get("choices", [{}])[0].get("message", {}) or {})
        return (msg.get("content") or "").strip()
    except Exception:
        return ""


# ==============================================
# 6) ENTIDADES (nomes, datas, etc.)
# ==============================================
def _entities_to_line(f: Dict[str, any]) -> str:
    """
    Converte fatos do tipo "mary.ent.*" em uma linha compacta.
    Exemplo de fato:
      - mary.ent.parceiro_nome = "Janio"
      - mary.ent.amante_carlos = "Carlos"
    """
    ents = []
    for k, v in f.items():
        if not isinstance(k, str):
            continue
        if not k.startswith("mary.ent."):
            continue
        label = k.replace("mary.ent.", "", 1)
        vs = str(v).strip()
        if not vs:
            continue
        ents.append(f"{label}={vs}")
    if not ents:
        return "—"
    return "; ".join(sorted(ents))


def _extract_and_store_entities(usuario_key: str, user_text: str, assistant_text: str) -> None:
    """
    Extrator super simples de entidades, só para exemplo.
    Você pode evoluir com NER real depois.
    """
    try:
        txt = f"{user_text}\n{assistant_text}"
        txt_low = txt.lower()
        # Exemplos bobos só para ilustrar
        nomes = []
        for nome in ["carlos", "beatriz", "laura", "ricardo", "janio", "mary"]:
            if nome in txt_low:
                nomes.append(nome)
        for nome in nomes:
            k = f"mary.ent.{nome}"
            set_fact(usuario_key, k, nome.capitalize(), {"fonte": "auto_entidade"})
        clear_user_cache(usuario_key)
    except Exception:
        return


# ==============================================
# 7) Eventos fixos (inclui gravidez, mas não só)
# ==============================================
def _collect_mary_events_from_facts(facts: Dict[str, any]) -> Dict[str, str]:
    """
    Coleta eventos de Mary em 2 formatos:
      1) Chaves planas: "mary.evento.xyz": "..."
      2) Estrutura aninhada: "mary": { "evento": { "xyz": "..." } }
    Retorna dict label -> texto.
    """
    eventos: Dict[str, str] = {}

    if not isinstance(facts, dict):
        return eventos

    # ---- Formato plano: mary.evento.xyz ----
    for k, v in facts.items():
        if not isinstance(k, str):
            continue
        if not k.startswith("mary.evento."):
            continue
        if not v:
            continue
        label = k.replace("mary.evento.", "", 1)
        eventos[label] = str(v)

    # ---- Formato aninhado: facts["mary"]["evento"][label] ----
    for mk in ("mary", "Mary"):
        mary_obj = facts.get(mk, {})
        if not isinstance(mary_obj, dict):
            continue

        evt_block = mary_obj.get("evento") or mary_obj.get("eventos") or {}
        if not isinstance(evt_block, dict):
            continue

        for label, val in evt_block.items():
            if not val:
                continue
            if label not in eventos:
                eventos[str(label)] = str(val)

    return eventos


def _collect_pregnancy_events_from_facts(facts: Dict[str, any]) -> Dict[str, str]:
    """
    Extrai só eventos que parecem falar de gravidez.
    """
    eventos = _collect_mary_events_from_facts(facts)
    preg_events: Dict[str, str] = {}
    for label, txt in eventos.items():
        low = txt.lower()
        if any(w in low for w in ["gravidez", "grávida", "teste positivo", "ultrassom", "ultra-som", "beta-hcg"]):
            preg_events[label] = txt
    return preg_events


def _derive_pregnancy_from_events(eventos: Dict[str, str]) -> Dict[str, any]:
    """
    A partir dos textos de eventos, tenta derivar:
      - gravida: bool
      - gravidez.meses
      - gravidez.semanas
      - gravidez.data_confirma
    Estratégia simplificada.
    """
    if not eventos:
        return {}

    # Pega o "último" evento pela ordenação do label.
    # Se o label tiver data tipo mary_2025-12-01 ou ..._2025-11-26,
    # a ordem alfabética costuma respeitar a ordem temporal.
    labels = sorted(eventos.keys())
    ultimo = eventos[labels[-1]].lower()

    out: Dict[str, any] = {}
    if any(w in ultimo for w in ["gravidez", "grávida", "teste positivo", "beta-hcg", "pré-natal", "ultrassom"]):
        out["gravida"] = True

    m_mes = re.search(r"(\d+)\s*mes", ultimo)
    if m_mes:
        out["gravidez.meses"] = m_mes.group(1)

    m_sem = re.search(r"(\d+)\s*semana", ultimo)
    if m_sem:
        out["gravidez.semanas"] = m_sem.group(1)

    # data simples tipo 26/11/2025
    m_data = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", ultimo)
    if m_data:
        out["gravidez.data_confirma"] = m_data.group(1)

    return out

# ==============================================
# 8) BLOCO de sistema principal da Mary
# ==============================================
def _build_system_block(
    persona_text: str,
    rolling_summary: str,
    sensory_focus: str,
    nsfw_hint: str,
    scene_loc: str,
    entities_line: str,
    evidence: str,
    prefs_line: str,
    scene_time: str = "",
) -> str:
    """
    System único que embute persona + resumo contínuo + preferências + memórias.
    """
    return f"""
Você é **Mary Massariol – Esposa Cúmplice**, personagem fixa em um roleplay contínuo com o usuário.

REGRAS GERAIS:
- Fale SEMPRE em primeira pessoa (eu).
- Trate o usuário como parceiro/cônjuge, com intimidade e cumplicidade estáveis (salvo se os fatos disserem o contrário).
- Tom sensual adaptado às preferências do usuário (nível, ritmo, tamanho de resposta).
- **Nunca quebre a quarta parede**: não fale sobre tokens, prompts, LLM, nem sobre "modelo de linguagem".
- Mantenha a continuidade dos fatos descritos no RESUMO_CONTINUO, na MEMÓRIA_PIN e em EVENTOS_FIXOS_MARY.
- Se houver contradição entre o pedido do usuário e a MEMÓRIA_PIN/EVENTOS_FIXOS_MARY, priorize essas memórias como verdade canônica e negocie com o usuário dentro da cena.

PERSONA (núcleo fixo):
{persona_text}

RESUMO_CONTINUO (v2 – história geral até agora):
{rolling_summary or '(sem resumo ainda – mantenha o tom de começo de relação, mas já casados e cúmplices).'}

PREFERENCIAS_DO_USUARIO:
{prefs_line}

ENTIDADES_MARY:
{entities_line}

EVIDENCIA_RECENTE_DO_USUARIO (últimas falas compactadas):
{evidence}

CENA_ATUAL:
- Local atual: {scene_loc}
- Momento/tempo atual (se existir): {scene_time or 'não especificado'}

FOCO_SENSORIAL_DESTE_TURNO:
- Priorize na descrição: {sensory_focus} (mas não se limite apenas a isso).

NSFW/POLITICA_DE_CONTEUDO:
{nsfw_hint}

ESTILO_DE_RESPOSTA:
- 4 a 7 parágrafos.
- 2 a 4 frases por parágrafo.
- Sempre misture descrição (terceira pessoa leve sobre ela mesma), sensação física, emoção e fala direta.
- Evite repetir expressões de forma mecânica.
- Não faça listas; escreva prosa contínua.
- Se o usuário pedir mudança brusca de cenário/tempo, negocie dentro da narrativa ou marque como "salto temporal" de forma suave, sem apagar o passado.

IMPORTANTE:
- Use as memórias fixas (EVENTOS_FIXOS_MARY e MEMÓRIA_PIN) como FONTE DE VERDADE sobre fatos importantes (por exemplo, gravidez, traições, decisões marcantes).
- Se algo não estiver na memória, não invente detalhes canônicos definitivos; trate como hipótese ou expectativa até ser confirmado.
    """.strip()


# ==============================================
# 9) Janela segura de saída
# ==============================================
def _safe_max_output(window_tokens: int, prompt_tokens: int) -> int:
    """
    Garante um limite de saída que não estoure a janela de contexto.
    """
    if window_tokens <= 0:
        window_tokens = _DEFAULT_WINDOW
    # deixa ~20% para segurança
    max_out = int(window_tokens * 0.3)
    # se o prompt já comer quase tudo, reduz mais ainda
    if prompt_tokens > window_tokens * 0.8:
        max_out = int(window_tokens * 0.2)
    if max_out < 512:
        max_out = 512
    return max_out


# ==============================================
# 10) Aviso visual de “queda de memória”
# ==============================================
def _mem_drop_warn(report: Dict[str, any]):
    if not report:
        return
    summarized = report.get("summarized_pairs", 0)
    trimmed = report.get("trimmed_pairs", 0)
    hist_tokens = report.get("hist_tokens", 0)
    hist_budget = report.get("hist_budget", 0)
    if summarized or trimmed:
        st.caption(
            f"🧠 Memória ajustada: {summarized} pares antigos resumidos, {trimmed} blocos verbatim podados. "
            f"(histórico: {hist_tokens}/{hist_budget} tokens). Se notar esquecimentos, peça um 'recap curto' "
            "ou fixe fatos na Memória Canônica."
        )


# ==============================================
# 11) Tool-calling robusto + roteamento
# ==============================================
def _provider_for_model(mid: str) -> str:
    if not mid:
        return "desconhecido"
    m = mid.lower()
    if m.startswith("together/"):
        return "Together"
    if "deepseek" in m:
        return "OpenRouter"  # ou Together, conforme seu setup
    if "grok-4.1" in m or "tng-r1t-chimera" in m:
        return "OpenRouter"
    if "claude" in m:
        return "OpenRouter"
    if "qwen" in m or "llama-3" in m:
        return "Together"  # ou OpenRouter — depende da sua configuração
    return "OpenRouter"


def _robust_chat_call(
    model: str,
    messages: List[Dict[str, any]],
    max_tokens: int,
    temperature: float,
    top_p: float,
    fallback_models: List[str] | None = None,
    tools: List[Dict[str, any]] | None = None,
):
    """
    Camada única de chamada de LLM com:
      - roteamento automático via route_chat_strict
      - failover para modelos alternativos
      - suporte opcional a tools
      - REASONING dinâmico para Grok e TNG-R1T-Chimera
    """
    if fallback_models is None:
        fallback_models = []

    used_model = model
    provider = _provider_for_model(model)

    def _build_body(mid: str) -> Dict[str, any]:
        low = (mid or "").lower()
        extra: Dict[str, any] = {}

        # GROK 4.1 Fast – reasoning opcional
        if "grok-4.1-fast" in low:
            extra["reasoning"] = {"effort": "medium"}

        # TNG R1T Chimera – é um modelo R1T, tende a usar "think tokens"
        if "tng-r1t-chimera" in low:
            pass

        body: Dict[str, any] = {
            "model": mid,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if extra:
            body["extra_body"] = extra
        return body

    # 1ª tentativa: modelo principal
    try:
        body = _build_body(model)
        data, used_model, prov = route_chat_strict(model, body)
        return data, used_model, prov
    except Exception as e:
        err_msg = str(e)
        st.warning(f"⚠️ Modelo principal falhou ({model}): {err_msg}")

    # Tentativas de fallback
    for fb in fallback_models:
        try:
            body = _build_body(fb)
            data, used_model, prov = route_chat_strict(fb, body)
            return data, fb, prov
        except Exception as e:
            st.warning(f"⚠️ Fallback falhou ({fb}): {e}")

    # Se tudo falhar, devolve um shape mínimo
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Desculpa, tive um problema para responder agora. Tenta de novo em instantes."
            }
        }]
    }, "synthetic-fallback", "synthetic-fallback"


# ==============================================
# 12) Detecção de tags temáticas simples
# ==============================================
def _detect_thematic_tags_from_prompt(prompt: str) -> List[str]:
    low = (prompt or "").lower()
    tags = []
    if any(w in low for w in ["gravidez", "grávida", "ultrassom", "ultra-som", "obstetra", "ginecologista"]):
        tags.append("gravidez")
    if any(w in low for w in ["trai", "traição", "infiel", "amante"]):
        tags.append("traicao")
    if any(w in low for w in ["primeira vez", "virgem", "desvirg"]):
        tags.append("primeira_vez")
    if any(w in low for w in ["viagem", "hotel", "aeroporto"]):
        tags.append("viagem")
    return tags


def _get_thematic_memories_for_tags(usuario_key: str, tags: List[str]) -> str:
    if not tags:
        return ""
    try:
        f = cached_get_facts(usuario_key) or {}
    except Exception:
        f = {}
    blocos = []
    for tag in tags:
        k = f"mary.thematic.{tag}"
        v = f.get(k)
        if not v:
            continue
        blocos.append(f"[{tag}] {v}")
    return "\n".join(blocos)


# ==============================================
# Persona específica (ideal: characters/mary/persona.py)
# ==============================================
try:
    from .persona import get_persona  # -> Tuple[str, List[Dict[str,str]]]
except ImportError:
    def get_persona() -> Tuple[str, List[Dict[str, str]]]:
        txt = (
            "Você é Mary Massariol — Esposa Cúmplice — esposa e parceira de aventuras do usuário. "
            "Fale em primeira pessoa (eu). Tom insinuante e sutil. "
            "Traga 1 pista sensorial integrada à ação. "
            "Sem metacena, sem listas. 2–4 frases por parágrafo; 4–7 parágrafos."
        )
        return txt, []


# ==============================================
# SERVIÇO PRINCIPAL – MARY
# ==============================================
class MaryService(BaseCharacter):
    id: str = "mary"
    display_name: str = "Mary"

    def _exec_tool_call(self, name: str, args: dict, usuario_key: str) -> str:
        """
        Executa uma ferramenta chamada pelo modelo.
        Sempre retorna STRING, para ser usada como mensagem de `tool`.
        """
        try:
            user_display = st.session_state.get("user_id", "") or ""

            if name == "get_memory_pin":
                return self._build_memory_pin(usuario_key, user_display)

            if name == "set_fact":
                k = (args or {}).get("key", "") if isinstance(args, dict) else ""
                v = (args or {}).get("value", "") if isinstance(args, dict) else ""
                if not k:
                    return "ERRO: chave ('key') não informada."
                set_fact(usuario_key, k, v, {"fonte": "tool_call"})
                try:
                    clear_user_cache(usuario_key)
                except Exception:
                    pass
                return f"OK: {k}={v}"

            if name == "save_event":
                label = ""
                content = ""
                if isinstance(args, dict):
                    label = (args.get("label") or "").strip()
                    content = (args.get("content") or "").strip()

                if not content:
                    content = st.session_state.get("last_assistant_message", "").strip()

                if not content:
                    return "ERRO: nenhum conteúdo para salvar."

                if not label:
                    low = content.lower()
                    if "carlos" in low and "beatriz" in low:
                        label = "carlos_beatriz"
                    else:
                        label = f"evento_{int(time.time())}"

                fact_key = f"mary.evento.{label}"
                set_fact(usuario_key, fact_key, content, {"fonte": "tool_call"})
                try:
                    clear_user_cache(usuario_key)
                except Exception:
                    pass
                return f"OK: salvo em {fact_key}"

            return f"ERRO: ferramenta desconhecida: {name}"

        except Exception as e:
            return f"ERRO: {e}"

    # ===== API =====
    def reply(self, user: str, model: str) -> str:
        prompt = self._get_user_prompt()
        if not prompt:
            return ""

        usuario_key = _current_user_key()

        # COMANDOS DEBUG
        plow = prompt.strip().lower()
                # ============================
        # /reset historico  (apenas diálogo + resumo)
        # ============================
        if plow in (
            "/reset historico",
            "/reset histórico",
            "/reset historico mary",
            "/reset histórico mary",
        ):
            try:
                # 1) Zera apenas HISTÓRICO em cache (docs de conversa),
                #    sem apagar facts nem mary.evento.*
                hk = f"history::{usuario_key}"
                st.session_state[hk] = []

                # 2) Zera resumo rolante v2 (mas não apaga eventos/canônicos)
                set_fact(
                    usuario_key,
                    "mary.rs.v2",
                    "",
                    {"fonte": "reset_debug"},
                )
                set_fact(
                    usuario_key,
                    "mary.rs.v2.ts",
                    0,
                    {"fonte": "reset_debug"},
                )

                # 3) Limpa cache leve para forçar reload limpo
                clear_user_cache(usuario_key)

                # 4) Atualiza relatório de memória para UI
                hist_budget, meta_budget, safety_budget = _budget_slices(model)
                st.session_state["_mem_drop_report"] = {
                    "summarized_pairs": 0,
                    "trimmed_pairs": 0,
                    "hist_tokens": 0,
                    "hist_budget": hist_budget,
                }

            except Exception as e:
                return (
                    "⚠️ Erro ao resetar histórico desta sessão.\n"
                    f"Detalhe técnico: {e}"
                )

            return (
                "🧹 **Histórico de diálogo e resumo rolante de Mary foram zerados APENAS para esta sessão.**\n"
                "- Memória Canônica (parceiro_atual, casados, local, etc.) foi preservada.\n"
                "- Memórias fixas (`mary.evento.*`) e registros dentro de `mary['evento']` foram preservados.\n"
                "- Use normalmente: Mary continua lendo suas memórias fixas e fatos salvos."
            )

        if plow.startswith("/debug eventos"):
            try:
                f_all = cached_get_facts(usuario_key) or {}
            except Exception:
                f_all = {}

            eventos = _collect_mary_events_from_facts(f_all)

            if not eventos:
                return (
                    "🔎 DEBUG EVENTOS\n"
                    f"- user_key: {usuario_key}\n"
                    "- Nenhuma memória de evento encontrada para Mary."
                )

            linhas = [
                "🔎 DEBUG EVENTOS",
                f"- user_key: {usuario_key}",
                f"- total encontrados: {len(eventos)}",
                "",
            ]
            for label, v in sorted(eventos.items()):
                vs = str(v)
                if len(vs) > 220:
                    vs = vs[:220] + "..."
                k_fmt = f"mary.evento.{label}"
                linhas.append(f"• {k_fmt} = {vs}")

            return "\n".join(linhas)

        persona_text, history_boot = self._load_persona()

        local_atual = self._safe_get_local(usuario_key)
        try:
            f_all = cached_get_facts(usuario_key) or {}
        except Exception:
            f_all = {}
        prefs = _read_prefs(f_all)
        memoria_pin = self._build_memory_pin(usuario_key, user)

        thematic_tags = _detect_thematic_tags_from_prompt(prompt)
        thematic_block = _get_thematic_memories_for_tags(usuario_key, thematic_tags)

        pool = [
            "cabelo", "olhos", "lábios/boca", "mãos/toque", "respiração",
            "perfume", "pele/temperatura", "quadril/coxas", "voz/timbre", "sorriso"
        ]
        idx = int(st.session_state.get("mary_attr_idx", -1))
        idx = (idx + 1) % len(pool)
        st.session_state["mary_attr_idx"] = idx
        foco = pool[idx]

        try:
            nsfw_on = bool(nsfw_enabled(usuario_key))
        except Exception:
            nsfw_on = False

        nivel = prefs.get("nivel_sensual", "sutil")
        if not nsfw_on:
            nsfw_hint = (
                "NSFW: BLOQUEADO. Use sugestão, tensão e intimidade sem descrição explícita de atos; "
                "concentre-se em atmosfera, presença e condução confiante."
            )
        else:
            if nivel == "sutil":
                nsfw_hint = (
                    "NSFW: LIBERADO. Insinuação elegante e tensão crescente; foque em sensações, proximidade e desejo sexual, "
                    "descrevendo corpo e contato de forma clara, mas sem exagerar nos detalhes gráficos de atos sexuais ou anatomia."
                )
            elif nivel == "alta":
                nsfw_hint = (
                    "NSFW: LIBERADO. Intensifique descrição corporal e sensorial, "
                    "mantendo coerência emocional e respeito aos limites do casal."
                )
            else:
                nsfw_hint = (
                    "NSFW: LIBERADO. Sensualidade clara e progressiva; descreva sensações e ações sem pressa, "
                    "demonstrando desejo sexual e sedução sem repetição mecânica."
                )

        rolling = self._get_rolling_summary(usuario_key)
        entities_line = _entities_to_line(f_all)

        try:
            docs = cached_get_history(usuario_key) or []
        except Exception:
            docs = []
        evidence = self._compact_user_evidence(docs, max_chars=320)

        system_block = _build_system_block(
            persona_text=persona_text,
            rolling_summary=rolling,
            sensory_focus=foco,
            nsfw_hint=nsfw_hint,
            scene_loc=local_atual or "—",
            entities_line=entities_line,
            evidence=evidence,
            prefs_line=_prefs_line(prefs),
            scene_time=st.session_state.get("momento_atual", "")
        )

        lore_msgs: List[Dict[str, str]] = []
        try:
            q = (prompt or "") + "\n" + (rolling or "")
            top = lore_topk(usuario_key, q, k=4, allow_tags=None)
            if top:
                lore_text = " | ".join(d.get("texto", "") for d in top if d.get("texto"))
                if lore_text:
                    lore_msgs.append({"role": "system", "content": f"[LORE]\n{lore_text}"})
        except Exception:
            pass

        verbatim_ultimos = int(st.session_state.get("verbatim_ultimos", 20))
        hist_msgs = self._montar_historico(
            usuario_key,
            history_boot,
            model,
            verbatim_ultimos=verbatim_ultimos,
        )

        eventos_block = ""
        try:
            eventos_dict = _collect_mary_events_from_facts(f_all)
            if eventos_dict:
                linhas_evt = []
                for label, val in sorted(eventos_dict.items()):
                    linhas_evt.append(f"- {label}: {str(val).strip()}")
                joined = "\n".join(linhas_evt)[:1200]
                eventos_block = (
                    "EVENTOS_FIXOS_MARY:\n"
                    "Use estes registros como FONTE DE VERDADE. "
                    "Eles são eventos já vividos pelo casal (ex.: gravidez confirmada, encontros marcantes, etc.).\n"
                    + joined
                )
        except Exception:
            eventos_block = ""

        messages: List[Dict, ] = (
            [{"role": "system", "content": system_block}]
            + ([{"role": "system", "content": memoria_pin}] if memoria_pin else [])
            + ([{"role": "system", "content": f"MEMÓRIA_TEMÁTICA:\n{thematic_block}"}]
               if thematic_block else [])
            + ([{"role": "system", "content": eventos_block}] if eventos_block else [])
            + lore_msgs
            + [{
                "role": "system",
                "content": (
                    f"LOCAL_ATUAL: {local_atual or '—'}. "
                    "Regra dura: NÃO mude tempo/lugar sem pedido explícito do usuário."
                )
            }]
            + hist_msgs
            + [{"role": "user", "content": prompt}]
        )

        try:
            _mem_drop_warn(st.session_state.get("_mem_drop_report", {}))
        except Exception:
            pass

        win = _get_window_for(model)
        try:
            prompt_tokens = sum(toklen(m["content"]) for m in messages)
        except Exception:
            prompt_tokens = 0
        base_out = _safe_max_output(win, prompt_tokens)
        size = prefs.get("tamanho_resposta", "media")
        mult = 1.0 if size == "media" else (0.75 if size == "curta" else 1.4)
        max_out = max(512, int(base_out * mult))

        ritmo = prefs.get("ritmo", "lento")
        temperature = 0.6 if ritmo == "lento" else (0.9 if ritmo == "rapido" else 0.7)

        fallbacks = [
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "anthropic/claude-3.5-haiku",
        ]
        tools_to_use = None
        if st.session_state.get("tool_calling_on", False):
            tools_to_use = TOOLS

        max_iterations = 3
        iteration = 0
        texto = ""
        tool_calls = []

        while iteration < max_iterations:
            iteration += 1

            data, used_model, provider = _robust_chat_call(
                model,
                messages,
                max_tokens=max_out,
                temperature=temperature,
                top_p=0.95,
                fallback_models=fallbacks,
                tools=tools_to_use,
            )

            msg = (data.get("choices", [{}])[0].get("message", {}) or {})
            texto = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls", [])

            if not tool_calls or not st.session_state.get("tool_calling_on", False):
                break

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
                    result = self._exec_tool_call(func_name, func_args, usuario_key)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result
                    })

                    st.caption(f"  ✓ {func_name}: {result[:50]}...")

                except Exception as e:
                    error_msg = f"ERRO ao executar {func_name}: {str(e)}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": error_msg
                    })
                    st.warning(f"⚠️ {error_msg}")

        if iteration >= max_iterations and st.session_state.get("tool_calling_on", False):
            st.warning("⚠️ Limite de iterações de Tool Calling atingido. Resposta pode estar incompleta.")

        try:
            if bool(st.session_state.get("ultra_ia_on", False)) and texto:
                critic_model = st.session_state.get("ultra_critic_model", model) or model
                notes = critic_review(critic_model, system_block, prompt, texto)
                texto = polish(model, system_block, prompt, texto, notes)
        except Exception:
            pass

        try:
            forgot_pat = re.compile(
                r"\b(eu\s+n[ãa]o\s+(lembro|recordo)|"
                r"n[ãa]o\s+me\s+lembro\s+mais|"
                r"esqueci\s+o\s+que\s+est[aá]vamos\s+fazendo)\b",
                re.I
            )
        except Exception:
            forgot_pat = None

        try:
            save_interaction(usuario_key, prompt, texto, f"{provider}:{used_model}")
        except Exception:
            pass

        mem_triggers = (
            "use sua ferramenta de memória",
            "mary, use sua ferramenta de memória",
            "mary use sua ferramenta de memória",
            "registre na sua memória",
            "salve na memória",
            "gravar memória",
            "registre o fato",
        )
        plow = prompt.lower()

        if any(t in plow for t in mem_triggers):
            try:
                nomes_conhecidos = ["carlos", "beatriz", "ricardo", "laura", "janio", "mary"]
                achados = [n for n in nomes_conhecidos if n in plow]

                mdata = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", plow)
                data_sufixo = ""
                if mdata:
                    d, m, y = mdata.groups()
                    data_sufixo = f"_{y}-{int(m):02d}-{int(d):02d}"

                if len(achados) >= 2:
                    base_label = f"{achados[0]}_{achados[1]}"
                elif len(achados) == 1:
                    base_label = achados[0]
                else:
                    base_label = f"evento_{int(time.time())}"

                label = f"{base_label}{data_sufixo}"
                fact_key = f"mary.evento.{label}"

                content = texto.strip() or "(sem conteúdo)"

                set_fact(usuario_key, fact_key, content, {"fonte": "auto_gravado"})

                if "carlos" in achados and "beatriz" in achados:
                    set_fact(
                        usuario_key,
                        "mary.evento.carlos_beatriz",
                        content,
                        {"fonte": "auto_gravado_alias"},
                    )

                clear_user_cache(usuario_key)

                st.session_state["last_saved_mary_event_key"] = fact_key
                st.session_state["last_saved_mary_event_val"] = content

                st.caption(f"🧠 Memória fixa registrada automaticamente como: **{fact_key}**")
            except Exception as e:
                st.warning(f"⚠️ Falha ao registrar memória fixa: {e}")

        try:
            _extract_and_store_entities(usuario_key, prompt, texto)
        except Exception:
            pass

        try:
            self._update_rolling_summary_v2(usuario_key, model, prompt, texto)
        except Exception:
            pass

        try:
            frag = f"[USER] {prompt}\n[MARY] {texto}"
            lore_save(usuario_key, frag, tags=["mary", "chat"])
        except Exception:
            pass

        try:
            ph = self._suggest_placeholder(texto, local_atual)
            st.session_state["suggestion_placeholder"] = ph
            st.session_state["last_assistant_message"] = texto
        except Exception:
            st.session_state["suggestion_placeholder"] = ""

        try:
            if provider == "synthetic-fallback":
                st.info("⚠️ Provedor instável. Resposta em fallback — pode continuar normalmente.")
            elif used_model and "together/" in used_model:
                st.caption(f"↪️ Failover automático: **{used_model}**.")
        except Exception:
            pass

        if st.session_state.get("json_mode_on", False):
            try:
                payload = {
                    "role": "assistant",
                    "character": "Mary",
                    "content": texto,
                }
                return json.dumps(payload, ensure_ascii=False, indent=2)
            except Exception:
                return texto

        return texto

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
        """
        Memória canônica curta + pistas fortes (não exceder).

        AGORA inclui também um mini-timeline textual dos eventos de gravidez,
        usando exatamente as memórias fixas salvas (mary.evento.*),
        para o modelo não tratar a gravidez como "hipótese".
        """
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}

        # --- 1) eventos fixos (inclui gravidez, mas não só) ---
        try:
            eventos_todos = _collect_mary_events_from_facts(f)
        except Exception:
            eventos_todos = {}

        # Eventos especificamente relacionados à gravidez
        try:
            preg_events = _collect_pregnancy_events_from_facts(f)
            preg_from_events = _derive_pregnancy_from_events(preg_events)
        except Exception:
            preg_events = {}
            preg_from_events = {}

        blocos: List[str] = []

        # --- 2) parceiro / relação ---
        parceiro = f.get("parceiro_atual") or f.get("parceiro") or ""
        nome_usuario = (parceiro or user_display).strip()
        if parceiro:
            blocos.append(f"parceiro_atual={parceiro}")

        # 🔴 Default agora é False (não assume casados se nada foi salvo)
        casados = bool(f.get("casados", False))
        blocos.append(f"casados={casados}")

        # --- 3) gravidez como FATO CANÔNICO (sem engessar mês; lê dos eventos) ---
        raw_gravida = (
            preg_from_events.get("gravida")
            if "gravida" in preg_from_events
            else f.get("gravida", False)
        )

        if isinstance(raw_gravida, bool):
            gravida = raw_gravida
        else:
            gravida = str(raw_gravida).strip().lower() in (
                "1", "true", "sim", "grávida", "gravida"
            )

        if gravida:
            meses = (
                preg_from_events.get("gravidez.meses")
                or f.get("gravidez.meses")
                or f.get("gravidez.meses_atual")
                or ""
            )
            semanas = (
                preg_from_events.get("gravidez.semanas")
                or f.get("gravidez.semanas")
                or ""
            )
            data_conf = (
                preg_from_events.get("gravidez.data_confirma")
                or f.get("gravidez.data_confirma")
                or ""
            )

            detalhes = ["gravida=True"]
            if meses not in ("", None):
                detalhes.append(f"meses={meses}")
            if semanas not in ("", None):
                detalhes.append(f"semanas={semanas}")
            if data_conf not in ("", None):
                detalhes.append(f"desde={data_conf}")

            blocos.append("; ".join(detalhes))

            # 🔎 Linha do tempo da gravidez – usa as memórias salvas, com texto real
            if preg_events:
                linhas_tl: List[str] = []

                # ordena por label (ex.: mary_2025-11-26, mary_2025-11-28, mary_2025-12-01)
                for label, raw in sorted(preg_events.items()):
                    txt = str(raw or "")
                    # compacta espaços e quebra em no máx ~220 chars
                    compact = " ".join(txt.split())
                    snippet = compact[:220]
                    linhas_tl.append(f"{label}: {snippet}")

                # limita tamanho total pra não explodir o PIN
                joined = " | ".join(linhas_tl)
                if len(joined) > 800:
                    joined = joined[-800:]  # guarda o fim, que tende a ser o mais recente

                blocos.append(f"linha_do_tempo_gravidez={{ {joined} }}")

        # --- 4) entidades gerais (club_name etc.) ---
        ent_line = _entities_to_line(f)
        if ent_line and ent_line != "—":
            blocos.append(f"entidades=({ent_line})")

        # --- 5) primeira vez registrada, se existir ---
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
            f"NOME_USUARIO={nome_usuario}. FATOS={{ {mem_str} }}.\n"
            "Regras:\n"
            "- Use estes FATOS como verdade canônica sobre relação, gravidez e contexto.\n"
            "- Se linha_do_tempo_gravidez existir, NUNCA trate a gravidez como dúvida ou hipótese.\n"
            "- Use ENTIDADES e EVENTOS_FIXOS_MARY (outro bloco de sistema) como fonte de verdade adicional.\n"
            "- Se algo NÃO estiver na memória, pergunte ou siga o que o usuário disser — não invente."
        )
        return pin


    def _montar_historico(
        self,
        usuario_key: str,
        history_boot: List[Dict[str, str]],
        model: str,
        verbatim_ultimos: int = 10,
    ) -> List[Dict[str, str]]:
        """
        Monta histórico usando orçamento por modelo:
          - Últimos N turnos verbatim preservados.
          - Antigos viram [RESUMO-*] em 1–3 camadas.
          - Se necessário, poda verbatim mais antigo.
        Além disso, grava um relatório em st.session_state['_mem_drop_report'].
        """
        win = _get_window_for(model)
        hist_budget, meta_budget, _ = _budget_slices(model)

        docs = cached_get_history(usuario_key)
        if not docs:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]

        pares: List[Dict[str, str]] = []
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get("resposta_mary") or "").strip()
            if u:
                pares.append({"role": "user", "content": u})
            if a:
                pares.append({"role": "assistant", "content": a})

        if not pares:
            st.session_state["_mem_drop_report"] = {}
            return history_boot[:]

        keep = max(0, verbatim_ultimos * 2)
        verbatim = pares[-keep:] if keep else []
        antigos = pares[: len(pares) - len(verbatim)]

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

        def _hist_tokens(mm: List[Dict[str, str]]) -> int:
            return sum(toklen(m["content"]) for m in mm)

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

        return msgs if msgs else history_boot[:]

        # ===== Rolling summary helpers =====
    def _get_rolling_summary(self, usuario_key: str) -> str:
        try:
            f = cached_get_facts(usuario_key) or {}
            return str(f.get("mary.rs.v2", "") or f.get("mary.rolling_summary", "") or "")
        except Exception:
            return ""

    def _should_update_summary(self, usuario_key: str, last_user: str, last_assistant: str) -> bool:
        try:
            f = cached_get_facts(usuario_key)
            last_summary = f.get("mary.rs.v2", "")
            last_update_ts = float(f.get("mary.rs.v2.ts", 0))
            now = time.time()
            if not last_summary:
                return True
            if now - last_update_ts > 300:  # 5 minutos
                return True
            if (len(last_user) + len(last_assistant)) > 100:
                return True
            return False
        except Exception:
            return True

    def _update_rolling_summary_v2(
        self,
        usuario_key: str,
        model: str,
        last_user: str,
        last_assistant: str
    ) -> None:
        """
        Atualiza o resumo rolante (mary.rs.v2) de forma INCREMENTAL.
        """
        if not self._should_update_summary(usuario_key, last_user, last_assistant):
            return

        # Carrega resumo anterior
        try:
            f = cached_get_facts(usuario_key) or {}
            resumo_anterior = str(f.get("mary.rs.v2", "") or "")
            ts_anterior = float(f.get("mary.rs.v2.ts", 0) or 0)
        except Exception:
            resumo_anterior = ""
            ts_anterior = 0.0

        seed = (
            "Você mantém um RESUMO CONTÍNUO da história entre Mary e o usuário.\n\n"
            "TAREFA:\n"
            "- Atualize o RESUMO_ANTERIOR com as NOVAS INFORMAÇÕES da última interação.\n"
            "- Mantenha fatos antigos se ainda forem verdadeiros (nomes, relação, locais, clima, segredos, decisões).\n"
            "- Se algo for claramente desmentido, ajuste ou remova.\n"
            "- Foque em fatos duráveis: relação (casados / cúmplices), locais importantes, eventos marcantes, "
            "acordos, limites, fantasias recorrentes.\n"
            "- Não repita diálogo literal.\n"
            "- Entregue um resumo único, em 8–14 frases telegráficas (pode usar '•' se quiser, mas não é obrigatório)."
        )

        corpo = (
            f"RESUMO_ANTERIOR:\n{resumo_anterior or '(sem resumo anterior ainda)'}\n\n"
            "ULTIMA_INTERACAO:\n"
            f"USER:\n{last_user}\n\n"
            f"MARY:\n{last_assistant}"
        )

        try:
            data, used_model, provider = route_chat_strict(model, {
                "model": model,
                "messages": [
                    {"role": "system", "content": seed},
                    {"role": "user", "content": corpo}
                ],
                "max_tokens": 260,
                "temperature": 0.2,
                "top_p": 0.9,
            })
            resumo_novo = (
                data.get("choices", [{}])[0]
                .get("message", {}) or {}
            ).get("content", "").strip()
            if resumo_novo:
                set_fact(usuario_key, "mary.rs.v2", resumo_novo, {"fonte": "auto_summary"})
                set_fact(usuario_key, "mary.rs.v2.ts", time.time(), {"fonte": "auto_summary"})
                clear_user_cache(usuario_key)
        except Exception:
            # Se der erro, mantém o resumo anterior
            return

    # ===== Placeholder leve =====
    def _suggest_placeholder(self, assistant_text: str, scene_loc: str) -> str:
        s = (assistant_text or "").lower()
        if "?" in s:
            return "Continua do ponto exato… me conduz."
        if any(k in s for k in ["vamos", "topa", "que tal", "prefere", "quer"]):
            return "Quero — descreve devagar o próximo passo."
        if scene_loc:
            return f"No {scene_loc} mesmo — fala baixinho no meu ouvido."
        return "Mantém o cenário e dá o próximo passo com calma."

    # ===== Evidência concisa do usuário (últimas falas) =====
    def _compact_user_evidence(self, docs: List[Dict], max_chars: int = 320) -> str:
        snippets: List[str] = []
        for d in reversed(docs):
            u = (d.get("mensagem_usuario") or "").strip()
            if u:
                u = re.sub(r"\s+", " ", u)
                snippets.append(u)
            if len(snippets) >= 4:
                break
        s = " | ".join(reversed(snippets))[:max_chars]
        return s

    # ===== Sidebar (somente leitura) =====
    def render_sidebar(self, container) -> None:
        container.markdown(
            "**Mary — Esposa Cúmplice** • Respostas sensuais (do sutil ao explícito, conforme preferências); "
            "4–7 parágrafos. Relação canônica: casados e cúmplices."
        )

        # mesma chave usada no reply()
        usuario_key = _current_user_key()

        # facts do usuário
        try:
            f = cached_get_facts(usuario_key) or {}
        except Exception:
            f = {}

        casados = bool(f.get("casados", False))
        ent = _entities_to_line(f)
        rs = (f.get("mary.rs.v2") or "")[:200]
        prefs = _read_prefs(f)

        container.caption(f"Estado da relação: **{'Casados' if casados else '—'}**")
        container.markdown("---")

        # toggles globais
        json_on = container.checkbox(
            "JSON Mode",
            value=bool(st.session_state.get("json_mode_on", False))
        )
        tool_on = container.checkbox(
            "Tool-Calling",
            value=bool(st.session_state.get("tool_calling_on", False))
        )
        st.session_state["json_mode_on"] = json_on
        st.session_state["tool_calling_on"] = tool_on

        lora = container.text_input(
            "Adapter ID (Together LoRA) — opcional",
            value=st.session_state.get("together_lora_id", "")
        )
        st.session_state["together_lora_id"] = lora

        if ent and ent != "—":
            container.caption(f"Entidades salvas: {ent}")
        if rs:
            container.caption("Resumo rolante ativo (v2).")
        container.caption(
            f"Prefs: nível={prefs.get('nivel_sensual')}, "
            f"ritmo={prefs.get('ritmo')}, "
            f"tamanho={prefs.get('tamanho_resposta')}"
        )

        # =====================================================
        # 1) DEBUG – mostrar tudo que o get_facts(...) trouxe
        # =====================================================
        with container.expander("🔎 DEBUG – facts brutos", expanded=False):
            if not f:
                st.caption("⚠️ Nenhum fact retornado para esta chave de usuário.")
                st.code(usuario_key)
            else:
                st.caption(f"User key: `{usuario_key}`")
                for k, v in f.items():
                    vs = str(v)
                    if len(vs) > 120:
                        vs = vs[:120] + "..."
                    st.write(f"- **{k}** = {vs}")

        # ============================
        # 🧠 Memórias fixas de Mary
        # ============================
        with container.expander("🧠 Memórias fixas de Mary", expanded=True):
            try:
                f_all = cached_get_facts(usuario_key) or {}
            except Exception:
                f_all = {}

            eventos = _collect_mary_events_from_facts(f_all)

            # também considera o que acabou de ser salvo nesta sessão
            last_key = st.session_state.get("last_saved_mary_event_key", "")
            last_val = st.session_state.get("last_saved_mary_event_val", "")
            if last_key:
                short = last_key.replace("mary.evento.", "")
                if short not in eventos:
                    eventos[short] = last_val or "(salvo nesta sessão; aguardando backend)"

            if not eventos:
                container.caption(
                    "Nenhuma memória salva ainda.\n"
                    "Ex.: **Mary, use sua ferramenta de memória para registrar o fato: ...**"
                )
            else:
                for label, val in sorted(eventos.items()):
                    container.markdown(f"**{label}**")
                    container.caption(val[:280] + ("..." if len(val) > 280 else ""))

                    col1, col2 = container.columns([1, 1])
                    with col1:
                        # botão apagar (só funciona se seu repo tiver delete_fact)
                        if container.button("🗑 Apagar", key=f"del_{usuario_key}_{label}"):
                            try:
                                from core.repositories import delete_fact
                            except Exception:
                                delete_fact = None

                            if delete_fact:
                                # tenta apagar nos dois formatos
                                delete_fact(usuario_key, f"mary.evento.{label}")
                                delete_fact(usuario_key, f"mary.eventos.{label}")
                            clear_user_cache(usuario_key)
                            container.success(f"Memória **{label}** apagada.")
                            st.experimental_rerun()
                    with col2:
                        container.caption(f"mary.evento.{label}")
