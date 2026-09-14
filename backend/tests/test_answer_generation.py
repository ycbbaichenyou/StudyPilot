from __future__ import annotations

from collections.abc import Collection, Sequence

from sqlalchemy.orm import Session, sessionmaker

from app.llm import LLMMessage
from app.models import (
    Chunk,
    Document,
    DocumentContent,
    DocumentEmbeddingStatus,
    DocumentStatus,
    KnowledgeBase,
)
from app.services.answer_generation import (
    AnswerStatus,
    generate_answer,
)
from app.services.citation_validation import CitationStatus
from app.stores import ChromaSearchHit


class RecordingEmbeddingModel:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return [1.0, 0.0, 0.0]


class RecordingVectorStore:
    def __init__(self, hits: list[ChromaSearchHit]) -> None:
        self.hits = hits
        self.calls: list[tuple[list[float], set[str], int]] = []

    def search(
        self,
        query_embedding: list[float],
        *,
        allowed_record_ids: Collection[str],
        top_k: int,
    ) -> list[ChromaSearchHit]:
        self.calls.append((query_embedding, set(allowed_record_ids), top_k))
        return self.hits


class RecordingLLM:
    def __init__(self, answer: str = "增长率表示相对变化。[1]") -> None:
        self.answer = answer
        self.calls: list[tuple[LLMMessage, ...]] = []

    def generate(self, messages: Sequence[LLMMessage]) -> str:
        self.calls.append(tuple(messages))
        return self.answer


def add_searchable_chunk(
    session: Session,
    *,
    text: str = "增长率表示相对变化。",
) -> tuple[KnowledgeBase, Document, DocumentContent, Chunk]:
    knowledge_base = KnowledgeBase(name="Economics")
    document = Document(
        knowledge_base=knowledge_base,
        filename="stored-growth.txt",
        original_filename="growth.txt",
        file_type="txt",
        file_size=len(text.encode()),
        status=DocumentStatus.PARSED.value,
        embedding_status=DocumentEmbeddingStatus.EMBEDDED.value,
        embedding_generation_id="generation-a",
    )
    content = DocumentContent(
        document=document,
        sequence=0,
        text=text,
        source_type="line",
        source_start=1,
        source_end=1,
    )
    chunk = Chunk(
        document_content=content,
        sequence=0,
        text=text,
        start_offset=0,
        end_offset=len(text),
    )
    session.add(knowledge_base)
    session.commit()
    return knowledge_base, document, content, chunk


def hit_for(document: Document, chunk: Chunk) -> ChromaSearchHit:
    generation_id = document.embedding_generation_id
    assert generation_id is not None
    return ChromaSearchHit(
        record_id=f"{generation_id}:{chunk.id}",
        document_id=document.id,
        chunk_id=chunk.id,
        generation_id=generation_id,
        distance=0.125,
    )


def test_generate_answer_runs_explicit_pipeline_and_preserves_citations(
    test_session_factory: sessionmaker[Session],
) -> None:
    embedding_model = RecordingEmbeddingModel()
    llm_model = RecordingLLM()
    with test_session_factory() as session:
        knowledge_base, document, content, chunk = add_searchable_chunk(session)
        vector_store = RecordingVectorStore([hit_for(document, chunk)])

        result = generate_answer(
            session,
            knowledge_base.id,
            query="什么是增长率？",
            top_k=3,
            max_context_characters=6000,
            embedding_model=embedding_model,
            vector_store_factory=lambda: vector_store,
            llm_model=llm_model,
        )

    expected_context = "[1] growth.txt | line 1\n\n增长率表示相对变化。"
    assert result.status == AnswerStatus.ANSWERED
    assert result.answer == "增长率表示相对变化。[1]"
    assert result.citation_status == CitationStatus.VALID
    assert result.used_context_characters == len(expected_context)
    assert result.context_truncated is False
    assert len(result.citations) == 1
    assert result.citations[0].citation_number == 1
    assert result.citations[0].chunk_id == chunk.id
    assert result.citations[0].document_content_id == content.id
    assert result.citations[0].document_id == document.id
    assert result.citations[0].knowledge_base_id == knowledge_base.id
    assert embedding_model.queries == ["什么是增长率？"]
    assert vector_store.calls == [
        ([1.0, 0.0, 0.0], {f"generation-a:{chunk.id}"}, 3)
    ]
    assert len(llm_model.calls) == 1
    assert expected_context in llm_model.calls[0][1].content
    assert "[1]" in llm_model.calls[0][0].content


