"""
Knowledge repository for RAG. Semantic search via Snowflake Cortex embeddings.
"""

import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeChunk:
    """Retrieved chunk from knowledge base, ready for prompt injection."""
    knowledge_id: str
    source: str
    topic: str
    subtopic: Optional[str]
    title: str
    content: str
    similarity_score: float = 0.0


class KnowledgeRepository:
    """Semantic search over swim technique knowledge via Snowflake Cortex."""

    def __init__(self, connection) -> None:
        self._conn = connection
    
    def search_similar(
        self,
        query: str,
        limit: int = 5,
        topic_filter: Optional[str] = None,
        min_score: float = 0.0
    ) -> list[KnowledgeChunk]:
        """Semantic search for chunks similar to query. Main RAG retrieval."""
        cursor = self._conn.cursor()
        
        try:
            if topic_filter:
                sql = """
                    SELECT 
                        knowledge_id,
                        source,
                        topic,
                        subtopic,
                        title,
                        content,
                        VECTOR_COSINE_SIMILARITY(
                            content_embedding,
                            SNOWFLAKE.CORTEX.EMBED_TEXT_768('e5-base-v2', %s)
                        ) AS similarity_score
                    FROM coaching_knowledge
                    WHERE topic = %s
                      AND content_embedding IS NOT NULL
                    ORDER BY similarity_score DESC
                    LIMIT %s
                """
                cursor.execute(sql, (query, topic_filter, limit))
            else:
                sql = """
                    SELECT 
                        knowledge_id,
                        source,
                        topic,
                        subtopic,
                        title,
                        content,
                        VECTOR_COSINE_SIMILARITY(
                            content_embedding,
                            SNOWFLAKE.CORTEX.EMBED_TEXT_768('e5-base-v2', %s)
                        ) AS similarity_score
                    FROM coaching_knowledge
                    WHERE content_embedding IS NOT NULL
                    ORDER BY similarity_score DESC
                    LIMIT %s
                """
                cursor.execute(sql, (query, limit))
            
            results = cursor.fetchall()
            
            chunks = []
            for row in results:
                score = float(row[6]) if row[6] else 0.0

                if score < min_score:
                    continue
                
                chunks.append(KnowledgeChunk(
                    knowledge_id=row[0],
                    source=row[1],
                    topic=row[2],
                    subtopic=row[3],
                    title=row[4],
                    content=row[5],
                    similarity_score=score
                ))
            
            logger.info(
                "Knowledge search completed",
                extra={
                    "query_length": len(query),
                    "results_count": len(chunks),
                    "topic_filter": topic_filter
                }
            )
            
            return chunks
        
        except Exception as e:
            logger.error(
                "Knowledge search failed",
                extra={"query": query[:100], "error": str(e)}
            )
            return []
        
        finally:
            cursor.close()
    
    def search_by_topics(
        self,
        topics: list[str],
        limit_per_topic: int = 2
    ) -> list[KnowledgeChunk]:
        """Exact topic matching (vs semantic). For known-relevant topics."""
        cursor = self._conn.cursor()
        
        try:
            placeholders = ', '.join(['%s'] * len(topics))
            
            sql = f"""
                SELECT 
                    knowledge_id,
                    source,
                    topic,
                    subtopic,
                    title,
                    content,
                    relevance_score
                FROM coaching_knowledge
                WHERE topic IN ({placeholders})
                ORDER BY topic, relevance_score DESC
            """
            
            cursor.execute(sql, tuple(topics))
            results = cursor.fetchall()
            
            topic_counts = {}
            chunks = []
            
            for row in results:
                topic = row[2]
                if topic_counts.get(topic, 0) >= limit_per_topic:
                    continue
                
                topic_counts[topic] = topic_counts.get(topic, 0) + 1
                
                chunks.append(KnowledgeChunk(
                    knowledge_id=row[0],
                    source=row[1],
                    topic=row[2],
                    subtopic=row[3],
                    title=row[4],
                    content=row[5],
                    similarity_score=float(row[6]) if row[6] else 1.0
                ))
            
            return chunks
        
        except Exception as e:
            logger.error(
                "Topic-based search failed",
                extra={"topics": topics, "error": str(e)}
            )
            return []
        
        finally:
            cursor.close()
    
    def get_relevant_for_stroke(
        self,
        stroke_type: str,
        analysis_summary: Optional[str] = None,
        limit: int = 5
    ) -> list[KnowledgeChunk]:
        """Combines topic + semantic search for a stroke analysis."""
        stroke_topic_prefixes = {
            'freestyle': ['freestyle_', 'drills'],
            'backstroke': ['backstroke_', 'drills'],
            'breaststroke': ['breaststroke_', 'drills'],
            'butterfly': ['butterfly_', 'drills'],
        }
        
        stroke_lower = stroke_type.lower()
        prefixes = stroke_topic_prefixes.get(stroke_lower, ['drills'])
        
        if analysis_summary:
            return self.search_similar(
                query=analysis_summary,
                limit=limit
            )
        else:
            cursor = self._conn.cursor()

            try:
                like_clauses = ' OR '.join([f"topic LIKE %s" for _ in prefixes])
                params = tuple(f"{p}%" for p in prefixes)
                
                sql = f"""
                    SELECT 
                        knowledge_id,
                        source,
                        topic,
                        subtopic,
                        title,
                        content,
                        relevance_score
                    FROM coaching_knowledge
                    WHERE {like_clauses}
                    ORDER BY relevance_score DESC
                    LIMIT %s
                """
                
                cursor.execute(sql, params + (limit,))
                results = cursor.fetchall()
                
                return [
                    KnowledgeChunk(
                        knowledge_id=row[0],
                        source=row[1],
                        topic=row[2],
                        subtopic=row[3],
                        title=row[4],
                        content=row[5],
                        similarity_score=float(row[6]) if row[6] else 1.0
                    )
                    for row in results
                ]
            
            except Exception as e:
                logger.error(
                    "Stroke knowledge retrieval failed",
                    extra={"stroke_type": stroke_type, "error": str(e)}
                )
                return []
            
            finally:
                cursor.close()
    
    def get_chunk_by_id(self, knowledge_id: str) -> Optional[KnowledgeChunk]:
        cursor = self._conn.cursor()
        
        try:
            cursor.execute("""
                SELECT 
                    knowledge_id,
                    source,
                    topic,
                    subtopic,
                    title,
                    content,
                    relevance_score
                FROM coaching_knowledge
                WHERE knowledge_id = %s
            """, (knowledge_id,))
            
            row = cursor.fetchone()
            
            if row:
                return KnowledgeChunk(
                    knowledge_id=row[0],
                    source=row[1],
                    topic=row[2],
                    subtopic=row[3],
                    title=row[4],
                    content=row[5],
                    similarity_score=float(row[6]) if row[6] else 1.0
                )
            
            return None
        
        finally:
            cursor.close()
    
    def count_chunks(self) -> int:
        cursor = self._conn.cursor()
        
        try:
            cursor.execute("SELECT COUNT(*) FROM coaching_knowledge")
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            cursor.close()

