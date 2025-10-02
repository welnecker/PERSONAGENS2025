# core/database.py
from typing import Any, Dict, List, Optional, Iterable, Tuple, Union
from threading import RLock
import uuid

# Armazenamento em memória (processo local)
_STORE: Dict[str, List[Dict[str, Any]]] = {}
_LOCK = RLock()


# ===== Helpers =====
def _get_nested(doc: Dict[str, Any], dotted: str, default: Any = None) -> Any:
    """
    Acessa campos aninhados usando caminho com pontos, ex.: "fatos.parceiro_atual".
    """
    if not dotted:
        return default
    cur: Any = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _normalize_sort(sort: Optional[Union[Dict[str, int], List, Tuple]]) -> List[Tuple[str, int]]:
    """
    Aceita:
      - None
      - [("ts", -1), ("_id", 1)]
      - ("ts", -1)
      - {"ts": -1, "_id": 1}
    Retorna lista de pares [(key, dir), ...]. Para entradas inválidas → [].
    """
    if not sort:
        return []
    if isinstance(sort, dict):
        return [(k, v) for k, v in sort.items()]
    if isinstance(sort, (list, tuple)):
        # lista de pares
        if sort and isinstance(sort[0], (list, tuple)):
            try:
                return [(str(k), int(d)) for (k, d) in sort]  # type: ignore[misc]
            except Exception:
                return []
        # tupla única ("campo", dir)
        if len(sort) == 2 and isinstance(sort[0], str):
            try:
                return [(sort[0], int(sort[1]))]  # type: ignore[index]
            except Exception:
                return []
    return []


class MemoryCollection:
    def __init__(self, name: str):
        self.name = name
        _STORE.setdefault(name, [])

    def insert_one(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        with _LOCK:
            d = dict(doc)
            d.setdefault("_id", str(uuid.uuid4()))
            _STORE[self.name].append(d)
            return {"inserted_id": d["_id"]}

    def _match(self, doc: Dict[str, Any], filt: Optional[Dict[str, Any]]) -> bool:
        """
        Filtro simples:
          - igualdade por chave (suporta caminho com pontos)
          - operador $in
        """
        if not filt:
            return True
        for k, v in filt.items():
            if isinstance(v, dict) and "$in" in v:
                val = _get_nested(doc, k, object())
                if val not in v["$in"]:
                    return False
            else:
                if _get_nested(doc, k, object()) != v:
                    return False
        return True

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[Union[Dict[str, int], List, Tuple]] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        with _LOCK:
            rows = [d.copy() for d in _STORE.get(self.name, []) if self._match(d, filt)]

        # ordenação (estável), do último critério para o primeiro
        sort_items = _normalize_sort(sort)
        for key, direction in reversed(sort_items):
            reverse = True if direction in (-1, "desc", "DESC") else False  # aceita ints e strings
            rows.sort(key=lambda x: _get_nested(x, key), reverse=reverse)

        if limit is not None:
            try:
                n = int(limit)
                if n >= 0:
                    rows = rows[:n]
            except Exception:
                pass

        return rows

    def find_one(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[Union[Dict[str, int], List, Tuple]] = None,
    ) -> Optional[Dict[str, Any]]:
        rows = list(self.find(filt=filt, sort=sort, limit=1))
        return rows[0] if rows else None

    def update_one(self, filt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        """
        Implementação simples de update:
          - Suporta $set (apenas chaves de 1 nível). Se precisar de set pontilhado, adaptar aqui.
        """
        with _LOCK:
            rows = _STORE.get(self.name, [])
            for d in rows:
                if self._match(d, filt):
                    if "$set" in update:
                        # $set raso (sem split por ponto) para manter simplicidade
                        d.update(update["$set"])
                    else:
                        d.update(update)
                    return
            if upsert:
                doc = dict(filt)
                if "$set" in update:
                    doc.update(update["$set"])
                self.insert_one(doc)

    def delete_many(self, filt: Dict[str, Any]) -> int:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            before = len(rows)
            rows[:] = [d for d in rows if not self._match(d, filt)]
            return before - len(rows)


def get_col(name: str) -> MemoryCollection:
    """Retorna uma 'coleção' em memória com interface básica (insert/find/update/delete)."""
    return MemoryCollection(name)
