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
from typing import Optional, List, Tuple

import streamlit as st

# ============ Ajuste de path ============
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ============ Gate opcional por senha ============
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


# ============ Boot da página ============
st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="wide")
require_password_if_configured("PERSONAGENS 2025")
st.title("PERSONAGENS 2025")

# ============ Registry de personagens ============
try:
    from characters.registry import get_service, list_characters
except Exception as e:
    st.error("Falha ao importar `characters.registry`.\n\n"
             f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()

# ============ Router de provedores/modelos ============
try:
    from core.service_router import available_providers, list_models
except Exception:
    def available_providers():
        provs = []
        if os.environ.get("OPENROUTER_API_KEY"):
            provs.append(("OpenRouter", True, "OK"))
        else:
            provs.append(("OpenRouter", False, "sem chave"))
        if os.environ.get("TOGETHER_API_KEY"):
            provs.append(("Together", True, "OK"))
        else:
            provs.append(("Together", False, "sem chave"))
        return provs

    def list_models(provider: str | None = None):
        base_or = [
            "deepseek/deepseek-chat-v3-0324",
            "anthropic/claude-3.5-haiku",
            "qwen/qwen3-max",
            "nousresearch/hermes-3-llama-3.1-405b",
        ]
        base_tg = [
            "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
            "together/Qwen/Qwen2.5-72B-Instruct",
            "together/Qwen/QwQ-32B",
        ]
        if provider == "OpenRouter":
            return base_or
        if provider == "Together":
            return base_tg
        return base_or + base_tg

# ============ Database helpers (UI escolhe backend) ============
# Esperado em core/database.py:
#   get_backend() -> 'memory'|'mongo'
#   set_backend(kind: str) -> None
#   ping_db() -> (backend:str, ok:bool, detail:str)
try:
    from core.database import get_backend, set_backend, ping_db
except Exception:
    # Fallback mínimo via env-var (mantém app funcional em Memória)
    def get_backend() -> str:
        return os.environ.get("DB_BACKEND", "memory")

    def set_backend(kind: str) -> None:
        os.environ["DB_BACKEND"] = kind

    def ping_db():
        kind = get_backend()
        if kind == "mongo":
            return kind, False, "core.database sem suporte Mongo carregado"
        return "memory", True, "memória local"

# ============ Carrega secrets → env (OpenRouter/Together/Mongo) ============
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
    }
    for k, v in mapping.items():
        if v and not os.environ.get(k):
            os.environ[k] = str(v)

_load_env_from_secrets()

# ============ Sidebar: Provedores & DB ============
st.sidebar.subheader("🧠 Provedores LLM")
for name, ok, detail in available_providers():
    st.sidebar.write(f"- **{name}**: {'✅ OK' if ok else '❌'} ({detail})")

st.sidebar.markdown("---")
st.sidebar.subheader("🗄️ Banco de Dados")

# Escolha backend
_current_backend = get_backend()
choice_backend = st.sidebar.radio(
    "Backend",
    options=["memory", "mongo"],
    index=(0 if _current_backend != "mongo" else 1),
    format_func=lambda x: "Memória (local)" if x == "memory" else "MongoDB (remoto)",
    horizontal=True,
)
if choice_backend != _current_backend:
    set_backend(choice_backend)
    st.sidebar.success(f"Backend ajustado para **{choice_backend}**.")

# Mostra credenciais Mongo (apenas máscara)
if choice_backend == "mongo":
    mu = os.environ.get("MONGO_USER", "")
    mc = os.environ.get("MONGO_CLUSTER", "")
    st.sidebar.caption(f"Mongo: user=`{mu or '—'}` host=`{mc or '—'}`")

if st.sidebar.button("Testar conexão"):
    kind, ok, detail = ping_db()
    if ok:
        st.sidebar.success(f"{kind}: {detail}")
    else:
        st.sidebar.error(f"{kind}: {detail}")

st.sidebar.markdown("---")

