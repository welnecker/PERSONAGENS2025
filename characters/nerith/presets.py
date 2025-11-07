# characters/nerith/presets.py
from __future__ import annotations
from typing import Dict

# --------- blocos base (sem celebridades) ---------
FACE_POS = (
    "striking mediterranean features, almond-shaped captivating eyes, "
    "defined cheekbones, soft cat-eye eyeliner, full lips, "
    "mature confident allure, intense gaze"
)

BODY_POS = (
    "hourglass figure, soft athletic tone, firm natural breasts, "
    "narrow waist, defined abdomen, high-set rounded glutes"
)

ANATOMY_NEG = (
    "bad anatomy, deformed body, mutated body, malformed limbs, "
    "warped body, twisted spine, extra limbs, fused fingers, missing fingers"
)

BODY_NEG = (
    "balloon breasts, implants, sagging breasts, torpedo breasts, "
    "plastic body, barbie proportions, distorted waist"
)

CELEB_NEG = (
    "celebrity, celebrity lookalike, look alike, famous actress, "
    "face recognition match, portrait of a celebrity, sophia loren, monica bellucci, "
    "penelope cruz, gal gadot, angelina jolie"
)

SENSUAL_POS = (
    "subtle sensual posture, cinematic shadows caressing the skin, "
    "dramatic rimlight, implicit sensuality"
)
SENSUAL_NEG = "explicit, pornographic, nude, censored, text, watermark"

TAIL_POS = (
    "a single biomechanical blade-tail fused to the spine, silver metal, "
    "sharp edges, blue glowing energy vein"
)
TAIL_NEG = "furry tail, fleshy tail, animal tail, penis tail, detached tail"

DOLL_NEG = (
    "doll, barbie, plastic skin, CGI skin, beauty-filter, "
    "uncanny-valley, over-smooth skin, poreless skin, wax figure"
)

INK_LINE_POS = (
    "inked line art, strong outlines, cel shading, halftone dots, "
    "cross-hatching, textured paper grain, gritty shadows"
)

COMIC_ADULT = (
    "adult comic illustration, dark mature tone, dramatic chiaroscuro, "
    "rich blacks, heavy shadows, limited palette"
)

DEFAULT_NEG = f"{ANATOMY_NEG}, {BODY_NEG}, {TAIL_NEG}, {DOLL_NEG}, {CELEB_NEG}, watermark, text, signature"

IDENTITY_ANCHOR = (
    "Nerith, original character, female dark-elf (drow) with blue-slate matte skin, "
    "long metallic silver hair, vivid emerald-green eyes, elongated pointed elven ears (no horns), "
    "solo subject, elegant yet fierce presence"
)

PRESETS: Dict[str, Dict[str, str]] = {
    # FLUX HQ
    "FLUX • Nerith HQ": {
        "positive": (
            f"{IDENTITY_ANCHOR}, {FACE_POS}, {BODY_POS}, {INK_LINE_POS}, "
            "subtle arcane glow accents"
        ),
        "negative": DEFAULT_NEG,
        "style": "masterpiece comic art, neon rimlight, high contrast",
    },

    # SDXL Comic Adulto
    "SDXL • Nerith Comic (Adulto)": {
        "positive": (
            f"{IDENTITY_ANCHOR}, {FACE_POS}, {BODY_POS}, {INK_LINE_POS}, "
            "mature intensity, warrior presence"
        ),
        "negative": DEFAULT_NEG,
        "style": f"{COMIC_ADULT}",
    },

    # SDXL Noir
    "SDXL • Nerith Noir Comic": {
        "positive": (
            f"{IDENTITY_ANCHOR}, {FACE_POS}, {BODY_POS}, {INK_LINE_POS}, "
            "rain reflections, moody atmosphere"
        ),
        "negative": DEFAULT_NEG,
        "style": f"{COMIC_ADULT}, noir tone, cinematic shadows",
    },

    # Dark Fantasy (Flux/FAL)
    "FLUX • Nerith Dark Fantasy": {
        "positive": (
            f"{IDENTITY_ANCHOR}, {FACE_POS}, {BODY_POS}, {INK_LINE_POS}, "
            "dark fantasy aura, arcane energy, dramatic heavy shadows"
        ),
        "negative": DEFAULT_NEG,
        "style": (
            "dark fantasy illustration, heavy ink, deep shadows, "
            "misty atmosphere, gothic mood, dramatic highlights"
        ),
    },

    # Qwen (realism + HQ)
    "Qwen • Nerith Realism Comic": {
        "positive": (
            f"{IDENTITY_ANCHOR}, {FACE_POS}, {BODY_POS}, {INK_LINE_POS}, "
            "comic-realism balance, defined pores, natural skin texture"
        ),
        "negative": DEFAULT_NEG,
        "style": "realistic comic portrait, cinematic shadows, strong outlines",
    },
}

__all__ = [
    "FACE_POS", "BODY_POS", "ANATOMY_NEG", "BODY_NEG", "CELEB_NEG", "SENSUAL_POS", "SENSUAL_NEG",
    "TAIL_POS", "TAIL_NEG", "DOLL_NEG", "INK_LINE_POS", "COMIC_ADULT", "DEFAULT_NEG",
    "IDENTITY_ANCHOR", "PRESETS",
]
