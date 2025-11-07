# main.py
from __future__ import annotations
import os
import sys
import time
import hmac
import hashlib
import inspect
import traceback
from pathlib import Path
from typing import Optional, List, Tuple, Dict
import streamlit as st
import base64
import re
from pymongo import MongoClient
from datetime import datetime
import html

# ========== BOOT (indexes/paths) ==========
# Adiciona o repositório do USO ao path do sistema para que possa ser importado
# Esta é a solução para o erro de instalação do git+https
try:
    # O caminho relativo funciona porque main.py está na raiz do projeto
    libs_path = os.path.abspath("./libs/USO" )
    if os.path.isdir(libs_path) and libs_path not in sys.path:
        sys.path.insert(0, libs_path)
except Exception:
    pass # Ignora se a pasta não existir

try:
    from core.memoria_longa import ensure_indexes
    ensure_indexes()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ========== CONFIG PÁGINA ==========
st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="centered")

# ===== CSS global =====
st.markdown("""
<style>
  html, body, .stApp { overflow-x: hidden; max-width: 100vw; }
  .block-container {
    max-width: 820px; width: 100%; margin: 0 auto; box-sizing: border-box;
    padding-top: 1rem; padding-bottom: 4rem; padding-left: 16px !important; padding-right: 16px !important;
  }
  .stChatMessage { line-height: 1.5; font-size: 1.02rem; }
  .stChatFloatingInputContainer, .stChatInput { max-width: 100% !important; width: 100% !important; overflow: hidden; }
  .assistant-paragraph {
    background: rgba(59,130,246,0.18); border-left: 3px solid rgba(59,130,246,0.55);
    padding: .55rem .75rem; margin: .5rem 0; border-radius: .5rem; line-height: 1.55; color: #fff;
  }
  .assistant-paragraph a { color: #fff; text-decoration: underline; }
  .assistant-paragraph a:hover { opacity: .85; }
  .assistant-paragraph + .assistant-paragraph { margin-top: .45rem; }
  .stChatMessage ::selection { background: rgba(59,130,246,0.35); color: #fff; }
  .stMarkdown img, .stImage img, .stVideo, .stAudio { max-width: 100% !important; height: auto !important; }
  @media (max-width: 420px) {
    .block-container { padding-left: 12px !important; padding-right: 12px !important; }
    .assistant-paragraph { font-size: .98rem; }
  }
</style>
""", unsafe_allow_html=True)

# --- Plano de fundo (CSS inline) ---
try:
    IMG_DIR  # type: ignore
except NameError:
    IMG_DIR = (ROOT / "imagem")
    try:
        IMG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        IMG_DIR = Path("./imagem")
        IMG_DIR.mkdir(parents=True, exist_ok=True)

def _encode_file_b64(p: Path) -> str:
    with p.open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def set_background(image_path: Path, *, darken: float = 0.25, blur_px: int = 0,
                   attach_fixed: bool = True, size_mode: str = "cover") -> None:
    if not image_path or not image_path.exists():
        return
    ext = image_path.suffix.lower()
    mime = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp", ".gif": "gif"}.get(ext, "jpeg")
    b64 = _encode_file_b64(image_path)
    att = "fixed" if attach_fixed else "scroll"
    darken = max(0.0, min(0.9, float(darken)))
    blur_px = max(0, min(40, int(blur_px)))
    size_mode = size_mode if size_mode in ("cover", "contain") else "cover"
    st.markdown(f"""
    <style>
    .stApp {{ background: transparent !important; }}
    .block-container {{ position: relative; z-index: 1; }}
    .stApp::before {{
      content: ""; position: fixed; inset: 0;
      background-image: url("data:image/{mime};base64,{b64}");
      background-position: center center; background-repeat: no-repeat;
      background-size: {size_mode}; background-attachment: {att}; filter: blur({blur_px}px); z-index: 0;
    }}
    .stApp::after {{
      content: ""; position: fixed; inset: 0; background: rgba(0,0,0,{darken}); z-index: 0; pointer-events: none;
    }}
    </style>
    """, unsafe_allow_html=True)