# ============ Estado base ============
st.session_state.setdefault("user_id", "Janio Donisete")
st.session_state.setdefault("character", "Mary")
st.session_state.setdefault("provider", "OpenRouter")  # apenas para UI
# Model será escolhido da lista combinada dos provedores disponíveis
all_models = list_models(None)
default_model = all_models[0] if all_models else "deepseek/deepseek-chat-v3-0324"
st.session_state.setdefault("model", default_model)

# ============ Top controls ============
c1, c2 = st.columns([2, 2])
with c1:
    st.text_input("👤 Usuário", key="user_id", placeholder="Seu nome ou identificador")
with c2:
    char_names = list_characters()
    default_idx = char_names.index("Mary") if "Mary" in char_names else 0
    st.selectbox("🎭 Personagem", char_names, index=default_idx, key="character")

# Modelos: lista unificada (ou por provedor quando quiser segmentar)
st.selectbox("🧠 Modelo", list_models(None), key="model")

# ============ Instancia serviço da personagem ============
try:
    service = get_service(st.session_state["character"])
except Exception as e:
    st.error(f"Falha ao instanciar serviço da personagem: {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()

# Sidebar específico da personagem (se existir)
render_sidebar = getattr(service, "render_sidebar", None)
if callable(render_sidebar):
    try:
        render_sidebar(st.sidebar)
    except Exception as e:
        st.sidebar.error(f"Erro no sidebar de {service.title}:\n\n**{e.__class__.__name__}:** {e}\n\n"
                         f"```\n{traceback.format_exc()}\n```")
else:
    st.sidebar.caption("Sem preferências para esta personagem.")

# ============ Histórico (memória leve na sessão) ============
st.session_state.setdefault("history", [])  # List[Tuple[role, content]]

for role, content in st.session_state["history"]:
    with st.chat_message("user" if role == "user" else "assistant", avatar=("💬" if role == "user" else "💚")):
        st.markdown(content)

# ========= Helpers de chamada segura =========
def _safe_reply_call(_service, *, user: str, model: str, prompt: str) -> str:
    """Tenta service.reply(user, model, prompt), cai em variações."""
    fn = getattr(_service, "reply", None)
    if not callable(fn):
        raise RuntimeError("Service atual não expõe reply().")

    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())

    try:
        if "prompt" in params:
            return fn(user=user, model=model, prompt=prompt)
        # Fallback 1: (user, model) e prompt via estado interno da personagem
        if params == ["user", "model"]:
            return fn(user=user, model=model)
        # Fallback 2: positional
        try:
            return fn(user, model, prompt)
        except TypeError:
            return fn(user, model)
    except Exception:
        raise

# ============ Entrada do chat ============
user_prompt = st.chat_input(f"Fale com {st.session_state['character']}")

# Botão Continuar
cont = st.button("🔁 Continuar", help="Prossegue a cena do ponto atual, sem mudar o local salvo.")

final_prompt: Optional[str] = None
auto_continue = False

if cont and not user_prompt:
    final_prompt = (
        "CONTINUAR: Prossiga a cena exatamente de onde a última resposta parou. "
        "Mantenha LOCAL_ATUAL, personagens presentes e tom. Não resuma; avance ação e diálogo em 1ª pessoa."
    )
    auto_continue = True
elif user_prompt:
    final_prompt = user_prompt

# ============ Ciclo de geração ============
if final_prompt:
    # Render prompt do usuário
    with st.chat_message("user"):
        st.markdown("🔁 **Continuar**" if auto_continue else final_prompt)
    st.session_state["history"].append(("user", "🔁 Continuar" if auto_continue else final_prompt))

    with st.spinner("Gerando…"):
        try:
            text = _safe_reply_call(
                service,
                user=st.session_state["user_id"],
                model=st.session_state["model"],
                prompt=final_prompt,
            )
        except Exception as e:
            text = f"Erro durante a geração:\n\n**{e.__class__.__name__}** — {e}\n\n```\n{traceback.format_exc()}\n```"

    with st.chat_message("assistant", avatar="💚"):
        st.markdown(text)
    st.session_state["history"].append(("assistant", text))
