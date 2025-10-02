# 🧱 Core comum

## `core/common/sidebar_types.py`

```python
from dataclasses import dataclass
from typing import Any, List, Optional, Dict, Callable

FieldType = str  # "bool" | "text" | "select" | "datetime" | "note" | "int" | "float"

@dataclass
class FieldSpec:
    key: str
    label: str
    type: FieldType
    help: str = ""
    default: Any = None
    choices: Optional[List[str]] = None
    read_only: bool = False
    visible_if: Optional[Dict[str, Any]] = None
    compute: Optional[Callable[[Dict[str, Any]], Any]] = None