# ========== GATE OPCIONAL (senha) ==========
def _check_scrypt(pwd: str) -> bool:
    cfg = st.secrets.get("auth", {})
    salt_hex = cfg.get("salt", "")
    hash_hex = cfg.get("password_scrypt", "")
    if not salt_hex or not hash_hex:
        return False
    try:
        calc = hashlib.scrypt(
            pwd.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=2**14, r=8, p=1
        ).hex()
        return hmac.compare_digest(calc, hash_hex)
    except Exception:
        return False

def require_password_if_configured(app_name: str = "PERSONAGENS 2025"):
    if "auth" not in st.secrets:
        return
    st.session_state.setdefault("_auth_ok", False)
    st.session_state.setdefault("_auth_attempts", 0)
    st.session_state.setdefault("_auth_block_until", 0.0)

    now = time.time()
    if now < st.session_state["_auth_block_until"]:
        wait = int(st.session_state["_auth_block_until"] - now)
        st.error(f"Tentativas excessivas. Tente novamente em {wait}s.")
        st.stop()

    if st.session_state["_auth_ok"]:
        with st.sidebar:
            if st.button("Sair"):
                for k in ["_auth_ok", "_auth_attempts", "_auth_block_until"]:
                    st.session_state.pop(k, None)
                st.rerun()
        return

    st.title(f"🔒 {app_name}")
    pwd = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        if _check_scrypt(pwd):
            st.session_state["_auth_ok"] = True
            st.session_state["_auth_attempts"] = 0
            st.success("Acesso liberado.")
            st.rerun()
        else:
            st.session_state["_auth_attempts"] += 1
            backoff = 5 * (3 ** max(0, st.session_state["_auth_attempts"] - 1))
            st.session_state["_auth_block_until"] = time.time() + backoff
            st.error("Senha incorreta.")
            st.stop()
    st.stop()

require_password_if_configured("PERSONAGENS 2025")
st.title("PERSONAGENS 2025")

# ========== SECRETS → ENV ==========
def _load_env_from_secrets():
    sec = st.secrets
    mapping = {
        "OPENROUTER_API_KEY": sec.get("OPENROUTER_API_KEY", ""),
        "TOGETHER_API_KEY":   sec.get("TOGETHER_API_KEY", ""),
        "LLM_HTTP_TIMEOUT":   sec.get("LLM_HTTP_TIMEOUT", "60"),
        "MONGO_USER":         sec.get("MONGO_USER", ""),
        "MONGO_PASS":         sec.get("MONGO_PASS", ""),
        "MONGO_CLUSTER":      sec.get("MONGO_CLUSTER", ""),
        "APP_NAME":           sec.get("APP_NAME", "personagens2025"),
        "APP_PUBLIC_URL":     sec.get("APP_PUBLIC_URL", ""),
        "DB_BACKEND":         sec.get("DB_BACKEND", "mongo"),
        "APP_ENV":            sec.get("APP_ENV", "dev"),   # dev|prod
        "HF_TOKEN":           sec.get("HF_TOKEN", ""),     # ← para comics/FAL
    }
    for k, v in mapping.items():
        if v and not os.environ.get(k):
            os.environ[k] = str(v)

_load_env_from_secrets()
APP_ENV = os.environ.get("APP_ENV", "dev").lower().strip()

# ========== REGISTRY / ROUTER FALLBACKS ==========
def _safe_error(msg: str, exc: Exception | None = None):
    """Erro verboso em dev, curto em prod (stack guardado no session_state)."""
    if APP_ENV == "prod":
        st.error(msg)
        if exc:
            st.session_state["last_traceback"] = traceback.format_exc()
    else:
        if exc:
            st.error(msg + f"\n\n**{exc.__class__.__name__}:** {exc}\n\n```\n{traceback.format_exc()}\n```")
        else:
            st.error(msg)

try:
    from characters.registry import get_service, list_characters
except Exception as e:
    _safe_error("Falha ao importar `characters.registry`.", e)
    st.stop()

try:
    from core.service_router import available_providers, list_models, chat as provider_chat, route_chat_strict
