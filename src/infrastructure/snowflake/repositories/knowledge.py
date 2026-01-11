"""
Knowledge repository for RAG (Retrieval-Augmented Generation).

This module handles semantic search over the swimming technique knowledge base.
Uses Snowflake Cortex embeddings for similarity search.
"""

import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeChunk:
    """
    A single piece of swimming technique knowledge.
    
    Represents one retrieved chunk from the knowledge base,
    ready to be injected into an AI prompt.
    """
    knowledge_id: str
    source: str
    topic: str
    subtopic: Optional[str]
    title: str
    content: str
    similarity_score: float = 0.0


class KnowledgeRepository:
    """
    Repository for querying swimming technique knowledge.
    
    Uses Snowflake Cortex for semantic search:
    1. Convert query text to embedding using EMBED_TEXT_768
    2. Find most similar chunks using vector distance
    3. Return ranked results
    
    Why Snowflake Cortex over a dedicated vector DB:
    - Already using Snowflake for other data
    - No additional infrastructure to manage
    - Built-in embedding function (no external API calls)
    - Good enough performance for our scale (<1000 chunks)
    """
    
    def __init__(self, connection) -> None:
        """
        Initialize repository with a database connection.
        
        Args:
            connection: Snowflake connection (or mock for testing)
        """
        self._conn = connection
    
    def search_similar(
        self,
        query: str,
        limit: int = 5,
        topic_filter: Optional[str] = None,
        min_score: float = 0.0
    ) -> list[KnowledgeChunk]:
        """
        Find knowledge chunks semantically similar to the query.
        
        This is the main RAG retrieval method. Given a query like
        "how do I improve my freestyle catch?", it finds relevant
        technique knowledge to augment the AI's response.
        
        Args:
            query: Natural language search query
            limit: Maximum chunks to return (default 5)
            topic_filter: Optional topic to filter by (e.g., 'freestyle_catch')
            min_score: Minimum similarity score (0-1, higher = more similar)
        
        Returns:
            List of KnowledgeChunk objects, ranked by similarity
        
        Example:
            chunks = repo.search_similar(
                query="My elbow keeps dropping during the catch",
                limit=3,
                topic_filter="freestyle_catch"
            )
            
            for chunk in chunks:
                print(f"{chunk.topic}: {chunk.content[:100]}...")
        """
        cursor = self._conn.cursor()
        
        try:
            # Build the query with optional topic filter
            # Uses Snowflake Cortex for:
            # 1. EMBED_TEXT_768 - convert query to embedding vector
            # 2. VECTOR_COSINE_SIMILARITY - find similar chunks
            
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
                
                # Filter by minimum score
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
            # Return empty list on error - don't break the main flow
            return []
        
        finally:
            cursor.close()
    
    def search_by_topics(
        self,
        topics: list[str],
        limit_per_topic: int = 2
    ) -> list[KnowledgeChunk]:
        """
        Get knowledge chunks for specific topics.
        
        Unlike search_similar(), this does exact topic matching.
        Useful when you know what topics are relevant based on
        the stroke type or detected issues.
        
        Args:
            topics: List of topic names (e.g., ['freestyle_catch', 'freestyle_pull'])
            limit_per_topic: Max chunks per topic
        
        Returns:
            List of KnowledgeChunk objects
        
        Example:
            # Get knowledge for freestyle stroke analysis
            chunks = repo.search_by_topics(
                topics=['freestyle_body_position', 'freestyle_catch', 'freestyle_breathing'],
                limit_per_topic=2
            )
        """
        cursor = self._conn.cursor()
        
        try:
            # Build placeholders for IN clause
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
            
            # Group by topic and limit
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
        """
        Get relevant knowledge for a specific stroke analysis.
        
        High-level method that combines topic-based and semantic search.
        Used by the coach to get relevant technique knowledge before
        generating coaching feedback.
        
        Args:
            stroke_type: The stroke being analyzed (freestyle, backstroke, etc.)
            analysis_summary: Optional summary text to use for semantic search
            limit: Maximum total chunks to return
        
        Returns:
            List of KnowledgeChunk objects, most relevant first
        """
        # Map stroke types to topic prefixes
        stroke_topic_prefixes = {
            'freestyle': ['freestyle_', 'drills'],
            'backstroke': ['backstroke_', 'drills'],
            'breaststroke': ['breaststroke_', 'drills'],
            'butterfly': ['butterfly_', 'drills'],
        }
        
        stroke_lower = stroke_type.lower()
        prefixes = stroke_topic_prefixes.get(stroke_lower, ['drills'])
        
        if analysis_summary:
            # Use semantic search with the analysis summary
            return self.search_similar(
                query=analysis_summary,
                limit=limit
            )
        else:
            # Fall back to topic-based search
            cursor = self._conn.cursor()
            
            try:
                # Get topics matching our stroke
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
        """
        Get a specific knowledge chunk by ID.
        
        Useful for debugging or when you need to reference
        specific content.
        """
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
        """Get total number of knowledge chunks in the database."""
        cursor = self._conn.cursor()
        
        try:
            cursor.execute("SELECT COUNT(*) FROM coaching_knowledge")
            result = cursor.fetchone()
            return result[0] if result else 0
        finally:
            cursor.close()

