# characters/nerith/prompt_builder.py
from __future__ import annotations
from typing import Tuple
from .presets import (
    INK_LINE_POS, SENSUAL_NEG, SENSUAL_POS, TAIL_POS
)

MAX_PROMPT_LEN = 1800

def _clean(s: str) -> str:
    return " ".join((s or "").split())

def _limit(s: str) -> str:
    return _clean(s)[:MAX_PROMPT_LEN]

def build_prompts(preset: dict, nsfw: bool, framing: str, angle: str,
                  pose: str, env: str) -> Tuple[str, str]:
    # prefixa com o nome da personagem (pesa mais no começo)
    pos = "Nerith; " + preset["positive"]
    neg = preset["negative"]
    style = preset["style"]

    # cauda fora de close-up
    if "close-up" not in framing:
        pos += ", " + TAIL_POS

    pos += f", {framing}, {angle}"
    if pose:
        pos += f", {pose}"
    if env:
        pos += f", scene: {env}"

    if nsfw:
        final_style = style
        final_neg = f"{neg}, {SENSUAL_NEG}"
    else:
        final_style = f"{style}, soft cinematic elegance"
        final_neg = f"{neg}, {SENSUAL_POS}"

    prompt = _limit(f"{pos}, style: {final_style}, {INK_LINE_POS}, original character, no celebrity likeness")
    negative = _limit(final_neg)
    return prompt, negative


# Pequeno ajuste para os modelos Qwen (evita rosto “perfeito/plástico”)
def qwen_prompt_fix(prompt: str) -> str:
    extra = (
        ", mature defined facial structure, deep cheekbone shadows, "
        "comic-textured skin, visible pores (not over-smooth), "
        "firm ink outlines around eyes and lips"
    )
    return prompt + extra