except Exception:
    def available_providers():
        provs = []
        provs.append(("OpenRouter", bool(os.environ.get("OPENROUTER_API_KEY")), "OK" if os.environ.get("OPENROUTER_API_KEY") else "sem chave"))
        provs.append(("Together",   bool(os.environ.get("TOGETHER_API_KEY")), "OK" if os.environ.get("TOGETHER_API_KEY") else "sem chave"))
        return provs
    def list_models(_p: str | None = None):
        return []
    def provider_chat(model: str, messages: List[dict], **kw):
        raise RuntimeError("service_router indisponível.")
    def route_chat_strict(model: str, payload: dict):
        raise RuntimeError("service_router indisponível.")

# ========== DB HELPERS (fallbacks) ==========
try:
    from core.database import get_backend, set_backend, ping_db, get_col, db_status
except Exception:
    def get_backend() -> str: return os.environ.get("DB_BACKEND", "memory")
    def set_backend(kind: str) -> None: os.environ["DB_BACKEND"] = kind
    def ping_db(): return ("memory", True, "memória local")
    def get_col(_name: str): raise RuntimeError("DB indisponível")
    def db_status(): return ("unknown", "core.database ausente")

# Força Mongo como padrão se nada tiver sido escolhido explicitamente
try:
    if os.environ.get("DB_BACKEND", "").strip() == "":
        os.environ["DB_BACKEND"] = "mongo"
    if get_backend() != "mongo":
        set_backend("mongo")
except Exception:
    pass

# Repositório (histórico/fatos) — safe fallback
try:
    from core.repositories import (
        get_history_docs, get_history_docs_multi,
        set_fact, get_fact, get_facts, delete_fact,
        delete_user_history, delete_last_interaction, delete_all_user_data,
        register_event, list_events,
        save_interaction,
    )
except Exception:
    def get_history_docs(_u: str, limit: int = 400): return []
    def get_history_docs_multi(_keys: List[str], limit: int = 400): return []
    def set_fact(*a, **k): ...
    def get_fact(_u: str, _k: str, default=None): return default
    def get_facts(_u: str): return {}
    def delete_user_history(_u: str): return 0
    def delete_last_interaction(_u: str): return False
    def delete_all_user_data(_u: str): return {"hist": 0, "state": 0, "eventos": 0}
    def register_event(*a, **k): ...
    def list_events(_u: str, limit: int = 5): return []
    def save_interaction(*a, **k): ...
    def delete_fact(*a, **k): ...

# ========== SIDEBAR: Provedores + DB ==========
st.sidebar.subheader("🧠 Provedores LLM")
prov_status = {name: ok for (name, ok, _) in available_providers()}
for name, ok, detail in available_providers():
    st.sidebar.write(f"- **{name}**: {'✅ OK' if ok else '❌'} ({detail})")

st.sidebar.markdown("---")
st.sidebar.subheader("🗄️ Banco de Dados")
bk, info = db_status()
st.sidebar.caption(f"Backend: **{bk}** — {info}")

cur_backend = get_backend()
choice_backend = st.sidebar.radio(
    "Backend",
    options=["memory", "mongo"],
    index=(0 if cur_backend != "mongo" else 1),
    format_func=lambda x: "Memória (local)" if x == "memory" else "MongoDB (remoto)",
    horizontal=True,
)
if choice_backend != cur_backend:
    set_backend(choice_backend)
    st.sidebar.success(f"Backend ajustado para **{choice_backend}**.")
    st.rerun()

if st.sidebar.button("🔍 Testar conexão DB"):
    kind, ok, detail = ping_db()
    (st.sidebar.success if ok else st.sidebar.error)(f"{kind}: {detail}")

if choice_backend == "mongo":
    mu = os.environ.get("MONGO_USER", "")
    mc = os.environ.get("MONGO_CLUSTER", "")
    st.sidebar.caption(f"Mongo: user=`{mu or '—'}` host=`{mc or '—'}`")
    if st.sidebar.button("🧪 Insert/Find (diagnostic)"):
        try:
            col = get_col("diagnostic")
            r_id = col.insert_one({"ts": __import__("datetime").datetime.utcnow()})["inserted_id"]
            last = col.find_one(sort=[("ts", -1)])
            st.sidebar.success(f"OK (id={r_id}) — last={last}")
        except Exception as e:
            _safe_error("Falha no diagnóstico Mongo.", e)

