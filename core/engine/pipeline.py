from typing import List, Dict, Tuple
import re
from re import error as ReError

from core.common.base_service import BaseCharacter
from core.repositories import (
    save_interaction, get_history_docs, set_fact, get_fact,
    get_facts, last_event,
)
from core.rules import violou_mary, reforco_system
from core.locations import infer_from_prompt
from core.textproc import strip_metacena, formatar_roleplay_profissional
from core.tokens import toklen
from core.service_router import route_chat_strict
from core.nsfw import nsfw_enabled

# -------- util sentence split (sem look-behind variável)
_SENT_END = re.compile(r'([.!?…]["”»\']?)\s+')

def _split_sentences(text: str) -> List[str]:
    text = re.sub(r'\s*\n+\s*', ' ', text)
    parts, i = [], 0
    for m in _SENT_END.finditer(text):
        parts.append(text[i:m.end(1)].strip())
        i = m.end()
    tail = text[i:].strip()
    if tail: parts.append(tail)
    return [p for p in parts if p]

def _force_paragraphs(text: str, max_frases_por_par: int = 2, alvo_pars: Tuple[int,int]=(3,5)) -> str:
    existing = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    if len(existing) >= alvo_pars[0]: return text
    sents = _split_sentences(text)
    chunks = [' '.join(sents[i:i+max_frases_por_par]).strip()
              for i in range(0, len(sents), max_frases_por_par)]
    chunks = [c for c in chunks if c]
    if len(chunks) > alvo_pars[1]:
        head = chunks[:alvo_pars[1]-1]; tail = ' '.join(chunks[alvo_pars[1]-1:])
        chunks = head + [tail]
    return '\n\n'.join(chunks)

def _memory_context(usuario_key: str) -> str:
    try: f = get_facts(usuario_key) or {}
    except Exception: f = {}
    out = []
    if f.get("parceiro_atual"): out.append(f"RELACIONAMENTO: parceiro_atual={f['parceiro_atual']}")
    if "virgem" in f: out.append(f"STATUS ÍNTIMO: virgem={bool(f['virgem'])}")
    if f.get("primeiro_encontro"): out.append(f"PRIMEIRO_ENCONTRO: {f['primeiro_encontro']}")
    try: ev = last_event(usuario_key, "primeira_vez")
    except Exception: ev = None
    if ev:
        dt = ev.get("ts"); quando = dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt,"strftime") else str(dt)
        out.append(f"EVENTO_CANÔNICO: primeira_vez em {quando} @ {ev.get('local') or '—'}")
    return "\n".join(out).strip()

def _montar_historico(usuario_key: str, history_boot: List[Dict[str,str]], limite_tokens=120_000):
    docs = get_history_docs(usuario_key)
    if not docs: return history_boot[:]
    total, out = 0, []
    for d in reversed(docs):
        u = d.get("mensagem_usuario") or ""; a = d.get("resposta_mary") or ""
        t = toklen(u)+toklen(a)
        if total + t > limite_tokens: break
        out.append({"role":"user","content":u}); out.append({"role":"assistant","content":a})
        total += t
    return list(reversed(out)) if out else history_boot[:]

def _make_third_person_flag(name: str) -> re.Pattern:
    safe = re.escape((name or "").strip())
    return re.compile(rf"(^|\n)\s*{safe}\b", re.IGNORECASE)

def _precisa_primeira_pessoa(txt: str, character: str) -> bool:
    return bool(_make_third_person_flag(character or "Mary").search(txt))

def _reforcar_primeira_pessoa(model: str, resposta: str) -> str:
    data, used_model, provider = route_chat_strict(model, {
        "model": model,
        "messages": [
            {"role":"system","content":"Reescreva em 1ª pessoa, 3–5 parágrafos, 1–2 frases cada, sem parênteses."},
            {"role":"user","content": resposta}
        ],
        "max_tokens": 2048, "temperature": 0.5, "top_p": 0.9
    })
    return (data.get("choices",[{}])[0].get("message",{}) or {}).get("content","") or resposta

