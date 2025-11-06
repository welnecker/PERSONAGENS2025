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
    "FAL • BRIA FIBO": {
        "provider": "fal-ai",
        "model": "briaai/FIBO",
        "size": "1024x1024",
    },
    "FAL • FLUX Schnell": {
        "provider": "fal-ai",
        "model": "black-forest-labs/FLUX.1-schnell",
        "size": "1024x1024",
    },
    "FAL • Stable Image Ultra": {
        "provider": "fal-ai",
        "model": "stabilityai/stable-image-ultra",
        "size": "1024x1024",
    },

    # ✅ NOVO MODELO — Flux.1-dev (HuggingFace)
    "HF • FLUX.1-dev": {
        "provider": "huggingface",
        "model": "black-forest-labs/FLUX.1-dev",
        "size": "1024x1024",
    },

    # ✅ OPCIONAL: versão Uncensored via HuggingFace LoRA (se quiser ativar depois)
    # Isto não gera NSFW por si — apenas segue melhor prompts ousados.
    # Pode comentar/descomentar a qualquer momento.
    "HF • FLUX.1-dev (Uncensored LoRA)": {
        "provider": "huggingface",
        "model": "Heartsync/Flux-NSFW-uncensored",
        "base":  "black-forest-labs/FLUX.1-dev",
        "size": "1024x1024",
    },
}


# ======================
# Token / Client
# ======================
def _get_hf_token() -> str:
    tok = (str(st.secrets.get("HUGGINGFACE_API_KEY", "")) or
           str(st.secrets.get("HF_TOKEN", "")) or
           os.environ.get("HUGGINGFACE_API_KEY", "") or
           os.environ.get("HF_TOKEN", ""))
    tok = (tok or "").strip()
    if not tok:
        raise RuntimeError("Defina HUGGINGFACE_API_KEY (ou HF_TOKEN) em st.secrets ou variável de ambiente.")
    return tok

def _hf_client(provider: str) -> InferenceClient:
    # provider é ignorado para HuggingFace
    return InferenceClient(token=_get_hf_token())

# ======================
# Utilitários de Prompt
# ======================
MAX_PROMPT_LEN = 1900

def _squash_spaces(s: str) -> str:
    return " ".join((s or "").split())

def _sanitize_scene(s: str, limit: int = 260) -> str:
    s = (s or "").replace("*", "").replace("`", " ").replace("\n", " ").replace("\r", " ")
    return _squash_spaces(s)[:limit]

def _fit_to_limit(prompt: str, max_len: int = MAX_PROMPT_LEN) -> tuple[str, bool]:
    p = _squash_spaces(prompt)
    if len(p) <= max_len:
        return p, False
    # Estratégia de corte simples para manter a função
    return p[:max_len], True

# ======================
# Blocos de Prompt (Positivos e Negativos)
# ======================
# --- Foco em evitar anomalias e chifres ---
ANATOMY_NEG = (
    "bad anatomy, extra limbs, extra legs, missing legs, cropped feet, deformed, malformed, mutated, "
    "fused fingers, extra fingers, twisted torso, broken spine, dislocated"
)
HORN_NEG = "horns, horn, antlers, antler, head spikes, forehead protrusions, demon horns"
DUPLICATE_NEG = "two people, two girls, duplicate, twin, second person, extra person, clone, copy"

# --- Descritores para sensualidade implícita (NSFW Sutil) ---
SENSUAL_POS = (
    "alluring pose, captivating gaze, soft shadows accentuating curves, luminous skin, "
    "figure partly obscured by silk sheets, form-fitting clothing, wet look, glistening skin, "
    "intimate atmosphere, moody lighting, boudoir photography style"
)
SENSUAL_NEG = (
    "fully clothed, non-sensual, clothed, sfw, chaste, modest"
)

# --- Descritores para SFW (Safe for Work) ---
SFW_POS = "action pose, dynamic stance, heroic, powerful, determined expression"
SFW_NEG = (
    "nsfw, alluring, seductive, intimate, boudoir, suggestive, explicit, sensual"
)

# ===================================================
# PRESETS
# ===================================================
_DEFAULT_PRESETS: Dict[str, Dict[str, str]] = {
    "Nerith • Caçadora": {
        "positive": (
            "high-end comic panel, full-body, bold ink, cel shading, dramatic rimlight, rain and neon; "
            "female dark-elf from Elysarix; blue-slate skin; metallic silver long hair; green predatory eyes; "
            "elongated pointed elven ears; silver sensory tendrils active; solo; "
            "natural contrapposto pose, three-quarter back view"
        ),
        "negative": "romance, couple, kiss, soft framing",
        "style": "gritty noir sci-fi, halftone accents, dynamic angle",
    },
    "Nerith • Boudoir (Sensual)": {
        "positive": (
            "boudoir photography, intimate setting, soft moody lighting, silk sheets; "
            "female dark-elf, blue-slate luminous skin, metallic silver long hair, captivating green eyes; "
            "elongated pointed elven ears; alluring pose on a bed, solo; "
            "body partly covered, accentuating curves, tasteful, artistic"
        ),
        "negative": "cluttered background, harsh lighting, fully clothed",
        "style": "photorealistic, soft focus, high detail, cinematic grain",
    },
    "Nerith • Dominante": {
        "positive": (
            "three-quarter to full body, bold ink, cel shading, dramatic back rimlight; "
            "dark-elf; metallic silver long hair; neon green eyes; dominant posture; "
            "elongated pointed elven ears; tendrils alive; tail-blade raised; solo"
        ),
        "negative": "romance, couple, kiss",
        "style": "cinematic backlight, smoky atmosphere",
    },
}

def _preset_store() -> Dict[str, Dict[str, str]]:
    return st.session_state.setdefault("nerith_comic_user_presets", {})

