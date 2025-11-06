# characters/nerith/comics.py
from __future__ import annotations
import os, io
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
    # ✅ Hugging Face direto (recomendado p/ qualidade + controle de prompt)
    "HF • FLUX.1-dev": {
        "provider": "huggingface",
        "model": "black-forest-labs/FLUX.1-dev",
        "size": "1024x1024",
    },
    # ⚠️ Opcional/experimental: alguns endpoints HF podem não suportar LoRA via Inference API.
    # Se falhar, use local/diffusers. Mantemos aqui para quando o provedor habilitar.
    "HF • FLUX.1-dev (Uncensored LoRA)": {
        "provider": "huggingface",
        "model": "Heartsync/Flux-NSFW-uncensored",   # LoRA repo
        "base":  "black-forest-labs/FLUX.1-dev",     # informativo
        "size": "1024x1024",
    },
}

# ======================
# Token / Client
# ======================
def _get_hf_token() -> str:
    tok = (
        str(st.secrets.get("HUGGINGFACE_API_KEY", "")) or
        str(st.secrets.get("HF_TOKEN", "")) or
        os.environ.get("HUGGINGFACE_API_KEY", "") or
        os.environ.get("HF_TOKEN", "")
    )
    tok = (tok or "").strip()
    if not tok:
        raise RuntimeError("Defina HUGGINGFACE_API_KEY (ou HF_TOKEN) em st.secrets ou variável de ambiente.")
    return tok

def _hf_client(_provider_name: str = "") -> InferenceClient:
    # O InferenceClient não usa 'provider' explicitamente — o endpoint é inferido pelo modelo.
    return InferenceClient(token=_get_hf_token())

# ======================
# Limites / utilitários de prompt
# ======================
MAX_PROMPT_LEN = 1900  # margem segura (endpoint costuma limitar a 2000)

def _squash_spaces(s: str) -> str:
    return " ".join((s or "").split())

def _sanitize_scene(s: str, limit: int = 240) -> str:
    s = (s or "").replace("*", " ").replace("`", " ").replace("\n", " ").replace("\r", " ")
    return _squash_spaces(s)[:limit]

def _fit_to_limit(text: str, max_len: int = MAX_PROMPT_LEN) -> Tuple[str, bool]:
    """Corta de forma segura mantendo intenção."""
    t = _squash_spaces(text or "")
    if len(t) <= max_len:
        return t, False
    # Estratégia: cortar do fim — mantemos início (descritores principais) e a cena
    return t[:max_len], True

# ======================
# Blocos de Prompt (Positivos e Negativos)
# ======================
# — Evitar anomalias e duplicações —
ANATOMY_NEG = (
    "bad anatomy, deformed, mutated, malformed, dislocated, broken spine, twisted torso, "
    "extra limbs, extra fingers, fused fingers, missing fingers, missing legs, cropped feet"
)
DUPLICATE_NEG = (
    "two people, two girls, duplicate, twin, second person, extra person, clone, copy, siamese, overlapping bodies"
)

# — Orelhas SÓ pontudas; sem chifres —
HORN_NEG = "horns, horn, antlers, head spikes, forehead protrusions, demon horns"

# — Cauda-lâmina (evitar confusão com membro/perna) —
TAIL_POS = (
    "a single curved blade-tail with visible base attached at the lower back (sacrum), "
    "emerging above the gluteal crease, clearly not a limb, not phallic"
)
TAIL_NEG = (
    "phallic tail, penis-like tail, tail shaped like a limb, tail fused to leg, detached tail, floating tail, "
    "tail intersecting legs, tail clipping body"
)

# — Pose segura / câmera —
POSE_POS = (
    "natural contrapposto, spine neutral, torso twist <= 30 degrees, head turn <= 30 degrees, "
    "shoulders and hips aligned, relaxed scapula, weight on rear leg, "
    "three-quarter back view, eye-level to low-angle"
)
POSE_NEG = "extreme twist, broken neck, hyperrotation, contortionist pose, scoliosis pose, misaligned hips"

# — Corpo (formas coerentes — seios/quadril/coxa/glúteo) —
BREAST_POS = (
    "full firm natural teardrop breasts proportional to athletic frame, smooth upper pole, subtle lower curve"
)
TORSO_GLU_POS = (
    "flat to slightly defined abdomen, narrow waist, wide pelvis, round high-set glutes with clear underglute line"
)
THIGHS_POS = "strong thighs with smooth quad curves and natural knee structure"
SHAPE_NEG = (
    "balloon breasts, sphere boobs, torpedo breasts, implants sphere, uneven breasts, misaligned nipples, "
    "collapsed chest, unnatural cleavage, dislocated breast, distorted abdomen, misshapen waist, "
    "collapsed butt, square butt, exaggerated butt, overinflated thighs, disproportionate thighs, wasp waist"
)