def _pos_processar_seguro(texto: str, local_atual: str, max_frases_por_par=2) -> str:
    if not texto: return texto
    s = texto.replace("\\","\\\\")
    try:
        s = strip_metacena(s)
        s = formatar_roleplay_profissional(s, max_frases_por_par=max_frases_por_par)
        s = _force_paragraphs(s, max_frases_por_par=max_frases_por_par)
        return s.replace("\\\\","\\")
    except ReError:
        return texto

def generate_response(svc: BaseCharacter, usuario: str, prompt_usuario: str, model: str) -> str:
    char = (svc.name or "Mary").strip()
    usuario_key = usuario if char.lower()=="mary" else f"{usuario}::{char.lower()}"

    loc = infer_from_prompt(prompt_usuario) or ""
    if loc: set_fact(usuario_key, "local_cena_atual", loc, {"fonte":"service"})

    hist = _montar_historico(usuario_key, svc.history_boot())
    local_atual = get_fact(usuario_key, "local_cena_atual", "") or ""
    memo = _memory_context(usuario_key)

    flirt_mode = bool(get_fact(usuario_key, "flirt_mode", False))
    nsfw_on = bool(nsfw_enabled(usuario_key))
    parceiro = (get_fact(usuario_key, "parceiro_atual", "") or "").strip().lower()
    romance_on = (char.lower()=="laura" and parceiro in {"janio","jânio"})

    estilo_msg = {"role":"system","content": svc.style_guide(nsfw_on, flirt_mode, romance_on)}
    few = svc.fewshots(flirt_mode, nsfw_on, romance_on)

    local_pin = {"role":"system","content": f"LOCAL_PIN: {local_atual or '—'}. Não mude o cenário salvo pedido explícito do usuário."}
    antirepeat_pin = {"role":"system","content": "ANTI-ECO: evite repetir frases de respostas recentes; avance a cena com fala/ação inéditas."}

    messages: List[Dict[str,str]] = (
        [{"role":"system","content": svc.persona_text()}, estilo_msg, local_pin, antirepeat_pin]
        + few + hist + [{
            "role":"user",
            "content": f"LOCAL_ATUAL: {local_atual}\nCONTEXTO_PERSISTENTE:\n{memo}\n\n{prompt_usuario}"
        }]
    )

    payload = {"model": model, "messages": messages, "max_tokens": 2048, "temperature": 0.6, "top_p": 0.9}

    data, used_model, provider = route_chat_strict(model, payload)
    resposta = (data.get("choices",[{}])[0].get("message",{}) or {}).get("content","") or ""

    # Reforço Mary (se aplicável)
    if char.lower()=="mary" and violou_mary(resposta):
        data2, _, _ = route_chat_strict(model, {**payload, "messages":[{"role":"system","content":svc.persona_text()}, reforco_system()]+messages[1:]})
        resposta = (data2.get("choices",[{}])[0].get("message",{}) or {}).get("content","") or resposta

    if _precisa_primeira_pessoa(resposta, char):
        try: resposta = _reforcar_primeira_pessoa(model, resposta)
        except Exception: pass

    # Hooks persona-específicos
    try: resposta = svc.refine_post(resposta, prompt_usuario, nsfw_on)
    except Exception: pass

    # Formatação final
    resposta = _pos_processar_seguro(resposta, local_atual, max_frases_por_par=2)

    # Escopo persona (ex.: remover cross-over indesejado)
    try: resposta = svc.enforce_scope(resposta, prompt_usuario)
    except Exception: pass

    # Pós (ex.: arco Laura; fidelidade etc) – implementado por persona
    try: resposta = svc.post_generation(resposta, prompt_usuario, usuario_key)
    except Exception: pass

    save_interaction(usuario_key, prompt_usuario, resposta, f"{provider}:{used_model}")
    return resposta

