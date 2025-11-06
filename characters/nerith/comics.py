# characters/nerith/comics.py
from __future__ import annotations
import os, io, re
from typing import Callable, List, Dict, Tuple
from PIL import Image
from huggingface_hub import InferenceClient
import streamlit as st

# ======================
# Config / Providers
# ======================
PROVIDERS: Dict[str, Dict[str, str]] = {
    "HF • FLUX.1-dev": {
        "provider": "huggingface",
        "model": "black-forest-labs/FLUX.1-dev",
        "size": "1024x1024",
    },
    "FAL • Stable Image Ultra": {
        "provider": "fal-ai",
        "model": "stabilityai/stable-image-ultra",
        "size": "1024x1024",
    },
}

# ======================
# Token / Client
# ======================
def _get_hf_token() -> str:
    tok = (str(st.secrets.get("HUGGINGFACE_API_KEY", "")) or str(st.secrets.get("HF_TOKEN", "")))
    if not tok:
        tok = os.environ.get("HUGGINGFACE_API_KEY", "") or os.environ.get("HF_TOKEN", "")
    if not (tok or "").strip():
        raise RuntimeError("Defina HUGGINGFACE_API_KEY (ou HF_TOKEN) em st.secrets ou variável de ambiente.")
    return tok.strip()

def _hf_client() -> InferenceClient:
    return InferenceClient(token=_get_hf_token())

# ======================
# Utilitários de Prompt
# ======================
MAX_PROMPT_LEN = 1800

def _squash_spaces(s: str) -> str:
    return " ".join((s or "").split())

def _fit_to_limit(text: str, max_len: int = MAX_PROMPT_LEN) -> str:
    t = _squash_spaces(text or "")
    return t[:max_len] if len(t) > max_len else t

# ======================
# Blocos de Prompt (Positivos e Negativos)
# ======================
ANATOMY_NEG = "bad anatomy, deformed, mutated, malformed, dislocated, broken spine, twisted torso, extra limbs, extra fingers, fused fingers, missing fingers, missing legs, cropped feet"
DUPLICATE_NEG = "two people, two girls, duplicate, twin, second person, extra person, clone, copy, siamese, overlapping bodies"
HORN_NEG = "horns, horn, antlers, head spikes, forehead protrusions, demon horns, ram horns, goat horns"
TAIL_POS = "a single curved blade-tail with visible base at the lower back (sacrum), clearly not a limb"
TAIL_NEG = "phallic tail, penis-like tail, tail shaped like a limb, tail fused to leg, detached tail, tail intersecting legs"
FACE_POS = "face like a young Sophia Loren, high cheekbones, almond-shaped captivating eyes, full lips, confident expression"
BODY_POS = "athletic hourglass figure, toned yet feminine, full firm natural teardrop breasts, flat defined abdomen, narrow waist, round high-set glutes"
BODY_NEG = "balloon breasts, sphere boobs, torpedo breasts, implants, uneven breasts, collapsed chest, distorted abdomen, square butt, exaggerated butt, wasp waist"
SFW_NEG = "explicit, pornographic, sexual, nude, naked"
SENSUAL_POS = "body draped in shadow and light, skin glistening under soft light, form-fitting silk robe partly open, lounging seductively, intimate atmosphere, alluring posture, moody soft shadows, implicit sensuality"
SENSUAL_NEG = "fully clothed, modest, chaste, asexual, text, watermark, signature"

# ===================================================
# PRESETS
# ===================================================
_DEFAULT_PRESETS: Dict[str, Dict[str, str]] = {
    "Nerith • Boudoir (Sophia Face)": {
        "positive": f"({FACE_POS}), female dark-elf, blue-slate luminous skin, metallic silver long hair, piercing green eyes, elongated pointed elven ears (no horns), solo subject, {BODY_POS}, {TAIL_POS}",
        "negative": ", ".join([HORN_NEG, DUPLICATE_NEG, ANATOMY_NEG, BODY_NEG, TAIL_NEG]),
        "style": f"masterpiece comic art, bold ink outlines, cel shading, dramatic lighting, {SENSUAL_POS}",
    },
    "Nerith • Caçadora (Sophia Face)": {
        "positive": f"({FACE_POS}), female dark-elf, blue-slate skin, metallic silver hair, green eyes, elongated pointed elven ears (no horns), solo, {BODY_POS}, {TAIL_POS}",
        "negative": ", ".join([HORN_NEG, DUPLICATE_NEG, ANATOMY_NEG, BODY_NEG, TAIL_NEG]),
        "style": "masterpiece comic art, gritty noir sci-fi, bold ink, cel shading, dramatic rimlight, rain and neon, dynamic angle",
    },
}

def _preset_store() -> Dict[str, Dict[str, str]]:
    return st.session_state.setdefault("nerith_comic_user_presets", {})

def get_all_presets() -> Dict[str, Dict[str, str]]:
    return {**_DEFAULT_PRESETS, **_preset_store()}

