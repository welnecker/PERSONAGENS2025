from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

from core.common.sidebar_types import FieldSpec

class BaseCharacter(ABC):
    name: str = "Character"
    aliases: Tuple[str, ...] = ()

    # Persona
    @abstractmethod
    def persona_text(self) -> str: ...
    @abstractmethod
    def history_boot(self) -> List[Dict[str, str]]: ...

    # Estilo e few-shots
    def style_guide(self, nsfw_on: bool, flirt_mode: bool, romance_on: bool) -> str:
        return "ESTILO: primeira pessoa. Frases curtas. 3–5 parágrafos. Sem metacena."

    def fewshots(self, flirt_mode: bool, nsfw_on: bool, romance_on: bool) -> List[Dict[str, str]]:
        return []

    # Sidebar
    def sidebar_schema(self) -> List[FieldSpec]:
        return []

    def on_sidebar_change(self, usuario_key: str, values: Dict[str, Any]) -> None:
        pass

    # Geração (delegado ao pipeline comum)
    def gerar_resposta(self, usuario: str, prompt: str, model: str) -> str:
        from core.engine.pipeline import generate_response
        return generate_response(self, usuario, prompt, model)

    # Hooks opcionais do pipeline
    def refine_post(self, text: str, user_prompt: str, nsfw_on: bool) -> str:
        return text

    def enforce_scope(self, text: str, user_prompt: str) -> str:
        return text

    def post_generation(self, text: str, user_prompt: str, usuario_key: str) -> str:
        return text