st.sidebar.markdown("---")

# ========== ESTADO BASE ==========
st.session_state.setdefault("user_id", "Janio Donisete")
st.session_state.setdefault("character", "Mary")

# ----- LISTA DE MODELOS -----
FORCED_MODELS = [
    "deepseek/deepseek-chat-v3-0324",
    "anthropic/claude-3.5-haiku",
    "qwen/qwen3-max",
    "nousresearch/hermes-3-llama-3.1-405b",
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
    "together/Qwen/Qwen2.5-72B-Instruct",
    "together/Qwen/QwQ-32B",
    "inclusionai/ling-1t",
    "z-ai/glm-4.6",
    "thedrummer/cydonia-24b-v4.1",
    "x-ai/grok-4-fast",
    "moonshotai/Kimi-K2-Instruct-0905",
    "deepseek-ai/DeepSeek-R1-0528-tput",
    "x-ai/grok-code-fast-1",
    "google/gemma-3-27b-it",
    "google/gemini-2.5-flash",
    "openai/gpt-4o-mini",
]

def _merge_models() -> List[str]:
    try:
        models_router = list_models(None) or []
    except Exception:
        models_router = []
    models = list(dict.fromkeys(models_router + FORCED_MODELS))
    return models

def _provider_for(model_id: str) -> str:
    m = (model_id or "").lower()
    if any(m.startswith(x) for x in ("together/", "moonshotai/kimi-k2-instruct-0905",
                                     "google/gemma-3-27b-it", "google/gemini-2.5-flash")):
        return "Together"
    if any(m.startswith(x) for x in ("anthropic/","qwen/","nousresearch/","deepseek/","inclusionai/",
                                     "z-ai/","thedrummer/","x-ai/","openai/")):
        return "OpenRouter"
    return "OpenRouter"

def _has_creds_for(model_id: str) -> bool:
    prov = _provider_for(model_id)
    if prov == "Together":
        return bool(os.environ.get("TOGETHER_API_KEY"))
    return bool(os.environ.get("OPENROUTER_API_KEY"))

def _light_ping_model(model_id: str) -> bool:
    if "service_router" in sys.modules:
        try:
            route_chat_strict(model_id, {
                "model": model_id,
                "messages": [{"role": "system", "content": "ping"},
                             {"role": "user", "content": "pong?"}],
                "max_tokens": 4, "temperature": 0.0
            })
            return True
        except Exception:
            return False
    return True

try:
    all_models = _merge_models()
except Exception:
    all_models = FORCED_MODELS[:]

st.session_state.setdefault("model", (all_models[0] if all_models else "deepseek/deepseek-chat-v3-0324"))
st.session_state.setdefault("history", [])  # List[Tuple[str, str]]
st.session_state.setdefault("history_loaded_for", "")
st.session_state.setdefault("_active_key", "")

# ========== CONTROLES TOPO ==========
c1, c2 = st.columns([2, 2])
with c1:
    st.text_input("👤 Usuário", key="user_id", placeholder="Seu nome ou identificador")
with c2:
    names = list_characters()
    default_idx = names.index("Mary") if "Mary" in names else 0
    st.selectbox("🎭 Personagem", names, index=default_idx, key="character")

def _label_model(mid: str) -> str:
    prov = _provider_for(mid)
    tag = "" if mid in (list_models(None) or []) else " • forçado"
    return f"{prov} • {mid}{tag}"

_prev_model = st.session_state.get("_last_model_id", st.session_state.get("model"))
sel = st.selectbox(
    "🧠 Modelo",
    all_models,
    index=all_models.index(st.session_state["model"]) if st.session_state["model"] in all_models else 0,
    format_func=_label_model,
    key="model"
)
if sel != _prev_model:
    if not _has_creds_for(sel):
        st.warning("Este modelo requer credenciais do provedor correspondentes. Revertendo para o anterior.")
        st.session_state["model"] = _prev_model
    else:
        ok = _light_ping_model(sel)
        if not ok:
            st.warning("Modelo não disponível no roteador atual. Revertendo.")
            st.session_state["model"] = _prev_model
        else:
            st.session_state["_last_model_id"] = sel