# ======================
# Construtor de Prompt
# ======================
def build_prompts(preset: Dict[str, str], nsfw_on: bool, framing: str, angle: str, pose_details: str, env_details: str) -> Tuple[str, str]:
    base_pos = preset.get("positive", "")
    base_neg = preset.get("negative", "")
    style = preset.get("style", "")

    # Adiciona detalhes de enquadramento e ângulo
    details_pos = f"{framing}, {angle}, {pose_details}"
    
    # Adiciona detalhes do ambiente à cena
    scene_desc = env_details

    if nsfw_on:
        final_neg = f"{base_neg}, {SENSUAL_NEG}"
        final_style = style # O estilo já contém os descritores sensuais
    else:
        final_neg = f"{base_neg}, {SFW_NEG}, {SENSUAL_POS}"
        final_style = "masterpiece comic art, dynamic action pose, confident stance, cinematic lighting"

    prompt = _fit_to_limit(f"{base_pos}, {details_pos}, style: {final_style}, Scene: {scene_desc}")
    negative_prompt = _fit_to_limit(final_neg)
    
    return prompt, negative_prompt

# ===================================================
# UI – Botão Principal
# ===================================================
def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str], # Mantido para compatibilidade, mas não usado ativamente
    *,
    title: str = "🎞️ Diretor de Arte (Nerith)",
    ui=None,
    key_prefix: str = "",
) -> None:
    ui = ui or st
    key_prefix = (key_prefix or "nerith_comics").replace(" ", "_")

    try:
        ui.markdown(f"### {title}")

        # --- Seleção de Modelo e Preset ---
        c1, c2 = ui.columns(2)
        prov_key = c1.selectbox("Modelo", options=list(PROVIDERS.keys()), index=0, key=f"{key_prefix}_model_sel")
        cfg = PROVIDERS.get(prov_key, {})
        model_name, size = cfg.get("model"), cfg.get("size")

        all_presets = get_all_presets()
        sel_preset = c2.selectbox("Preset de Estilo", options=list(all_presets.keys()), index=0, key=f"{key_prefix}_preset_sel")
        cur = dict(all_presets.get(sel_preset, {}))

        # --- Controles de Direção de Arte ---
        ui.markdown("---")
        ui.subheader("Direção da Cena")

        col_framing, col_angle = ui.columns(2)
        framing_map = {
            "Retrato (close-up)": "close-up shot, portrait",
            "Meio corpo (medium shot)": "medium shot, cowboy shot, waist up",
            "Corpo inteiro (full body)": "full body shot, full length",
        }
        framing_choice = col_framing.selectbox("Enquadramento", options=list(framing_map.keys()), index=2, key=f"{key_prefix}_framing")
        framing = framing_map[framing_choice]

        angle_map = {
            "De frente": "front view, facing the viewer",
            "De lado": "side view, profile shot",
            "De costas": "from behind, back view",
            "Três quartos": "three-quarter view",
        }
        angle_choice = col_angle.selectbox("Ângulo", options=list(angle_map.keys()), index=3, key=f"{key_prefix}_angle")
        angle = angle_map[angle_choice]

        with ui.expander("Direção de Arte (Opcional)"):
            pose_details = st.text_input("Detalhes da Pose e Ação", placeholder="Ex: olhando por cima do ombro, segurando uma taça...", key=f"{key_prefix}_pose_details")
            env_details = st.text_input("Detalhes do Ambiente", placeholder="Ex: em uma varanda com vista para a cidade cyberpunk...", key=f"{key_prefix}_env_details")

        # --- Geração ---
        ui.markdown("---")
        nsfw_on = ui.toggle("Liberar sensualidade implícita", value=True, key=f"{key_prefix}_nsfw_toggle", help="Usa prompts de arte sensual para guiar o modelo sem violar as regras da API.")
        gen = ui.button("Gerar Painel", use_container_width=True, key=f"{key_prefix}_gen_btn")

        if not gen:
            return

        # --- Construção do Prompt ---
        prompt, negative_prompt = build_prompts(cur, nsfw_on, framing, angle, pose_details, env_details)

        with ui.expander("Ver prompts finais", expanded=False):
            st.markdown("**Prompt Positivo:**"); st.code(prompt, language=None)
            st.markdown("**Prompt Negativo:**"); st.code(negative_prompt, language=None)

        # --- Geração da Imagem ---
        width, height = map(int, str(size).lower().split("x"))
        client = _hf_client()
        with st.spinner("Gerando painel…"):
            img_data = client.text_to_image(model=model_name, prompt=prompt, negative_prompt=negative_prompt, width=width, height=height)

        img = Image.open(io.BytesIO(img_data)) if isinstance(img_data, bytes) else img_data

        # --- Exibição e Download ---
        ui.image(img, caption=f"Painel gerado com o preset '{sel_preset}'", use_column_width=True)
        buf = io.BytesIO(); buf.name = 'nerith_quadrinho.png'
        img.save(buf, "PNG")
        ui.download_button("⬇️ Baixar PNG", data=buf, file_name=buf.name, mime="image/png", key=f"{key_prefix}_dl_btn")

    except Exception as e:
        ui.error(f"Falha na geração de quadrinhos: {e}")
        st.exception(e)
