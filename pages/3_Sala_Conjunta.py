# pages/3_Sala_Conjunta.py

from __future__ import annotations
import streamlit as st
from typing import List, Dict, Tuple

from core.service_router import route_chat_strict
from core.repositories import save_interaction


# =========================
# Helpers de sessão
# =========================

def _get_user_id() -> str:
    uid = str(st.session_state.get("user_id", "") or "").strip()
    return uid or "Visitante"


def _get_joint_key() -> str:
    # chave para salvar no Mongo, se quiser reaproveitar
    return f"{_get_user_id()}::sala_conjunta"


def _init_state():
    if "joint_history" not in st.session_state:
        # lista de mensagens no formato {"role": "user"/"assistant", "content": "..."}
        st.session_state["joint_history"] = []
    if "joint_scene_desc" not in st.session_state:
        st.session_state["joint_scene_desc"] = (
            "Sala íntima, fim de noite; Mary, Nerith, Laura e Adelle reunidas com você, "
            "todas se vendo e se ouvindo."
        )


# =========================
# Engine da Sala Conjunta
# =========================

def _build_system_block(scene_desc: str) -> str:
    return f"""
Você está controlando **QUATRO PERSONAGENS** simultaneamente na mesma cena:

--- PERSONAGENS ---

MARY
- Brasileira, jovem adulta, intensa, emocional e sensual.
- Gosta de intimidade, cumplicidade, carinho e tensão romântica.
- Fala em primeira pessoa, chamando o usuário pelo nome quando fizer sentido.

LAURA
- 30 anos, ruiva, amante cúmplice, elegante e provocante.
- Mistura humor, charme e confiança; adora flertar e provocar.
- Fala em primeira pessoa; tom de "amante confidente".

ADELLE
- Femme fatale estrategista, olhar clínico, perigosa e sedutora.
- Usa linguagem calculada, frases com peso, insinua poder e segredos.
- Nunca é boba: sempre parece estar três passos à frente.

NERITH
- Guerreira de outro mundo, presença física marcante.
- Mistura estranheza (alienígena) com desejo e orgulho.
- Fala em primeira pessoa, com uma certa estranheza cultural em relação aos humanos.

--- CENA ATUAL ---
Todas estão **NO MESMO AMBIENTE**, vendo e ouvindo umas às outras em tempo real.

Descrição da cena (resumo atual):
{scene_desc or "Sala fechada, ambiente íntimo; todas próximas ao usuário."}

--- REGRAS DA SALA CONJUNTA ---

1. Sempre responda em UM ÚNICO BLOCO DE TEXTO, no formato:

Mary:
(texto da Mary)

Laura:
(texto da Laura, ou <<silêncio>>)

Adelle:
(texto da Adelle, ou <<silêncio>>)

Nerith:
(texto da Nerith, ou <<silêncio>>)

2. Se o usuário chamar uma personagem pelo nome (ex.: "Nerith, lembra quando fomos para Elysarix?"):
   - Essa personagem é a PROTAGONISTA da rodada: ela responde com 2–3 parágrafos.
   - As outras só comentam se fizer sentido, com no máximo 1 parágrafo curto.
   - Se não tiver nada relevante a dizer, respondem exatamente com: <<silêncio>>

3. Se o usuário falar com todas ("meninas", "vocês", etc.):
   - Todas podem responder, mas:
       - Escolha 1 protagonista (a que for mais natural pela fala do usuário).
       - As restantes reagem com comentários curtos, ou <<silêncio>>.

4. NÃO repita blocos de instrução como "[CENA COMPARTILHADA]" ou explicações de sistema.
   - O texto de saída deve parecer uma cena de romance/aventura adulta, não um manual.

5. Mantenha **coerência de ambiente**:
   - Se alguém está sentada no sofá, outra pode reagir olhando ou se aproximando, etc.
   - Não mude lugar/tempo de forma brusca; mude só se o usuário pedir.

6. O tom é adulto e sensual, mas foque mais em:
   - química entre personagens,
   - olhares, proximidade, diálogo,
   - tensão emocional e clima.

Responda agora seguindo estritamente o formato:

Mary:
...

Laura:
...

Adelle:
...

Nerith:
...
    """.strip()


def gerar_resposta_conjunta(
    model_id: str,
    scene_desc: str,
    history: List[Dict[str, str]],
    user_msg: str,
    temperature: float = 0.75,
) -> Tuple[str, str, str]:
    """
    Usa um ÚNICO modelo para controlar Mary, Laura, Adelle e Nerith
    na mesma cena compartilhada.
    """
    system_block = _build_system_block(scene_desc)

    messages: List[Dict[str, str]] = []
    messages.append({"role": "system", "content": system_block})

    # Histórico conjunto (limitando para não estourar contexto)
    # Mantém últimos 8 turnos (user+assistant)
    for m in history[-16:]:
        messages.append(m)

    # Mensagem atual do usuário
    messages.append({"role": "user", "content": user_msg})

    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": 1800,
        "temperature": float(temperature),
        "top_p": 0.95,
    }

    data, used_model, provider = route_chat_strict(model_id, payload)
    msg = (data.get("choices", [{}])[0].get("message", {}) or {})
    texto = (msg.get("content", "") or "").strip() or "[sem resposta]"

    return texto, used_model, provider


# =========================
# UI da página
# =========================

_init_state()

st.title("🪩 Sala Conjunta — Mary, Laura, Adelle e Nerith")

user_id = _get_user_id()
joint_key = _get_joint_key()
model_id = st.session_state.get("model") or "deepseek/deepseek-chat-v3-0324"

st.caption(f"Usuário: **{user_id}** · Modelo ativo: `{model_id}`")

# Cena compartilhada (editável)
st.subheader("🪩 Descrição da cena compartilhada")
scene_desc = st.text_area(
    "Descreva o ambiente onde todas estão juntas:",
    key="joint_scene_desc",
    height=100,
)

st.markdown("---")

# Histórico conjunto (chat log)
st.subheader("💥 Interação conjunta")

for m in st.session_state["joint_history"]:
    if m["role"] == "user":
        st.markdown(f"**Você:** {m['content']}")
    else:
        # resposta do conjunto (inclui Mary:/Laura:/Adelle:/Nerith:)
        st.markdown(m["content"])

st.markdown("---")

# Controle de temperatura (opcional)
with st.expander("⚙️ Ajustes da Sala Conjunta", expanded=False):
    temp = st.slider(
        "Temperatura (criatividade)",
        min_value=0.3,
        max_value=1.2,
        value=float(st.session_state.get("joint_temp", 0.75)),
        step=0.05,
    )
    st.session_state["joint_temp"] = float(temp)
else:
    temp = float(st.session_state.get("joint_temp", 0.75))

# Entrada do usuário
user_msg = st.chat_input("Fale com elas (ex.: 'Nerith, lembra quando fomos para Elysarix?')", key="joint_chat_input")

if user_msg:
    # Adiciona turno do usuário no histórico local
    st.session_state["joint_history"].append({"role": "user", "content": user_msg})

    # Chama engine conjunta
    resposta, used_model, provider = gerar_resposta_conjunta(
        model_id=model_id,
        scene_desc=scene_desc,
        history=st.session_state["joint_history"],
        user_msg=user_msg,
        temperature=temp,
    )

    # Adiciona resposta no histórico
    st.session_state["joint_history"].append({"role": "assistant", "content": resposta})

    # Persistência opcional no Mongo (se quiser rastrear a Sala Conjunta)
    try:
        save_interaction(
            joint_key,
            user_msg,
            resposta,
            f"{provider}:{used_model}",
        )
    except Exception:
        pass

    st.rerun()
