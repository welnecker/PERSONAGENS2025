# core/common/sidebar_types.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional, Literal

FieldType = Literal[
    "text", "textarea", "checkbox", "select",
    "number", "slider", "time", "date"
]

@dataclass
class FieldSpec:
    """
    Especifica um campo exibido no sidebar.
    """
    key: str
    label: str
    type: FieldType
    help: Optional[str] = None

    # Para selects
    options: Optional[List[str]] = None

    # Para number/slider
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None

    # Valor padrão
    default: Any = None


@dataclass
class SidebarSection:
    """
    Bloco lógico do sidebar (título + campos).
    """
    title: str
    fields: List[FieldSpec] = field(default_factory=list)


# ---------- fábricas de conveniência ----------
def text_field(key: str, label: str, *, default: str = "", help: Optional[str] = None) -> FieldSpec:
    return FieldSpec(key=key, label=label, type="text", default=default, help=help)

def textarea_field(key: str, label: str, *, default: str = "", help: Optional[str] = None) -> FieldSpec:
    return FieldSpec(key=key, label=label, type="textarea", default=default, help=help)

def checkbox_field(key: str, label: str, *, default: bool = False, help: Optional[str] = None) -> FieldSpec:
    return FieldSpec(key=key, label=label, type="checkbox", default=default, help=help)

def select_field(key: str, label: str, options: List[str], *, default: Optional[str] = None,
                 help: Optional[str] = None) -> FieldSpec:
    return FieldSpec(key=key, label=label, type="select", options=options, default=default, help=help)

def number_field(key: str, label: str, *, default: Optional[float] = None,
                 min: Optional[float] = None, max: Optional[float] = None, step: Optional[float] = None,
                 help: Optional[str] = None) -> FieldSpec:
    return FieldSpec(key=key, label=label, type="number", default=default, min=min, max=max, step=step, help=help)

def slider_field(key: str, label: str, *, default: Optional[float] = None,
                 min: float = 0.0, max: float = 1.0, step: float = 0.01,
                 help: Optional[str] = None) -> FieldSpec:
    return FieldSpec(key=key, label=label, type="slider", default=default, min=min, max=max, step=step, help=help)

def time_field(key: str, label: str, *, default: Optional[str] = None,
               help: Optional[str] = None) -> FieldSpec:
    return FieldSpec(key=key, label=label, type="time", default=default, help=help)

def date_field(key: str, label: str, *, default: Optional[str] = None,
               help: Optional[str] = None) -> FieldSpec:
    return FieldSpec(key=key, label=label, type="date", default=default, help=help)


__all__ = [
    "FieldSpec", "SidebarSection",
    "text_field", "textarea_field", "checkbox_field", "select_field",
    "number_field", "slider_field", "time_field", "date_field",
]


