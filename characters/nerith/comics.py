from __future__ import annotations
import os, io
from typing import Callable, List, Dict
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
        raise RuntimeError("Defina HUGGINGFACE_API_KEY em st.secrets.")
    return tok

def _hf_client() -> InferenceClient:
    return InferenceClient(token=_get_hf_token())


# ======================
# Regras auxiliares (anti-duplicação / anatomia)
# ======================
ANTI_DUP_NEG = (
    "two people, two girls, duplicate, twin, second person, extra person, extra body, "
    "second figure, clone, copy, siamese, conjoined, overlapping bodies, mirrored person, "
    "reflected person, multiple women"
)
ANATOMY_NEG = (
    "extra limbs, extra legs, missing legs, cut off legs, cropped feet, deformed legs, "
    "bad anatomy, malformed hands, fused fingers, extra fingers"
)
TAIL_DISAMBIG_POS = (
    "single curved blade tail emerging from the lower back, clearly one tail, not a person, not a leg"
)
NO_HUMAN_REFLECTIONS_POS = "wet ground reflects neon lights only, no human reflections"
SOLO_POS = "solo, single female character, one subject, centered composition"
LEGS_FULL_POS = "full-length legs visible down to the feet, natural proportions"


# ======================
# Forma corporal (positivos e negativos)
# ======================
BREAST_SHAPE_POS = (
    "full and firm breasts with natural teardrop shape, proportional to athletic frame, "
    "cohesive attachment to chest, natural gravity, smooth upper pole, defined but subtle lower curve"
)
TORSO_GLU_POS = (
    "flat to slightly defined abdomen, visible oblique lines, narrow waist, "
    "wide pelvis, round high-set glutes with clear underglute line"
)
THIGHS_POS = "strong thighs, smooth quad curves, natural knee structure, calves balanced"

# Negativos específicos de seio/barriga/glúteo/coxa
SHAPE_NEG = (
    "balloon breasts, sphere boobs, torpedo breasts, implant sphere, uneven breasts, misaligned nipples, "
    "collapsed chest, unnatural cleavage, underboob artifact, extra nipples, dislocated breast, "
    "distorted abdomen, misshapen waist, broken spine, broken hips, "
    "collapsed butt, square butt, saggy butt, fused thighs"
)

# Perfis prontos (padrão = Atlética)
SHAPE_PROFILES = {
    "Atlética (padrão)": (
        "athletic hourglass, toned yet feminine, " + BREAST_SHAPE_POS + ", "
        + TORSO_GLU_POS + ", " + THIGHS_POS
    ),
    "Hourglass suave": (
        "soft hourglass, slightly fuller breasts and hips, gentle abdomen definition, "
        + BREAST_SHAPE_POS + ", " + THIGHS_POS
    ),
    "Slim power": (
        "slim athletic, smaller but firm teardrop breasts, tighter waist, compact round glutes, "
        + THIGHS_POS
    ),
}

# ===================================================
# ✅ PRESETS (originais + do usuário)
# ===================================================