def get_all_presets() -> Dict[str, Dict[str, str]]:
    merged = dict(_DEFAULT_PRESETS)
    merged.update(_preset_store())
    return merged

def save_user_preset(name: str, data: Dict[str, str]) -> None:
    name = (name or "").strip()
    if not name: return
    store = _preset_store()
    store[name] = {
        "positive": data.get("positive", ""),
        "negative": data.get("negative", ""),
        "style": data.get("style", ""),
    }

def build_prompt_from_preset(
    preset: Dict[str, str],
    scene_desc: str,
    nsfw_on: bool,
) -> str:
    """Constrói o prompt final, ajustando para SFW/NSFW com termos sutis."""
    
    # Base do preset
    pos = (preset.get("positive", "") or "").strip()
    neg = (preset.get("negative", "") or "").strip()
    sty = (preset.get("style", "") or "").strip()

    # Adiciona blocos com base no toggle NSFW
    if nsfw_on:
        pos += " " + SENSUAL_POS
        neg += " " + SENSUAL_NEG
    else:
        pos += " " + SFW_POS
        neg += " " + SFW_NEG

    # Consolida todos os negativos
    final_neg = ", ".join(filter(None, [
        neg,
        ANATOMY_NEG,
        HORN_NEG,
        DUPLICATE_NEG,
    ]))

    # Monta o prompt final
    parts = [
        _squash_spaces(pos),
        (f"style: {sty}" if sty else ""),
        f"Scene: {scene_desc}",
        f"NEGATIVE: ({_squash_spaces(final_neg)})",
    ]
    return " ".join(filter(None, parts))

# ===================================================
# UI – Botão Principal
# ===================================================
def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str],
    *,
    model_name: str = "briaai/FIBO",
    size: str = "1024x1024",
    title: str = "🎞️ Quadrinho (beta)",
    ui=None,
    key_prefix: str = "",
) -> None:

    ui = ui or st
    key_prefix = (key_prefix or "nerith_comics").replace(" ", "_")

    try:
        ui.markdown(f"### {title}")

        # --- Seleção de Modelo ---
        prov_key = ui.selectbox(
            "Modelo",
            options=list(PROVIDERS.keys()),
            index=0,
            key=f"{key_prefix}_model_sel"
        )
        cfg = PROVIDERS.get(prov_key, {})
        model_name = cfg.get("model", model_name)
        provider_name = cfg.get("provider", "fal-ai")
        size = cfg.get("size", size)

        # --- Seleção e Edição de Preset ---
        all_presets = get_all_presets()
        preset_names = list(all_presets.keys())
        # Tenta selecionar o preset "Boudoir" por padrão se existir
        default_idx = preset_names.index("Nerith • Boudoir (Sensual)") if "Nerith • Boudoir (Sensual)" in preset_names else 0
        sel_preset = ui.selectbox(
            "Preset de Cena",
            options=preset_names,
            index=default_idx,
            key=f"{key_prefix}_preset_sel",
        )
        cur = dict(all_presets.get(sel_preset, {}))

        with ui.expander("✏️ Ajustar preset (opcional)", expanded=False):
            cur["positive"] = ui.text_area("Positive Prompt", value=cur.get("positive", ""), height=140, key=f"{key_prefix}_preset_pos")
            cur["negative"] = ui.text_area("Negative Prompt", value=cur.get("negative", ""), height=90, key=f"{key_prefix}_preset_neg")
            cur["style"] = ui.text_input("Estilo Extra", value=cur.get("style", ""), key=f"{key_prefix}_preset_style")

            col1, col2 = ui.columns([3, 1])
            new_name = col1.text_input("Salvar como", value=f"{sel_preset} (cópia)", key=f"{key_prefix}_preset_newname")
            if col2.button("💾 Salvar preset", key=f"{key_prefix}_savepreset"):
                save_user_preset(new_name, cur)
                ui.success(f"Preset salvo: {new_name}")
                st.rerun()

        # --- Controles de Geração ---
        A, B = ui.columns([3, 1])
        nsfw_on = A.toggle("Liberar Sensualidade (Implícito)", value=False, key=f"{key_prefix}_nsfw_toggle")
        gen = B.button("Gerar", use_container_width=True, key=f"{key_prefix}_gen_btn")

        if not gen:
            return

        # --- Construção do Prompt ---
        raw_scene = scene_text_provider() or "Nerith em um quarto com iluminação suave."
        scene_desc = _sanitize_scene(raw_scene, limit=220)

        prompt = build_prompt_from_preset(cur, scene_desc, nsfw_on)
        prompt, reduced = _fit_to_limit(prompt, MAX_PROMPT_LEN)
        if reduced:
            ui.warning("⚠️ Prompt longo demais, foi compactado.")
        
        with ui.expander("Ver prompt final", expanded=False):
            st.code(prompt, language=None)

        # --- Conversão de Tamanho ---
        try:
            width, height = map(int, size.lower().split('x'))
        except Exception:
            width, height = 1024, 1024

        # --- Geração da Imagem ---
        client = _hf_client(provider_name)
        with st.spinner("Gerando painel…"):
            img_data = client.text_to_image(model=model_name, prompt=prompt, width=width, height=height)

        if isinstance(img_data, Image.Image):
            img = img_data
        else:
            img = Image.open(io.BytesIO(img_data))

        # --- Exibição e Download ---
        ui.image(img, caption="Painel gerado", use_column_width=True)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        ui.download_button("⬇️ Baixar PNG", data=buf.getvalue(), file_name="nerith_quadrinho.png", mime="image/png", key=f"{key_prefix}_dl_btn")

    except Exception as e:
        ui.error(f"Falha na geração de quadrinhos: {e}")
        st.exception(e)
