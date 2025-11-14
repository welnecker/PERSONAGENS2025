# pages/3_Sala_Conjunta.py
from __future__ import annotations

import streamlit as st
from typing import List, Dict, Tuple
import inspect  # <- para inspecionar assinatura de reply()

# ---- Imports internos do projeto ----
from characters.registry import get_service, list_characters
from core.repositories import get_history_docs
from core.service_router import list_models  # só para montar lista de modelos

# =============== CONFIG PÁGINA ===============
st.set_page_config(
    page_title="Sala Conjunta – Personagens 2025",
    page_icon="👥",
    layout="centered",
)

st.title("👥 Sala Conjunta – Mary, Nerith, Laura e Adelle")
st.caption(
    "Experimento de **meta-cena**: você fala uma vez e cada personagem responde "
    "a partir da sua própria memória no Mongo. A confusão é proposital. 😈"
)

st.markdown("---")

# =============== CONTROLES BÁSICOS ===============
# Usuário (mesmo esquema do main.py)
user_id = st.text_input("👤 Usuário", value=st.session_state.get("user_id", "Janio Donisete")).strip()
if not user_id:
    user_id = "Janio Donisete"
st.session_state["user_id"] = user_id  # manter coerência com main.py

# Lista oficial de personagens a partir do registry
all_chars = list_characters()  # deve retornar ["Mary", "Laura", "Adelle", "Nerith"]
# Vamos filtrar só as que nos interessam aqui (caso você adicione outras no futuro)
target_chars = [c for c in all_chars if c in ["Mary", "Laura", "Adelle", "Nerith"]]

default_sel = target_chars[:]  # todas marcadas
chars_sel = st.multiselect(
    "🎭 Personagens ativas nesta cena conjunta",
    options=target_chars,
    default=default_sel,
)
if not chars_sel:
    st.info("Selecione pelo menos uma personagem para continuar.")
    st.stop()

# =============== MODELOS ===============
FORCED_MODELS = [
    "deepseek/deepseek-chat-v3-0324",
    "anthropic/claude-3.5-haiku",
    "qwen/qwen3-max",
    "together/meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
    "together/Qwen/Qwen2.5-72B-Instruct",
]

try:
    router_models = list_models(None) or []
except Exception:
    router_models = []

models_all = list(dict.fromkeys(router_models + FORCED_MODELS))
if not models_all:
    models_all = FORCED_MODELS

st.session_state.setdefault("model", models_all[0])
model_id = st.selectbox(
    "🧠 Modelo para todas as respostas",
    options=models_all,
    index=models_all.index(st.session_state["model"]) if st.session_state["model"] in models_all else 0,
)
st.session_state["model"] = model_id

st.markdown("---")

# =============== HELPER COMPATÍVEL COM TODOS OS SERVICES ===============
def _safe_reply_call(_service, *, user: str, model: str, prompt: str) -> str:
    """
    Replica a lógica do main.py:
    - grava prompt em st.session_state["prompt"]
    - chama reply() aceitando variações na assinatura:
      (user, model, prompt) OU (user, model) etc.
    """
    st.session_state["prompt"] = prompt
    fn = getattr(_service, "reply", None)
    if not callable(fn):
        raise RuntimeError("Service atual não expõe reply().")

    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())

    # Caso mais moderno: reply(self, user, model, prompt)
    if "prompt" in params:
        return fn(user=user, model=model, prompt=prompt)

    # Caso antigo: reply(self, user, model)
    if params == ["user", "model"]:
        return fn(user=user, model=model)

    # Fallback posicional
    try:
        return fn(user, model, prompt)
    except TypeError:
        return fn(user, model)

# =============== HISTÓRICO (LEITURA DO MONGO) ===============
st.subheader("📜 Últimos turnos de cada personagem")

for name in chars_sel:
    user_key = f"{user_id}::{name.lower()}"
    st.markdown(f"#### 💚 {name}")
    try:
        docs = get_history_docs(user_key) or []
    except Exception as e:
        st.error(f"Erro ao ler histórico de {name}: {e}")
        continue

    if not docs:
        st.caption("_Sem histórico salvo para esta dupla ainda._")
        st.markdown("---")
        continue

    # Mostra só os últimos 6 turnos (user + character)
    for d in docs[-6:]:
        u = (d.get("mensagem_usuario") or "").strip()
        a = (
            d.get(f"resposta_{name.lower()}")
            or d.get("resposta")
            or d.get("assistant")
            or ""
        ).strip()
        if u:
            with st.chat_message("user", avatar="💬"):
                st.markdown(u)
        if a:
            with st.chat_message("assistant", avatar="💚"):
                st.markdown(a)

    st.markdown("---")

