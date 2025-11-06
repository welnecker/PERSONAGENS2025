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

def _hf_client() -> InferenceClient:
    # huggingface_hub.InferenceClient usa 'token'
    return InferenceClient(token=_get_hf_token())

# ======================
# Limites / utilitários de prompt
# ======================
MAX_PROMPT_LEN = 1900  # margem segura abaixo do limite 2000 do endpoint

_SHORT_NEG = (
    "no kissing, no couple, no duplicate person, no second person, no extra limbs, "
    "no missing legs, no cropped feet, no bad anatomy"
)

def _squash_spaces(s: str) -> str:
    return " ".join((s or "").split())

def _sanitize_scene(s: str, limit: int = 260) -> str:
    s = (s or "").replace("*", "").replace("`", " ")
    s = s.replace("\n", " ").replace("\r", " ")
    return _squash_spaces(s)[:limit]

def _fit_to_limit(prompt: str, max_len: int = MAX_PROMPT_LEN) -> tuple[str, bool]:
    """Reduz o prompt mantendo intenção. Retorna (texto, foi_reduzido?)."""
    p = _squash_spaces(prompt)
    reduced = False
    if len(p) <= max_len:
        return p, reduced
    reduced = True

    # 1) comprime NEGATIVE
    if "NEGATIVE:" in p and len(p) > max_len:
        prefix, _, _ = p.partition("NEGATIVE:")
        p = _squash_spaces(f"{prefix} NEGATIVE: ({_SHORT_NEG})")

    # 2) remove bloco ' style:' se ainda exceder
    if len(p) > max_len and " style:" in p:
        head, sep, tail = p.partition(" style:")
        if sep:
            _style_block, sep2, rest = tail.partition(" Scene:")
            p = _squash_spaces(head + (" Scene:" + rest if sep2 else ""))

    # 3) encurta a cena
    if len(p) > max_len and " Scene:" in p:
        head, _, rest = p.partition(" Scene:")
        rest = rest[: max(120, (max_len - len(head) - 20))]
        p = _squash_spaces(f"{head} Scene: {rest}")

    # 4) corte duro final
    if len(p) > max_len:
        p = p[: max_len - 3] + "..."
    return p, reduced

# ======================
# Regras auxiliares (anti-duplicação / anatomia / orelhas)
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
HORN_NEG = (
    "horns, horn, antlers, antler, head spikes, forehead spikes, "
    "bony protrusions on head, skull horns, demon horns, curved horns"
)

# ===== Pose / Anatomia segura =====
POSE_POS = (
    "natural contrapposto, spine neutral, torso twist ≤30°, head turn ≤30°, "
    "shoulders and hips aligned, relaxed scapula, weight on rear leg, "
    "three-quarter back view, eye-level to low-angle camera, "
    "tail curves BEHIND the body, does not cross legs or torso"
)
POSE_NEG = (
    "twisted torso, extreme twist, broken spine, broken neck, neck 180°, "
    "hyperrotation, contortionist pose, excessive arch, scoliosis pose, "
    "misaligned shoulders, hips misalignment, dislocated joints, "
    "tail intersecting legs, tail clipping body"
)

# --- Cauda: base e desambiguação ---
TAIL_BASE_POS = (
    "a single curved blade tail with a visible anatomical base attached at the sacrum/lower-back; "
    "it emerges above the gluteal crease, between the upper glutes; "
    "clearly non-genital, non-phallus, non-limb, never exiting the anus"
)
TAIL_NEG = (
    "phallic tail, penis-like tail, tail shaped like a limb, tail fused to leg, "
    "detached tail, floating tail, tail exiting the anus, tail from crotch"
)

# --- Reflexos / composição ---
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

SHAPE_NEG = (
    "balloon breasts, sphere boobs, torpedo breasts, implant sphere, uneven breasts, misaligned nipples, "
    "collapsed chest, unnatural cleavage, underboob artifact, extra nipples, dislocated breast, "
    "distorted abdomen, misshapen waist, broken spine, broken hips, "
    "collapsed butt, square butt, saggy butt, fused thighs"
)

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

# --- Proporções corporais coesas (perfis) ---
PROPORTION_PROFILES = {
    "Balanceado": (
        "balanced hourglass, harmonious hip-to-waist ratio, natural taper at the waist, "
        "glutes round and lifted but proportional to pelvis, thighs strong but proportional to glutes"
    ),
    "Curvas firmes": (
        "pronounced hourglass with firm high-set glutes, slightly fuller thighs, "
        "tight waist with natural abdomen definition, overall cohesive silhouette"
    ),
    "Atleta seca": (
        "lean athletic hourglass, moderate glute volume, defined thighs and obliques, "
        "natural waist without extreme taper"
    ),
}

