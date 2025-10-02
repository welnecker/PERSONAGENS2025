# core/database.py
from typing import Any, Dict, List, Optional, Iterable, Tuple
from threading import RLock
import uuid

# Armazenamento em memória (processo local)
_STORE: Dict[str, List[Dict[str, Any]]] = {}
_LOCK = RLock()


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
        if not filt:
            return True
        for k, v in filt.items():
            if isinstance(v, dict) and "$in" in v:
                if doc.get(k) not in v["$in"]:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True

    def find(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
        limit: Optional[int] = None,
    ) -> Iterable[Dict[str, Any]]:
        with _LOCK:
            rows = [d.copy() for d in _STORE.get(self.name, []) if self._match(d, filt)]
        if sort:
            # aplica múltiplas chaves, da última para a primeira
            for key, direction in reversed(sort):
                rows.sort(key=lambda x: x.get(key), reverse=(direction < 0))
        if limit:
            rows = rows[:limit]
        return rows

    def find_one(
        self,
        filt: Optional[Dict[str, Any]] = None,
        sort: Optional[List[Tuple[str, int]]] = None,
    ) -> Optional[Dict[str, Any]]:
        rows = list(self.find(filt=filt, sort=sort, limit=1))
        return rows[0] if rows else None

    def update_one(self, filt: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> None:
        with _LOCK:
            rows = _STORE.get(self.name, [])
            for d in rows:
                if self._match(d, filt):
                    if "$set" in update:
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
