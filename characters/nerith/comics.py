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


# ===================================================
# ✅ PRESETS (originais + do usuário)
# ===================================================

_DEFAULT_PRESETS: Dict[str, Dict[str, str]] = {
    "Nerith • Caçadora": {
        "positive": (
            "high-end comic panel, full-body shot, bold ink outlines, cel shading, "
            "dramatic rimlight, gritty detail, dynamic angle, halftone texture, rain and neon; "
            "female dark-elf warrior from Elysarix; blue-slate luminous skin; metallic silver long hair; "
            "predatory green eyes; athletic hourglass body; strong thighs; wide hips; firm shaped glutes; "
            "silver sensory tendrils moving; " + TAIL_DISAMBIG_POS + "; " + SOLO_POS + "; "
            + LEGS_FULL_POS + "; " + NO_HUMAN_REFLECTIONS_POS
        ),
        "negative": (
            "kiss, couple, romance, soft framing, gentle embrace, coy look, fisheye, warped anatomy, distorted proportions, "
            + ANTI_DUP_NEG + ", " + ANATOMY_NEG
        ),
        "style": "gritty noir sci-fi, rain, neon reflections",
    },
    "Nerith • Dominante": {
        "positive": (
            "three-quarter to full body, bold ink, cel shading, dramatic rimlight; "
            "Elysarix dark elf; glowing blue-slate skin; silver hair; green neon eyes; "
            "hips emphasized; dominant posture; teasing gaze; tendrils active; tail-blade raised; "
            + SOLO_POS + "; " + LEGS_FULL_POS + "; " + NO_HUMAN_REFLECTIONS_POS + "; " + TAIL_DISAMBIG_POS
        ),
        "negative": "romance, couple, kiss, soft cinematography, " + ANTI_DUP_NEG + ", " + ANATOMY_NEG,
        "style": "cinematic backlight, halftone accents",
    },
    "Nerith • Batalha": {
        "positive": (
            "full-body combat stance, explosive motion, debris, sparks; tail-blade extended; tendrils reacting; "
            "muscles defined; bold inks, cel shading, halftone texture; " + SOLO_POS + "; "
            + LEGS_FULL_POS + "; " + NO_HUMAN_REFLECTIONS_POS + "; " + TAIL_DISAMBIG_POS
        ),
        "negative": "romance, kiss, couple, " + ANTI_DUP_NEG + ", " + ANATOMY_NEG,
        "style": "dynamic action shot, low-angle",
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
    force_solo: bool = True,
    legs_visible: bool = True,
    anti_mirror: bool = True,
) -> str:
    """Constrói o prompt final com reforços anti-duplicação/anatomia."""
    guard = "" if nsfw_on else "sfw, no explicit nudity, no genitals, implied tension only,"
    pos = preset.get("positive", "").strip()
    neg = preset.get("negative", "").strip()
    sty = preset.get("style", "").strip()

    extras_pos = []
    if force_solo:
        extras_pos.append(SOLO_POS)
        extras_pos.append(TAIL_DISAMBIG_POS)
    if legs_visible:
        extras_pos.append(LEGS_FULL_POS)
    if anti_mirror:
        extras_pos.append(NO_HUMAN_REFLECTIONS_POS)

    # Negativos globais
    neg_all = ", ".join([s for s in [neg, ANTI_DUP_NEG, ANATOMY_NEG] if s])

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