# --- Evitar exageros dissonantes ---
DISPROPORTION_NEG = (
    "extreme hourglass, wasp waist, overly wide hips, exaggerated butt, cartoonish glutes, "
    "overinflated thighs, disproportionate thighs, unnatural pelvis tilt"
)

# ===================================================
# PRESETS (originais + do usuário)
# ===================================================
_DEFAULT_PRESETS: Dict[str, Dict[str, str]] = {
    "Nerith • Caçadora": {
        "positive": (
            "high-end comic panel, full-body, bold ink, cel shading, dramatic rimlight, rain and neon; "
            "female dark-elf from Elysarix; blue-slate luminous skin with faint inner glow; "
            "metallic silver long hair; green predatory eyes; "
            "elongated pointed elven ears; no horns; no antlers; no head protrusions; "
            "silver sensory tendrils active; "
            "three-quarter back view, eye-level to low-angle; natural contrapposto; "
            f"{SOLO_POS}; {LEGS_FULL_POS}; {NO_HUMAN_REFLECTIONS_POS}"
        ),
        "negative": (
            "romance, couple, kiss, soft framing, " + ANTI_DUP_NEG + ", " + ANATOMY_NEG + ", " + SHAPE_NEG + ", " + HORN_NEG
        ),
        "style": "gritty noir sci-fi, halftone accents, dynamic angle",
    },
    "Nerith • Dominante": {
        "positive": (
            "three-quarter to full body, bold ink, cel shading, dramatic back rimlight; "
            "dark-elf; metallic silver long hair; neon green eyes; dominant posture; "
            "elongated pointed elven ears; no horns; no antlers; no head protrusions; "
            "tendrils alive; tail-blade raised; solo; legs complete; no human reflections"
        ),
        "negative": "romance, couple, kiss, " + ANTI_DUP_NEG + ", " + ANATOMY_NEG + ", " + SHAPE_NEG + ", " + HORN_NEG,
        "style": "cinematic backlight, smoky atmosphere",
    },
    "Nerith • Batalha": {
        "positive": (
            "full-body combat stance, explosive motion lines, sparks, debris; "
            "elongated pointed elven ears; no horns; no antlers; no head protrusions; "
            "tendrils reacting; tail-blade extended; solo; legs complete; no human reflections"
        ),
        "negative": "romance, kiss, couple, " + ANTI_DUP_NEG + ", " + ANATOMY_NEG + ", " + SHAPE_NEG + ", " + HORN_NEG,
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
    tail_base_visible: bool = True,
    proportions_profile: str = "Balanceado",
    pose_safe: bool = True,
) -> str:
    """Constrói prompt final com guarda NSFW, anatomia, cauda correta e proporções coesas."""
    guard = "" if nsfw_on else "sfw, no explicit nudity, no genitals, implied tension only,"

    pos = (preset.get("positive", "") or "").strip()
    neg = (preset.get("negative", "") or "").strip()
    sty = (preset.get("style", "") or "").strip()

    # Perfis
    shape_txt = SHAPE_PROFILES.get(anatomy_profile, SHAPE_PROFILES["Atlética (padrão)"])
    prop_txt  = PROPORTION_PROFILES.get(proportions_profile, PROPORTION_PROFILES["Balanceado"])

    extras_pos: List[str] = [shape_txt, prop_txt]
    if pose_safe:
        extras_pos.append(POSE_POS)
    if force_solo:
        extras_pos.append(SOLO_POS)
    if legs_visible:
        extras_pos.append(LEGS_FULL_POS)
    if anti_mirror:
        extras_pos.append(NO_HUMAN_REFLECTIONS_POS)
    if tail_base_visible:
        extras_pos.append(TAIL_BASE_POS)
    # Reforço único da cauda como cauda (não membro)
    extras_pos.append("exactly one tail-blade; clearly a tail; not a limb; not genital")

    # Negativos consolidados
    neg_all = ", ".join([n for n in [neg, SHAPE_NEG, ANTI_DUP_NEG, ANATOMY_NEG, TAIL_NEG, HORN_NEG, DISPROPORTION_NEG, POSE_NEG] if n])

    parts = [
        guard,
        pos,
        " ".join(extras_pos),
        (f"style: {sty}" if sty else ""),
        f"Scene: {scene_desc}",
        f"NEGATIVE: ({neg_all})",
    ]
    return " ".join(p for p in parts if p)

# ===================================================
# UI – botão principal (sem balões)
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
        # --------------------------------------------------
        ui.markdown(f"### {title}")
        ui.caption("• bloco de quadrinhos carregado")

        # --------------------------------------------------
        # Seleção de modelo
        # --------------------------------------------------
        prov_key = ui.selectbox(
            "Modelo",
            options=list(PROVIDERS.keys()),
            index=0,
            key=f"{key_prefix}_model_sel"
        )
        cfg = PROVIDERS.get(prov_key, {})
        model_name = cfg.get("model", model_name)
        size = cfg.get("size", size)

        # --------------------------------------------------
        # Seleção / edição de preset
        # --------------------------------------------------
        all_presets = get_all_presets()
        preset_names = list(all_presets.keys())
        sel_preset = ui.selectbox(
            "Preset de Cena",
            options=preset_names,
            index=0,
            key=f"{key_prefix}_preset_sel",
        )
        cur = dict(all_presets.get(sel_preset, {}))

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

        # --------------------------------------------------
        # Controles de cena
        # --------------------------------------------------
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
        force_solo = col_solo.checkbox("Forçar solo", value=True,  key=f"{key_prefix}_solo")
        legs_visible = col_legs.checkbox("Pernas completas", value=True, key=f"{key_prefix}_legs")
        anti_mirror = col_mirror.checkbox("Sem reflexo humano", value=True, key=f"{key_prefix}_mirror")

        col_tail, col_prop = ui.columns([1, 2])
        tail_base_visible = col_tail.checkbox("Base da cauda visível", value=True, key=f"{key_prefix}_tailbase")
        proportions_profile = col_prop.selectbox(
            "Equilíbrio corporal", options=list(PROPORTION_PROFILES.keys()),
            index=0, key=f"{key_prefix}_prop_profile",
        )

        pose_safe = ui.checkbox("Pose segura (anti-torção)", value=True, key=f"{key_prefix}_pose_safe")

        anatomy_profile = ui.selectbox(
            "Perfil anatômico",
            options=list(SHAPE_PROFILES.keys()),
            index=0,
            key=f"{key_prefix}_shape_profile",
        )

        if not gen:
            return

        # --------------------------------------------------
        # Cena textual
        # --------------------------------------------------
        try:
            _ = get_history_docs_fn()
        except Exception:
            pass

        raw_scene = scene_text_provider() or "Nerith em posição de combate, noite, chuva, neon."
        scene_desc = _sanitize_scene(raw_scene, limit=220)

        # --------------------------------------------------
        # Prompt final
        # --------------------------------------------------
        prompt = build_prompt_from_preset(
            cur, scene_desc, nsfw_on,
            anatomy_profile=anatomy_profile,
            force_solo=force_solo,
            legs_visible=legs_visible,
            anti_mirror=anti_mirror,
            tail_base_visible=tail_base_visible,
            proportions_profile=proportions_profile,
            pose_safe=pose_safe,
        )

        # 🔒 Compactação obrigatória
        prompt, reduced = _fit_to_limit(prompt, MAX_PROMPT_LEN)
        if reduced:
            ui.warning("⚠️ Prompt acima do limite — compactado automaticamente.")
        ui.caption(f"Tamanho FINAL do prompt: {len(prompt)}/{MAX_PROMPT_LEN} chars")

        # --------------------------------------------------
        # Converter size
        # --------------------------------------------------
        if isinstance(size, str) and "x" in size.lower():
            parts = size.lower().split("x", 1)
            width, height = int(parts[0].strip()), int(parts[1].strip())
        elif isinstance(size, (tuple, list)) and len(size) == 2:
            width, height = int(size[0]), int(size[1])
        else:
            width = height = 1024

        # --------------------------------------------------
        # Gerar imagem
        # --------------------------------------------------
        client = _hf_client()
        with st.spinner("Gerando painel…"):
            img = client.text_to_image(
                model=model_name,
                prompt=prompt,
                width=width,
                height=height,
            )

        # compat: pode vir bytes em algumas versões
        if not isinstance(img, Image.Image):
            try:
                img = Image.open(io.BytesIO(img))
            except Exception:
                raise RuntimeError("Falha ao decodificar imagem retornada pela API.")

        # --------------------------------------------------
        # Exibir & baixar
        # --------------------------------------------------
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
