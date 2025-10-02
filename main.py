# main.py
from __future__ import annotations

import sys
from pathlib import Path

# --- assegura que o projeto raiz está no sys.path (Streamlit muda o CWD às vezes) ---
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

# tente importar o registry e mostre erro útil se falhar
try:
    from characters.registry import get_service, list_characters  # noqa: E402
except Exception as e:  # mostra traceback na interface
    import traceback
    st.error("Falha ao importar `characters.registry`.\n\n"
             f"**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
    st.stop()

st.set_page_config(page_title="PERSONAGENS 2025", page_icon="🎭", layout="wide")

st.sidebar.title("Personagem")
char_names = list_characters()
choice = st.sidebar.selectbox("Escolha", char_names, index=char_names.index("Mary") if "Mary" in char_names else 0)

service = get_service(choice)

# desenha o sidebar específico da personagem
service.render_sidebar(st.sidebar)

st.title(service.title)
user = st.text_input("Você:", placeholder="Escreva sua fala/cena aqui…")
model = st.selectbox("Modelo", service.available_models(), index=0)

if st.button("Enviar", type="primary") or (user and st.session_state.get("enter_to_send", False)):
    with st.spinner("Gerando…"):
        try:
            reply = service.reply(user=user, model=model)
        except Exception as e:
            import traceback
            st.error(f"Erro durante a geração:\n\n**{e.__class__.__name__}:** {e}\n\n```\n{traceback.format_exc()}\n```")
        else:
            st.markdown(reply)