_DEFAULT_PRESETS = {
    "Nerith • Caçadora": {
        "positive": (
            "high-end comic panel, full-body, bold ink, cel shading, dramatic rimlight, rain and neon; "
            "female dark-elf from Elysarix; blue-slate luminous skin; metallic silver long hair; green predatory eyes; "
            "silver sensory tendrils active; single curved blade tail (not a person); solo, one subject; "
            "full-length legs to the feet; wet ground neon reflections (no human reflections)"
        ),
        "negative": (
            "romance, couple, kiss, soft framing, " + ANTI_DUP_NEG + ", " + ANATOMY_NEG + ", " + SHAPE_NEG
        ),
        "style": "gritty noir sci-fi, halftone accents, dynamic angle",
    },
    "Nerith • Dominante": {
        "positive": (
            "three-quarter to full body, bold ink, cel shading, dramatic back rimlight; "
            "dark-elf; silver hair; neon green eyes; dominant posture; tendrils alive; tail-blade raised; "
            "solo; legs complete; no human reflections"
        ),
        "negative": "romance, couple, kiss, " + ANTI_DUP_NEG + ", " + ANATOMY_NEG + ", " + SHAPE_NEG,
        "style": "cinematic backlight, smoky atmosphere",
    },
    "Nerith • Batalha": {
        "positive": (
            "full-body combat stance, explosive motion lines, sparks, debris; tendrils reacting; tail-blade extended; "
            "solo; legs complete; no human reflections"
        ),
        "negative": "romance, kiss, couple, " + ANTI_DUP_NEG + ", " + ANATOMY_NEG + ", " + SHAPE_NEG,
        "style": "dynamic action, low-angle shot",
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

def build_prompt_from_preset(
    preset: Dict[str, str],
    scene_desc: str,
    nsfw_on: bool,
    *,
    anatomy_profile: str = "Atlética (padrão)",
    force_solo: bool = True,
    legs_visible: bool = True,
    anti_mirror: bool = True,
) -> str:
    """Constrói o prompt final com guarda NSFW, perfil anatômico e reforços de continuidade."""
    guard = "" if nsfw_on else "sfw, no explicit nudity, no genitals, implied tension only,"
    pos = (preset.get("positive", "") or "").strip()
    neg = (preset.get("negative", "") or "").strip()
    sty = (preset.get("style", "") or "").strip()

    # Perfil anatômico
    shape_txt = SHAPE_PROFILES.get(anatomy_profile, SHAPE_PROFILES["Atlética (padrão)"])

    extras_pos = [shape_txt]
    if force_solo:
        extras_pos.append("solo, one female subject, centered composition")
        extras_pos.append("single curved blade tail clearly distinct from legs")
    if legs_visible:
        extras_pos.append("full-length legs visible down to the feet, natural proportions")
    if anti_mirror:
        extras_pos.append("wet ground reflects neon lights only, no human reflections")

    neg_all = ", ".join([n for n in [neg, SHAPE_NEG, ANTI_DUP_NEG, ANATOMY_NEG] if n])

    parts = [
        guard,
        pos,
        " ".join(extras_pos),
        f"style: {sty}" if sty else "",
        f"Scene: {scene_desc}",
        f"NEGATIVE: ({neg_all})",
    ]
    return " ".join(p for p in parts if p)


# ===================================================
# ✅ UI – botão principal (sem balões)
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
        # ------------------------------
        ui.markdown(f"### {title}")
        ui.caption("• bloco de quadrinhos carregado")

        # ------------------------------
        # SELEÇÃO DE MODELO
        # ------------------------------
        prov_key = ui.selectbox(
            "Modelo",
            options=list(PROVIDERS.keys()),
            index=0,
            key=f"{key_prefix}_model_sel"
        )
        cfg = PROVIDERS.get(prov_key, {})
        model_name = cfg.get("model", model_name)
        size = cfg.get("size", size)

        # ------------------------------
        # SELEÇÃO DE PRESET
        # ------------------------------
        all_presets = get_all_presets()
        preset_names = list(all_presets.keys())
        sel_preset = ui.selectbox(
            "Preset de Cena",
            options=preset_names,
            index=0,
            key=f"{key_prefix}_preset_sel",
        )
        cur = dict(all_presets.get(sel_preset, {}))

        # ====================================================
        # Editor do preset
        # ====================================================
        with ui.expander("✏️ Ajustar preset (opcional)", expanded=False):
            cur["positive"] = ui.text_area(
                "Positive Prompt",
                value=cur.get("positive", ""),
                height=140,
                key=f"{key_prefix}_preset_pos",
            )
            cur["negative"] = ui.text_area(
                "Negative Prompt",
                value=cur.get("negative", ""),
                height=90,
                key=f"{key_prefix}_preset_neg",
            )
            cur["style"] = ui.text_input(
                "Estilo Extra",
                value=cur.get("style", ""),
                key=f"{key_prefix}_preset_style",
            )

            col1, col2 = ui.columns([3, 1])
            new_name = col1.text_input(
                "Salvar como",
                value=f"{sel_preset} (meu)",
                key=f"{key_prefix}_preset_newname",
            )
            if col2.button("💾 Salvar preset", key=f"{key_prefix}_savepreset"):
                save_user_preset(new_name, cur)
                ui.success(f"Preset salvo: {new_name}")

        # ------------------------------
        # NSFW + Botão
        # ------------------------------
        A, B = ui.columns([3, 1])
        nsfw_on = A.toggle(
            "NSFW liberado",
            value=False,
            key=f"{key_prefix}_nsfw_toggle"
        )
        gen = B.button(
            "Gerar quadrinho",
            use_container_width=True,
            key=f"{key_prefix}_gen_btn"
        )

        col_solo, col_legs, col_mirror = ui.columns(3)
        force_solo = col_solo.checkbox("Forçar solo", value=True, key=f"{key_prefix}_solo")
        legs_visible = col_legs.checkbox("Pernas completas", value=True, key=f"{key_prefix}_legs")
        anti_mirror = col_mirror.checkbox("Sem reflexo humano", value=True, key=f"{key_prefix}_mirror")


        if not gen:
            return

        # Seleção do perfil anatômico
        col_shape, _, _ = ui.columns([2, 1, 1])
        anatomy_profile = col_shape.selectbox(
            "Perfil anatômico",
            options=list(SHAPE_PROFILES.keys()),
            index=0,
            key=f"{key_prefix}_shape_profile",
        )
        
        # (se já existem estes)
        # force_solo, legs_visible, anti_mirror definidos em checkboxes? Se não, fixe como True:
        force_solo = True
        legs_visible = True
        anti_mirror = True

        # ------------------------------
        # Cena textual
        # ------------------------------
        try:
            _ = get_history_docs_fn()
        except:
            pass

        scene_desc = scene_text_provider() or "Nerith em posição de combate, noite, chuva, neon."

        # ------------------------------
        # Prompt final pelo PRESET
        # ------------------------------
        prompt = build_prompt_from_preset(
        cur, scene_desc, nsfw_on,
        anatomy_profile=anatomy_profile,
        force_solo=force_solo,
        legs_visible=legs_visible,
        anti_mirror=anti_mirror,
    )


        # ------------------------------
        # Converter SIZE
        # ------------------------------
        if isinstance(size, str) and "x" in size:
            w, h = size.lower().split("x")
            width, height = int(w), int(h)
        else:
            width = height = 1024

        # ------------------------------
        # Gerar imagem
        # ------------------------------
        client = _hf_client()

        with st.spinner("Gerando painel…"):
            img = client.text_to_image(
                model=model_name,
                prompt=prompt,
                width=width,
                height=height,
            )

        # Se vier bytes
        if not isinstance(img, Image.Image):
            img = Image.open(io.BytesIO(img))

        # ------------------------------
        # Exibir e Baixar
        # ------------------------------
        ui.image(img, caption="Painel em estilo HQ", use_column_width=True)

        buf = io.BytesIO()
        img.save(buf, "PNG")
        ui.download_button(
            "⬇️ Baixar PNG",
            data=buf.getvalue(),
            file_name="nerith_quadrinho.png",
            mime="image/png",
            key=f"{key_prefix}_dl_btn",
        )

    except Exception as e:
        ui.error(f"Quadrinhos: {e}")
