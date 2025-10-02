# reexporta utilidades comuns
from .base_service import BaseCharacter
from .sidebar_types import (
    FieldSpec, SidebarSection,
    field_bool, field_text, field_select, field_slider
)

__all__ = [
    "BaseCharacter",
    "FieldSpec", "SidebarSection",
    "field_bool", "field_text", "field_select", "field_slider",
]