# — SFW / Sensual —
SFW_POS  = "dynamic action pose, confident stance, cinematic lighting"
SFW_NEG  = "explicit, pornographic"
SENS_POS = "alluring posture, moody soft shadows, wet glossy skin highlights, implicit sensuality"
SENS_NEG = "text, watermark, signature"

# ===================================================
# PRESETS (originais + do usuário)
# ===================================================
_DEFAULT_PRESETS: Dict[str, Dict[str, str]] = {
    "Nerith • Caçadora": {
        "positive": (
            "high-end comic panel, full body, bold ink, cel shading, dramatic rimlight, rain and neon; "
            "female dark-elf from Elysarix; blue-slate luminous skin; metallic silver long hair; "
            "predatory green eyes; elongated pointed elven ears (no horns); "
            "silver sensory tendrils active; solo subject; "
            f"{TAIL_POS}; {POSE_POS}; {BREAST_POS}; {TORSO_GLU_POS}; {THIGHS_POS}"
        ),
        "negative": ", ".join([DUPLICATE_NEG, TAIL_NEG, POSE_NEG, SHAPE_NEG, HORN_NEG]),
        "style": "gritty noir sci-fi, halftone accents, dynamic angle",
    },
    "Nerith • Dominante": {
        "positive": (
            "three-quarter to full body, bold ink, cel shading, dramatic back rimlight; "
            "dark-elf; metallic silver long hair; neon green eyes; elongated pointed elven ears (no horns); "
            "dominant confident posture; silver tendrils alive; tail-blade half raised; solo; "
            f"{POSE_POS}; {BREAST_POS}; {TORSO_GLU_POS}; {THIGHS_POS}"
        ),
        "negative": ", ".join([DUPLICATE_NEG, TAIL_NEG, POSE_NEG, SHAPE_NEG, HORN_NEG]),
        "style": "cinematic backlight, smoky atmosphere",
    },
    "Nerith • Batalha": {
        "positive": (
            "full-body combat stance, explosive motion lines, sparks, debris; "
            "silver tendrils reacting; tail-blade extended; solo; "
            "elongated pointed elven ears (no horns); "
            f"{POSE_POS}; {BREAST_POS}; {TORSO_GLU_POS}; {THIGHS_POS}"
        ),
        "negative": ", ".join([DUPLICATE_NEG, TAIL_NEG, POSE_NEG, SHAPE_NEG, HORN_NEG]),
        "style": "dynamic action, low-angle shot",
    },

    # ✅ Novo preset oficial otimizado para FLUX.1-dev (mais curto e assertivo)
    "Nerith • FLUX Dev (HQ curto)": {
        "positive": (
            "comic panel, full body, bold ink, cel shading, dramatic rimlight; rainy neon alley; "
            "female dark-elf, blue-slate skin, metallic silver long hair, green eyes; "
            "long pointed elven ears (no horns); solo; "
            f"{TAIL_POS}; {POSE_POS}; {BREAST_POS}; {TORSO_GLU_POS}; {THIGHS_POS}"
        ),
        "negative": ", ".join([DUPLICATE_NEG, TAIL_NEG, POSE_NEG, SHAPE_NEG, HORN_NEG]),
        "style": "noir sci-fi, halftone, dynamic angle",
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
    if not name:
        return
    store = _preset_store()
    store[name] = {
        "positive": data.get("positive", ""),
        "negative": data.get("negative", ""),
        "style": data.get("style", ""),
    }

# ======================
# Construtor de Prompt
# ======================
def build_prompts_from_preset(
    preset: Dict[str, str],
    scene_desc: str,
    nsfw_on: bool,
) -> Tuple[str, str]:
    """
    Retorna (prompt, negative_prompt), já combinando SFW/NSFW e a cena.
    Mantém os negativos fora do prompt principal para melhor controle.
    """
    base_pos = _squash_spaces((preset.get("positive", "") or "").strip())
    base_neg = _squash_spaces((preset.get("negative", "") or "").strip())
    sty     = _squash_spaces((preset.get("style", "") or "").strip())

    # SFW vs Sensual implícito
    if nsfw_on:
        style_block = f"{sty} {SENS_POS}".strip()
        neg_block   = f"{base_neg}, {SENS_NEG}".strip(", ")
    else:
        style_block = f"{sty} {SFW_POS}".strip()
        neg_block   = f"{base_neg}, {SFW_NEG}".strip(", ")

    # Monta os prompts concisos
    prompt = " ".join([
        base_pos,
        f"style: {style_block}" if style_block else "",
        f"Scene: {scene_desc}" if scene_desc else "",
    ]).strip()

    negative_prompt = neg_block

    # Compactação sob limite
    prompt, _ = _fit_to_limit(prompt, MAX_PROMPT_LEN)
    negative_prompt, _ = _fit_to_limit(negative_prompt, MAX_PROMPT_LEN)

    return prompt, negative_prompt

# ===================================================
# UI – Botão Principal
# ===================================================
def render_comic_button(
    get_history_docs_fn: Callable[[], List[Dict]],
    scene_text_provider: Callable[[], str],
    *,
    model_name: str = "black-forest-labs/FLUX.1-dev",  # default p/ melhor qualidade
    size: str = "1024x1024",
    title: str = "🎞️ Quadrinho (beta)",
    ui=None,
    key_prefix: str = "",
) -> None:

    ui = ui or st
    key_prefix = (key_prefix or "nerith_comics").replace(" ", "_")

    try:
        ui.markdown(f"### {title}")
        ui.caption("• Gerador de painéis em estilo HQ para Nerith")

        # Seleção de modelo
        prov_key = ui.selectbox(
            "Modelo",
            options=list(PROVIDERS.keys()),
            index=list(PROVIDERS.keys()).index("HF • FLUX.1-dev") if "HF • FLUX.1-dev" in PROVIDERS else 0,
            key=f"{key_prefix}_model_sel",
        )
        cfg = PROVIDERS.get(prov_key, {})
        provider_name = cfg.get("provider", "huggingface")
        model_name = cfg.get("model", model_name)
        size = cfg.get("size", size)

        # Seleção de preset
        all_presets = get_all_presets()
        preset_names = list(all_presets.keys())
        default_idx = preset_names.index("Nerith • FLUX Dev (HQ curto)") if "Nerith • FLUX Dev (HQ curto)" in preset_names else 0
        sel_preset = ui.selectbox(
            "Preset de Cena",
            options=preset_names,
            index=default_idx,
            key=f"{key_prefix}_preset_sel",
        )
        cur = dict(all_presets.get(sel_preset, {}))

        with ui.expander("✏️ Ajustar preset (opcional)", expanded=False):
            cur["positive"] = ui.text_area("Positive Prompt", value=cur.get("positive", ""), height=120, key=f"{key_prefix}_preset_pos")
            cur["negative"] = ui.text_area("Negative Prompt", value=cur.get("negative", ""), height=90, key=f"{key_prefix}_preset_neg")
            cur["style"]    = ui.text_input("Estilo Extra", value=cur.get("style", ""), key=f"{key_prefix}_preset_style")

            c1, c2 = ui.columns([3, 1])
            new_name = c1.text_input("Salvar como", value=f"{sel_preset} (cópia)", key=f"{key_prefix}_preset_newname")
            if c2.button("💾 Salvar preset", key=f"{key_prefix}_savepreset"):
                save_user_preset(new_name, cur)
                ui.success(f"Preset salvo: {new_name}")
                st.rerun()

        # Controles
        colA, colB = ui.columns([3, 1])
        nsfw_on = colA.toggle("Liberar sensualidade implícita", value=False, key=f"{key_prefix}_nsfw_toggle")
        gen = colB.button("Gerar painel", use_container_width=True, key=f"{key_prefix}_gen_btn")

        if not gen:
            return

        # Cena
        try:
            _ = get_history_docs_fn()  # reservado p/ contexto futuro
        except Exception:
            pass
        raw_scene = scene_text_provider() or "Rainy neon alley at night; Nerith pauses, listening; camera at low-angle."
        scene_desc = _sanitize_scene(raw_scene, limit=220)

        # Prompts finais
        prompt, negative_prompt = build_prompts_from_preset(cur, scene_desc, nsfw_on)

        with ui.expander("Ver prompts finais", expanded=False):
            st.code(prompt)
            st.code(negative_prompt)

        # Tamanho
        ui.caption(f"Len(prompt)={len(prompt)} / Len(negative)={len(negative_prompt)} / limite={MAX_PROMPT_LEN}")

        # size -> width/height
        try:
            width, height = map(int, str(size).lower().split("x"))
        except Exception:
            width, height = 1024, 1024

        # Geração
        client = _hf_client(provider_name)
        with st.spinner("Gerando painel…"):
            img_data = client.text_to_image(
                model=model_name,
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
            )

        # Compat: bytes -> Image
        if isinstance(img_data, Image.Image):
            img = img_data
        else:
            img = Image.open(io.BytesIO(img_data))

        # Exibir / Download
        ui.image(img, caption="Painel em estilo HQ (Nerith)", use_column_width=True)
        buf = io.BytesIO(); img.save(buf, "PNG")
        ui.download_button(
            "⬇️ Baixar PNG",
            data=buf.getvalue(),
            file_name="nerith_quadrinho.png",
            mime="image/png",
            key=f"{key_prefix}_dl_btn",
        )

    except Exception as e:
        ui.error(f"Falha na geração de quadrinhos: {e}")
        # opcional: log detalhado
        try:
            st.exception(e)
        except Exception:
            pass