# ===== Cena compartilhada =====
scene_desc = st.text_input(
    "🪩 Descrição da cena compartilhada",
    value=st.session_state.get(
        "joint_scene_desc",
        "Sala íntima, fim de noite; Mary, Nerith, Laura e Adelle reunidas com você, todas se vendo e se ouvindo."
    ),
    key="joint_scene_desc_input",   # <<< evita ID duplicado
)

st.session_state["joint_scene_desc"] = scene_desc
st.session_state.setdefault("joint_last_round", {})

# =============== CHAT CONJUNTO ===============
st.subheader("💥 Interação conjunta")

placeholder = "Fale algo que todas devam reagir…"
user_msg = st.chat_input(placeholder)

# ===== Cena compartilhada =====
scene_desc = st.text_input(
    "🪩 Descrição da cena compartilhada",
    value=st.session_state.get(
        "joint_scene_desc",
        "Sala íntima, fim de noite; Mary, Nerith, Laura e Adelle reunidas com você, todas se vendo e se ouvindo."
    ),
)
st.session_state["joint_scene_desc"] = scene_desc
st.session_state.setdefault("joint_last_round", {})


if user_msg:
    # mostra sua fala uma vez
    with st.chat_message("user", avatar="💬"):
        st.markdown(user_msg)

    # detectar se você chamou alguém pelo nome
    normalized = user_msg.lower()
    focus_name = None
    for name in chars_sel:
        if name.lower() in normalized:
            focus_name = name
            break

    last_round: Dict[str, str] = st.session_state.get("joint_last_round", {}) or {}
    new_round: Dict[str, str] = {}

    combined_blocks: List[str] = []

    for name in chars_sel:
        try:
            service = get_service(name)
        except Exception as e:
            combined_blocks.append(f"**{name}**\n\n❌ Falha ao instanciar serviço: {e}")
            continue

        # outras personagens vistas por esta
        others = [n for n in chars_sel if n != name]
        others_snips = []
        for other in others:
            prev = (last_round.get(other) or "").strip()
            if prev:
                prev_short = prev.replace("\n", " ")
                if len(prev_short) > 260:
                    prev_short = prev_short[:260] + "..."
                others_snips.append(f"- {other}: {prev_short}")

        if others_snips:
            others_block = (
                "Na rodada anterior desta cena conjunta, as outras personagens reagiram assim:\n"
                + "\n".join(others_snips)
            )
        else:
            others_block = (
                "Esta é a primeira rodada da cena conjunta; assuma apenas que todas estão presentes e ouvindo você."
            )

        # regra de foco: quem foi chamada fala mais, as outras comentam ou ficam em silêncio
        if focus_name is None:
            role_hint = (
                "O usuário não chamou nenhuma personagem específica. "
                "Responda em 1–2 parágrafos, interagindo com todas, "
                "mas deixe espaço para as outras reagirem."
            )
        elif name == focus_name:
            role_hint = (
                f"O usuário se dirigiu principalmente a VOCÊ ({name}). "
                "Você é a protagonista desta resposta: responda em 2–3 parágrafos, "
                "puxando a cena e reagindo ao que lembrar das últimas interações."
            )
        else:
            role_hint = (
                f"O usuário falou principalmente com {focus_name}. "
                "Você só reage se fizer sentido, como um comentário lateral. "
                "Se reagir, use no máximo 1 parágrafo curto. "
                "Se não tiver nada relevante a dizer agora, responda APENAS com a string literal '<<silêncio>>'."
            )

        joint_prompt = (
            "[CENA COMPARTILHADA]\n"
            f"{scene_desc}\n\n"
            f"Você é {name} e está na mesma sala que "
            f"{', '.join(others)} e o usuário {user_id}. "
            "Todas se veem e se ouvem em tempo real. "
            "Responda como se estivesse no MESMO ambiente que elas.\n\n"
            + role_hint
            + "\n\n"
            + others_block
            + "\n\n[FALA DO USUÁRIO AGORA]\n"
            + user_msg
        )

        with st.spinner(f"Gerando resposta de {name}…"):
            try:
                txt = _safe_reply_call(
                    service,
                    user=user_id,
                    model=model_id,
                    prompt=joint_prompt,
                )
            except Exception as e:
                txt = f"❌ Erro ao gerar resposta de {name}: {e}"

        txt_clean = (txt or "").strip()
        new_round[name] = txt_clean

        # ignora quem escolheu ficar em silêncio
        if txt_clean == "<<silêncio>>":
            continue

        # bloco de texto dessa personagem dentro da resposta única
        combined_blocks.append(f"**{name}**\n\n{txt_clean}")

    st.session_state["joint_last_round"] = new_round

    # agora sim: UMA resposta única, misturando tudo
    with st.chat_message("assistant", avatar="💚"):
        if combined_blocks:
            st.markdown("\n\n---\n\n".join(combined_blocks))
        else:
            st.markdown("_Todas ficaram em silêncio por enquanto…_")