@st.cache_resource
def _mongo():
    """Retorna coleção MongoDB com cache de conexão."""
    try:
        mongo_user = st.secrets.get("MONGO_USER", "")
        mongo_pass = st.secrets.get("MONGO_PASS", "")
        mongo_cluster = st.secrets.get("MONGO_CLUSTER", "")
        if not (mongo_user and mongo_pass and mongo_cluster):
            return None
        uri = f"mongodb+srv://{mongo_user}:{mongo_pass}@{mongo_cluster}/?retryWrites=true&w=majority"
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        db = client["roleplay_mary"]
        coll = db["interacoes"]
        coll.find_one()  # ping
        return coll
    except Exception as e:
        st.error(f"❌ Erro ao conectar MongoDB: {e}")
        return None

def _save_json_response_to_mongo(data: dict, *, user: str, personagem: str, modelo: str) -> None:
    """Salva resposta JSON estruturada no MongoDB com cache de conexão."""
    try:
        coll = _mongo()
        if not coll:
            st.warning("⚠️ Credenciais do Mongo ausentes em st.secrets.")
            return
        doc = {
            "usuario": user,
            "personagem": personagem,
            "fala": (data.get("fala") or "").strip(),
            "pensamento": (data.get("pensamento") or "").strip(),
            "acao": (data.get("acao") or "").strip(),
            "meta": (data.get("meta") or "").strip(),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "modelo": modelo,
            "modo_json": True,
        }
        coll.insert_one(doc)
    except Exception as e:
        st.error(f"❌ Erro ao salvar no MongoDB: {e}")

# ====================================================================
# FUNÇÃO CORRIGIDA
# ====================================================================
def render_assistant_bubbles(markdown_text: str) -> None:
    """
    Renderiza respostas da assistente. Se vier JSON válido (schema: fala/pensamento/acao/meta),
    formata; caso contrário, renderiza Markdown normal.
    """
    import html  # garante que html.escape esteja disponível

    if not markdown_text:
        return

    # 1) Tenta JSON estruturado
    try:
        import json as _json
        data = _json.loads(markdown_text)
        if isinstance(data, dict) and ("fala" in data or "pensamento" in data or "acao" in data or "meta" in data):
            fala = str(data.get("fala", "") or "").strip()
            pensamento = str(data.get("pensamento", "") or "").strip()
            acao = str(data.get("acao", "") or "").strip()
            meta = str(data.get("meta", "") or "").strip()

            # Use <br> em vez de inserir quebras reais dentro das aspas
            if fala:
                safe_fala = html.escape(fala).replace("\n", "<br>")
                st.markdown(f"<div class='assistant-paragraph'><b>{safe_fala}</b></div>", unsafe_allow_html=True)

            if pensamento:
                safe_pense = html.escape(pensamento).replace("\n", "<br>")
                st.markdown(f"<div class='assistant-paragraph'><em>{safe_pense}</em></div>", unsafe_allow_html=True)

            if acao:
                safe_acao = html.escape(acao).replace("\n", "<br>")
                st.caption(safe_acao)

            if meta:
                safe_meta = html.escape(meta).replace("\n", "<br>")
                st.caption(safe_meta)

            # Log Mongo (personagem atual) — ignora falhas silenciosamente
            try:
                _user = st.session_state.get("user_name") or st.session_state.get("usuario") or "desconhecido"
                _person = (st.session_state.get("character") or "desconhecida").strip()
                _model = st.session_state.get("model") or st.session_state.get("current_model") or "desconhecido"
                _save_json_response_to_mongo(data, user=_user, personagem=_person, modelo=_model)
            except Exception:
                pass
            return
    except Exception:
        # Se a análise JSON falhar, cai no fallback de Markdown
        pass


    # 2) Fallback: Markdown por parágrafo e blocos de código
    # Este bloco agora é alcançado se o texto não for um JSON válido.
    import re, html
    
    parts = re.split(r"(```[\s\S]*?```)", markdown_text)  # CORREÇÃO: [\s\S] em vez de [\\s\\S]
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            # Mantém bloco de código intacto (pode ter ```python, ```json etc.)
            st.markdown(part)
        else:
            # Quebra por parágrafos (linhas em branco)
            paras = [p.strip() for p in re.split(r"\n\s*\n", part) if p.strip()]
            for p in paras:
                # CORREÇÃO: evitar quebra real dentro das aspas; use <br> ou "  \\n"
                safe = html.escape(p).replace("\n", "<br>")
                st.markdown(f"<div class='assistant-paragraph'>{safe}</div>", unsafe_allow_html=True)


