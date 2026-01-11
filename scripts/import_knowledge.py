#!/usr/bin/env python3
"""
Import swimming technique knowledge into Snowflake for RAG.

Parses RAG_SWIMMING_KNOWLEDGE.md and inserts into coaching_knowledge table.
Uses Snowflake Cortex EMBED_TEXT_768 for generating embeddings.

Usage:
    python scripts/import_knowledge.py

Requires:
    - .env file with Snowflake credentials
    - RAG_SWIMMING_KNOWLEDGE.md in project root
"""

import os
import re
import sys
import uuid
from pathlib import Path

# Add src to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def parse_knowledge_markdown(filepath: str) -> list[dict]:
    """
    Parse the knowledge base markdown file into chunks.
    
    Each chunk is delimited by '---' and contains:
    - Source: (required)
    - Topic: (required)
    - Subtopic: (optional)
    - Content: (everything else)
    
    Returns list of dicts with extracted fields.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split by --- separator
    raw_chunks = content.split('\n---\n')
    
    chunks = []
    
    for raw_chunk in raw_chunks:
        raw_chunk = raw_chunk.strip()
        
        # Skip empty chunks
        if not raw_chunk:
            continue
        
        # Remove header lines (## or ###) - they might be at the start
        # but keep content after the header
        lines = raw_chunk.split('\n')
        filtered_lines = []
        for line in lines:
            # Skip markdown headers
            if line.startswith('#'):
                continue
            filtered_lines.append(line)
        
        raw_chunk = '\n'.join(filtered_lines).strip()
        
        if not raw_chunk:
            continue
        
        # Extract metadata using regex
        source_match = re.search(r'\*\*Source:\*\*\s*(.+?)(?:\s*\n|$)', raw_chunk)
        topic_match = re.search(r'\*\*Topic:\*\*\s*(\S+)', raw_chunk)
        subtopic_match = re.search(r'\*\*Subtopic:\*\*\s*(\S+)', raw_chunk)
        
        # Skip chunks without required fields
        if not source_match or not topic_match:
            continue
        
        source = source_match.group(1).strip()
        topic = topic_match.group(1).strip()
        subtopic = subtopic_match.group(1).strip() if subtopic_match else None
        
        # Extract content - everything that's not metadata lines
        # Remove metadata lines
        content_lines = []
        for line in raw_chunk.split('\n'):
            if line.startswith('**Source:**'):
                continue
            if line.startswith('**Topic:**'):
                continue
            if line.startswith('**Subtopic:**'):
                continue
            content_lines.append(line)
        
        content_text = '\n'.join(content_lines).strip()
        
        # Skip if no meaningful content
        if len(content_text) < 50:
            continue
        
        # Generate a title from first line or sentence
        first_line = content_text.split('\n')[0]
        title = first_line[:200] if len(first_line) <= 200 else first_line[:197] + '...'
        
        chunks.append({
            'knowledge_id': str(uuid.uuid4()),
            'source': source,
            'topic': topic,
            'subtopic': subtopic,
            'title': title,
            'content': content_text
        })
    
    return chunks


def insert_knowledge_to_snowflake(chunks: list[dict], dry_run: bool = False):
    """
    Insert knowledge chunks into Snowflake with embeddings.
    
    Uses Snowflake Cortex EMBED_TEXT_768 to generate embeddings inline.
    """
    import snowflake.connector
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    import base64
    
    # Get credentials from environment
    account = os.getenv('SNOWFLAKE_ACCOUNT')
    user = os.getenv('SNOWFLAKE_USER')
    password = os.getenv('SNOWFLAKE_PASSWORD')
    private_key_path = os.getenv('SNOWFLAKE_PRIVATE_KEY_PATH')
    private_key_base64 = os.getenv('SNOWFLAKE_PRIVATE_KEY_BASE64')
    database = os.getenv('SNOWFLAKE_DATABASE', 'SWIMCOACH')
    schema = os.getenv('SNOWFLAKE_SCHEMA', 'COACHING')
    warehouse = os.getenv('SNOWFLAKE_WAREHOUSE')
    role = os.getenv('SNOWFLAKE_ROLE')
    
    if not account or not user:
        print("ERROR: Missing SNOWFLAKE_ACCOUNT or SNOWFLAKE_USER")
        return False
    
    # Build connection params
    conn_params = {
        'account': account,
        'user': user,
        'database': database,
        'schema': schema,
    }
    
    if warehouse:
        conn_params['warehouse'] = warehouse
    if role:
        conn_params['role'] = role
    
    # Handle authentication (key-pair or password)
    if private_key_base64:
        print("Using base64-encoded private key authentication")
        key_bytes = base64.b64decode(private_key_base64)
        private_key = serialization.load_pem_private_key(
            key_bytes,
            password=None,
            backend=default_backend()
        )
        conn_params['private_key'] = private_key
    elif private_key_path and os.path.exists(private_key_path):
        print(f"Using private key file: {private_key_path}")
        with open(private_key_path, 'rb') as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None,
                backend=default_backend()
            )
        conn_params['private_key'] = private_key
    elif password:
        print("Using password authentication")
        conn_params['password'] = password
    else:
        print("ERROR: No authentication method available (password, key file, or base64 key)")
        return False
    
    if dry_run:
        print("\n=== DRY RUN - No data will be inserted ===\n")
        for chunk in chunks:
            print(f"Would insert: {chunk['topic']}/{chunk['subtopic']} - {chunk['title'][:50]}...")
        print(f"\nTotal: {len(chunks)} chunks")
        return True
    
    try:
        print(f"Connecting to Snowflake account: {account}")
        conn = snowflake.connector.connect(**conn_params)
        cursor = conn.cursor()
        
        print(f"Using database {database}, schema {schema}")
        cursor.execute(f"USE DATABASE {database}")
        cursor.execute(f"USE SCHEMA {schema}")
        
        # Check if table exists
        cursor.execute("""
            SELECT COUNT(*) FROM information_schema.tables 
            WHERE table_schema = %s AND table_name = 'COACHING_KNOWLEDGE'
        """, (schema.upper(),))
        
        if cursor.fetchone()[0] == 0:
            print("ERROR: coaching_knowledge table doesn't exist. Run setup_snowflake.sql first.")
            return False
        
        # Insert each chunk with embedding
        # Using Snowflake Cortex EMBED_TEXT_768 function
        insert_sql = """
            INSERT INTO coaching_knowledge (
                knowledge_id, source, topic, subtopic, title, content, content_embedding
            )
            SELECT 
                %s, %s, %s, %s, %s, %s,
                SNOWFLAKE.CORTEX.EMBED_TEXT_768('e5-base-v2', %s)
        """
        
        inserted = 0
        errors = 0
        
        for chunk in chunks:
            try:
                cursor.execute(insert_sql, (
                    chunk['knowledge_id'],
                    chunk['source'],
                    chunk['topic'],
                    chunk['subtopic'],
                    chunk['title'],
                    chunk['content'],
                    chunk['content']  # Same content for embedding
                ))
                inserted += 1
                print(f"[OK] Inserted: {chunk['topic']}/{chunk['subtopic'] or 'general'}")
            except Exception as e:
                errors += 1
                print(f"[ERR] Error inserting {chunk['topic']}: {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"\n=== Import Complete ===")
        print(f"Inserted: {inserted}")
        print(f"Errors: {errors}")
        
        return errors == 0
        
    except Exception as e:
        print(f"ERROR connecting to Snowflake: {e}")
        return False


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Import swimming knowledge to Snowflake')
    parser.add_argument('--dry-run', action='store_true', help='Parse only, don\'t insert')
    parser.add_argument('--file', default='RAG_SWIMMING_KNOWLEDGE.md', help='Knowledge file path')
    args = parser.parse_args()
    
    # Find the knowledge file
    filepath = args.file
    if not os.path.exists(filepath):
        # Try relative to script location
        script_dir = Path(__file__).parent.parent
        filepath = script_dir / args.file
    
    if not os.path.exists(filepath):
        print(f"ERROR: Cannot find {args.file}")
        sys.exit(1)
    
    print(f"Parsing knowledge from: {filepath}")
    chunks = parse_knowledge_markdown(str(filepath))
    print(f"Found {len(chunks)} knowledge chunks")
    
    if not chunks:
        print("ERROR: No valid chunks found in knowledge file")
        sys.exit(1)
    
    # Show summary by topic
    topics = {}
    for chunk in chunks:
        topic = chunk['topic']
        topics[topic] = topics.get(topic, 0) + 1
    
    print("\nChunks by topic:")
    for topic, count in sorted(topics.items()):
        print(f"  {topic}: {count}")
    
    # Insert to Snowflake
    success = insert_knowledge_to_snowflake(chunks, dry_run=args.dry_run)
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