def test_generate_answer_skips_llm_when_retrieval_returns_no_hits(
    test_session_factory: sessionmaker[Session],
) -> None:
    llm_model = RecordingLLM()
    with test_session_factory() as session:
        knowledge_base, _, _, _ = add_searchable_chunk(session)
        vector_store = RecordingVectorStore([])

        result = generate_answer(
            session,
            knowledge_base.id,
            query="没有匹配的问题",
            embedding_model=RecordingEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
            llm_model=llm_model,
        )

    assert result.status == AnswerStatus.INSUFFICIENT_CONTEXT
    assert result.answer is None
    assert result.citations == ()
    assert result.citation_status == CitationStatus.MISSING
    assert result.used_context_characters == 0
    assert result.context_truncated is False
    assert llm_model.calls == []


def test_generate_answer_skips_llm_when_first_block_exceeds_budget(
    test_session_factory: sessionmaker[Session],
) -> None:
    llm_model = RecordingLLM()
    with test_session_factory() as session:
        knowledge_base, document, _, chunk = add_searchable_chunk(session)
        vector_store = RecordingVectorStore([hit_for(document, chunk)])

        result = generate_answer(
            session,
            knowledge_base.id,
            query="问题",
            max_context_characters=1,
            embedding_model=RecordingEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
            llm_model=llm_model,
        )

    assert result.status == AnswerStatus.INSUFFICIENT_CONTEXT
    assert result.answer is None
    assert result.citations == ()
    assert result.citation_status == CitationStatus.MISSING
    assert result.used_context_characters == 0
    assert result.context_truncated is True
    assert llm_model.calls == []


def test_generate_answer_returns_only_citations_referenced_by_model(
    test_session_factory: sessionmaker[Session],
) -> None:
    llm_model = RecordingLLM("第二段提供了定义。[2] [2]")
    with test_session_factory() as session:
        knowledge_base, document, _, first_chunk = add_searchable_chunk(session)
        second_text = "增长率可用于比较不同规模的数据。"
        second_content = DocumentContent(
            document=document,
            sequence=1,
            text=second_text,
            source_type="line",
            source_start=2,
            source_end=2,
        )
        second_chunk = Chunk(
            document_content=second_content,
            sequence=0,
            text=second_text,
            start_offset=0,
            end_offset=len(second_text),
        )
        session.add(second_content)
        session.commit()
        vector_store = RecordingVectorStore(
            [
                hit_for(document, first_chunk),
                ChromaSearchHit(
                    record_id=f"generation-a:{second_chunk.id}",
                    document_id=document.id,
                    chunk_id=second_chunk.id,
                    generation_id="generation-a",
                    distance=0.25,
                ),
            ]
        )

        result = generate_answer(
            session,
            knowledge_base.id,
            query="增长率有什么用途？",
            embedding_model=RecordingEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
            llm_model=llm_model,
        )

    assert result.status == AnswerStatus.ANSWERED
    assert result.citation_status == CitationStatus.VALID
    assert [item.citation_number for item in result.citations] == [2]
    assert [item.chunk_id for item in result.citations] == [second_chunk.id]
    assert len(llm_model.calls) == 1


def test_generate_answer_preserves_answer_and_marks_invalid_reference(
    test_session_factory: sessionmaker[Session],
) -> None:
    llm_model = RecordingLLM("增长率表示相对变化。[1] 另见 [9]")
    with test_session_factory() as session:
        knowledge_base, document, _, chunk = add_searchable_chunk(session)
        vector_store = RecordingVectorStore([hit_for(document, chunk)])

        result = generate_answer(
            session,
            knowledge_base.id,
            query="什么是增长率？",
            embedding_model=RecordingEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
            llm_model=llm_model,
        )

    assert result.answer == "增长率表示相对变化。[1] 另见 [9]"
    assert result.citation_status == CitationStatus.INVALID_REFERENCE
    assert [item.citation_number for item in result.citations] == [1]
    assert len(llm_model.calls) == 1


def test_generate_answer_marks_uncited_answer_missing(
    test_session_factory: sessionmaker[Session],
) -> None:
    llm_model = RecordingLLM("增长率表示相对变化。")
    with test_session_factory() as session:
        knowledge_base, document, _, chunk = add_searchable_chunk(session)
        vector_store = RecordingVectorStore([hit_for(document, chunk)])

        result = generate_answer(
            session,
            knowledge_base.id,
            query="什么是增长率？",
            embedding_model=RecordingEmbeddingModel(),
            vector_store_factory=lambda: vector_store,
            llm_model=llm_model,
        )

    assert result.answer == "增长率表示相对变化。"
    assert result.citations == ()
    assert result.citation_status == CitationStatus.MISSING
    assert len(llm_model.calls) == 1
