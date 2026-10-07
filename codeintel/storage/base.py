from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Dict, Any
from codeintel.core.models import Entity, Relation, Chunk, Generation

class GraphStore(ABC):
    @abstractmethod
    def add_entities(self, entities: List[Entity]) -> None:
        pass

    @abstractmethod
    def add_relations(self, relations: List[Relation]) -> None:
        pass

    @abstractmethod
    def get_entity(self, entity_id: str, generation_id: Optional[str] = None) -> Optional[Entity]:
        pass

    @abstractmethod
    def get_entities_by_name(self, name: str, repo_id: Optional[str] = None, generation_id: Optional[str] = None) -> List[Entity]:
        pass


    @abstractmethod
    def get_callers(self, entity_id: str, max_depth: int = 1, generation_id: Optional[str] = None,
                    include_heuristic: bool = False) -> List[Tuple[int, Entity, str]]:
        pass

    @abstractmethod
    def get_callees(self, entity_id: str, max_depth: int = 1, generation_id: Optional[str] = None,
                    include_heuristic: bool = False) -> List[Tuple[int, Entity, str]]:
        pass


    @abstractmethod
    def get_files_count(self, generation_id: Optional[str] = None) -> int:
        pass

    @abstractmethod
    def remove_generation_data(self, generation_id: str) -> None:
        pass

class LexicalIndex(ABC):
    @abstractmethod
    def get_chunks_for_file(self, file_id: str, generation_id: Optional[str] = None) -> List[Chunk]:
        pass

    @abstractmethod
    def index_chunks(self, chunks: List[Chunk], is_full_replacement: bool = True) -> None:
        pass

    @abstractmethod
    def get_chunk(self, chunk_id: str, generation_id: Optional[str] = None) -> Optional[Chunk]:
        pass

    @abstractmethod
    def search(self, query: str, limit: int = 20, generation_id: Optional[str] = None) -> List[Tuple[Chunk, float]]:
        pass

class GenerationStore(ABC):
    @abstractmethod
    def create_generation(
        self,
        repo_id: str,
        sequence: int,
        snapshot_hash: str,
        build_fingerprint: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Generation:
        pass

    @abstractmethod
    def activate_generation(self, generation_id: str) -> None:
        pass

    @abstractmethod
    def update_generation_metadata(self, generation_id: str, metadata: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    def delete_generation_data(self, generation_id: str) -> None:
        pass

    @abstractmethod
    def get_active_generation(self, repo_id: str) -> Optional[Generation]:
        pass

    @abstractmethod
    def list_generations(self, repo_id: str) -> List[Generation]:
        pass
