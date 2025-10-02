# main.py
from __future__ import annotations

import os
import sys
from pathlib import Path
import streamlit as st

# ======================================================================
# Bootstrap de import (garante que o pacote local esteja no sys.path)
# ======================================================================
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="wide")

# ======================================================================
# Imports defensivos
# ======================================================================
try:
    from characters.registry import get_service, list_characters  # noqa: E402
except Exception as e:
    import traceback
    st.error(
        "Falha ao importar `characters.registry`.\n\n"
        f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```"
    )
    st.stop()

try:
    from core.service_router import available_providers, list_models  # noqa: E402
except Exception as e:
    import traceback
    st.error(
        "Falha ao importar `core.service_router`.\n\n"
        f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```"
    )
    st.stop()

# ======================================================================
# Sidebar: Personagem, Provedor, Modelo, User ID, Preferências por personagem
# ======================================================================
st.sidebar.title("Configuração")

# Personagem
char_names = list_characters()
if not char_names:
    st.sidebar.error("Nenhuma personagem registrada em `characters/`.")
    st.stop()

default_idx = char_names.index("Mary") if "Mary" in char_names else 0
choice = st.sidebar.selectbox("Personagem", char_names, index=default_idx)

# Instancia o serviço da personagem escolhida
service = get_service(choice)

# User ID (persistente na sessão)
if "user_id" not in st.session_state:
    st.session_state.user_id = os.getenv("DEFAULT_USER_ID", "anon")

st.sidebar.text_input(
    "User ID",
    key="user_id",
    placeholder="ex.: anon, user123...",
    help="Identificador lógico para histórico/memória desta personagem.",
)

# Provedor
prov_opts = available_providers()
if not prov_opts:
    # fallback visual para não quebrar a UI
    prov_opts = ["openrouter", "together"]

# Seleção automática baseada nas chaves presentes
def _default_provider_index(opts: list[str]) -> int:
    if "openrouter" in opts and os.getenv("OPENROUTER_API_KEY"):
        return opts.index("openrouter")
    if "together" in opts and os.getenv("TOGETHER_API_KEY"):
        return opts.index("together")
    return 0

prov_index = _default_provider_index(prov_opts)
provider = st.sidebar.selectbox("Provedor", prov_opts, index=prov_index)

# Modelos por provedor
models = []
try:
    models = list_models(provider) or []
except Exception:
    models = []

if not models:
    st.sidebar.warning(
        "Nenhum modelo listado para este provedor.\n\n"
        "• Configure `OPENROUTER_API_KEY` e/ou `TOGETHER_API_KEY`.\n"
        "• Opcional: defina `OPENROUTER_MODELS` / `TOGETHER_MODELS`."
    )
    models = ["<nenhum modelo>"]

model = st.sidebar.selectbox("Modelo", models, index=0)

# Sidebar específico da personagem (opcional)
render_sidebar = getattr(service, "render_sidebar", None)
if callable(render_sidebar):
    try:
        render_sidebar(st.sidebar)
    except Exception as e:
        import traceback
        st.sidebar.error(
            f"Erro no sidebar de {service.title}:\n\n"
            f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```"
        )
else:
    st.sidebar.caption("Sem preferências específicas para esta personagem.")

# ======================================================================
# Corpo: Mensagem e ação
# ======================================================================
st.title(service.title)

prompt = st.text_area("Mensagem", height=220, placeholder="Escreva sua fala/cena aqui…")

col_send, col_clear = st.columns([1, 1])

with col_send:
    if st.button("Enviar", type="primary", use_container_width=True):
        if not prompt.strip():
            st.warning("Digite uma mensagem antes de enviar.")
        elif model == "<nenhum modelo>":
            st.error("Selecione um modelo válido. Verifique as chaves do provedor.")
        else:
            with st.spinner("Gerando…"):
                try:
                    # IMPORTANTE: a assinatura usada pelos serviços
                    # reply(user=<ID>, model=<MODEL>, provider=<PROV>, prompt=<TEXTO>)
                    reply = service.reply(
                        user=st.session_state.user_id,
                        model=model,
                        provider=provider,
                        prompt=prompt,
                    )
                    st.markdown(reply)
                except Exception as e:
                    import traceback
                    st.error(
                        "Erro durante a geração:\n\n"
                        f"**{e.__class__.__name__}:** {e}\n\n"
                        f"```\n{traceback.format_exc()}\n```"
                    )

with col_clear:
    if st.button("Limpar entrada", use_container_width=True):
        st.experimental_rerun()