def _user_keys_for_history(user_id: str, character_name: str) -> List[str]:
    ch = (character_name or "").strip().lower()
    primary = f"{user_id}::{ch}"
    if ch == "mary":
        return [primary, user_id]
    return [primary]

def _reload_history(force: bool = False):
    user_id = str(st.session_state["user_id"])
    char = str(st.session_state["character"])
    key = f"{user_id}|{char}|{get_backend()}"
    if not force and st.session_state["history_loaded_for"] == key:
        return
    try:
        keys = _user_keys_for_history(user_id, char)
        docs = get_history_docs_multi(keys, limit=400) or []
        hist: List[Tuple[str, str]] = []

        resposta_key = f"resposta_{char.strip().lower()}"
        for d in docs:
            u = (d.get("mensagem_usuario") or "").strip()
            a = (d.get(resposta_key) or d.get("resposta_mary") or "").strip()
            if u:
                hist.append(("user", u))
            if a:
                hist.append(("assistant", a))
        st.session_state["history"] = hist
        st.session_state["history_loaded_for"] = key
    except Exception as e:
        _safe_error("Não foi possível carregar o histórico.", e)

# ========== Boot da First Message ==========
try:
    user_id = str(st.session_state.get("user_id", "")).strip()
    char    = str(st.session_state.get("character", "")).strip()
    if user_id and char:
        char_key = f"{user_id}::{char.lower()}"
        docs_exist = False
        try:
            existing = get_history_docs(char_key) or []
            docs_exist = len(existing) > 0
        except Exception:
            existing = []
            docs_exist = False

        if not docs_exist:
            try:
                mod = __import__(f"characters.{char.lower()}.persona", fromlist=["get_persona"])
                get_persona = getattr(mod, "get_persona", None)
            except Exception:
                get_persona = None

            if callable(get_persona):
                persona_text, history_boot = get_persona()
                first_msg = next((m.get("content","") for m in (history_boot or [])
                                  if (m.get("role") or "") == "assistant"), "").strip()
                if first_msg:
                    try:
                        save_interaction(char_key, "", first_msg, "boot:first_message")
                    except Exception:
                        pass
except Exception as e:
    _safe_error("Boot da primeira mensagem falhou.", e)

# ========== Troca de thread ==========
_current_active = f"{st.session_state['user_id']}::{str(st.session_state['character']).lower()}"
if st.session_state["_active_key"] != _current_active:
    st.session_state["_active_key"] = _current_active
    st.session_state["history"] = []
    st.session_state["history_loaded_for"] = ""
    _reload_history(force=True)

# ========== Auto-seed: Mary ==========
try:
    _user = str(st.session_state.get("user_id", "")).strip()
    _char = str(st.session_state.get("character", "")).strip().lower()

    if _user and _char == "mary":
        _mary_key = f"{_user}::mary"

        # Carrega fatos existentes
        try:
            f = get_facts(_mary_key) or {}
        except Exception:
            f = {}

        changed = False

        # parceiro_atual
        if not str(f.get("parceiro_atual", "")).strip():
            try:
                set_fact(_mary_key, "parceiro_atual", _user, {"fonte": "auto_seed"})
                changed = True
            except Exception:
                pass

        # casados
        if "casados" not in f:
            try:
                set_fact(_mary_key, "casados", True, {"fonte": "auto_seed"})
                changed = True
            except Exception:
                pass

        # (adicione outras seeds aqui, se houver)

        # marcação de última execução
        if changed:
            try:
                set_fact(
                    _mary_key,
                    "_last_auto_seed",
                    datetime.utcnow().isoformat(),
                    {"fonte": "auto_seed"},
                )
            except Exception:
                pass

except Exception as e:
    _safe_error("Auto-seed Mary: falha inesperada.", e)
