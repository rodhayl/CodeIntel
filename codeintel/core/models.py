from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Any, Optional, Tuple


class TrustClass(str, Enum):
    EXACT = "EXACT"
    RESOLVED = "RESOLVED"
    HEURISTIC = "HEURISTIC"
    INFERRED = "INFERRED"


class EntityKind(str, Enum):
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    CLASS = "CLASS"
    INTERFACE = "INTERFACE"
    TYPE_ALIAS = "TYPE_ALIAS"
    VARIABLE = "VARIABLE"
    MODULE = "MODULE"
    ENDPOINT = "ENDPOINT"
    UNKNOWN = "UNKNOWN"


class RelationType(str, Enum):
    CALLS = "CALLS"
    DEFINES = "DEFINES"
    REFERENCES = "REFERENCES"
    IMPORTS = "IMPORTS"
    CONTAINS = "CONTAINS"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"




@dataclass
class Generation:
    generation_id: str
    repo_id: str
    sequence: int
    snapshot_hash: str
    created_at: str = ""
    is_active: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def build_fingerprint(self) -> Optional[str]:
        return self.metadata.get("build_fingerprint")


@dataclass
class FileRecord:
    file_id: str
    repo_id: str
    generation_id: str
    rel_path: str
    content_hash: str
    size_bytes: int


@dataclass
class Entity:
    entity_id: str
    repo_id: str
    file_id: str
    generation_id: str
    name: str
    qualified_name: str
    kind: EntityKind
    span: Tuple[int, int, int, int]
    content_hash: str = ""
    docstring: Optional[str] = None
    signature: Optional[str] = None
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Relation:
    relation_id: str
    repo_id: str
    generation_id: str
    source_id: str
    target_id: str
    rel_type: RelationType
    file_id: str
    span: Tuple[int, int, int, int]
    trust_class: TrustClass = TrustClass.EXACT
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    chunk_id: str
    file_id: str
    generation_id: str
    rel_path: str
    span: Tuple[int, int, int, int]
    content: str
    content_hash: str
    entity_ids: List[str] = field(default_factory=list)


@dataclass
class Evidence:
    """Canonical, model-neutral Evidence IR."""
    evidence_id: str
    generation_id: str
    file_id: str
    path: str
    span: Tuple[int, int, int, int]
    content_hash: str
    trust_class: TrustClass
    entity_ids: List[str] = field(default_factory=list)
    relation_ids: List[str] = field(default_factory=list)
    reason_selected: str = ""
    source_text: str = ""
    score: float = 1.0
    # Exact syntax target, not a claim about dependency/relevance completeness.
    symbol_span: Optional[Tuple[int, int, int, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
