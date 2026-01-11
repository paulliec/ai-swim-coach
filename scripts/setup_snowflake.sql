-- SwimCoach AI - Snowflake Schema
-- 
-- This schema is designed for:
-- 1. Tracking coaching sessions and their outcomes
-- 2. Storing analysis results for future reference
-- 3. Enabling analytics on technique patterns across swimmers
--
-- Design decisions:
-- - VARIANT columns for flexible JSON storage (feedback, observations)
-- - Separate tables for videos and analyses (1:1 now, but could be 1:many)
-- - Timestamps in UTC, always
-- - UUIDs as primary keys for easier cross-system integration

-- Create database and schema if they don't exist
CREATE DATABASE IF NOT EXISTS SWIMCOACH;
CREATE SCHEMA IF NOT EXISTS SWIMCOACH.COACHING;

USE SCHEMA SWIMCOACH.COACHING;

-- ---------------------------------------------------------------------------
-- Core Tables
-- ---------------------------------------------------------------------------

-- Video metadata (not the video itself - that's in object storage)
CREATE OR REPLACE TABLE videos (
    video_id VARCHAR(36) PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    storage_path VARCHAR(1000) NOT NULL,
    duration_seconds FLOAT,
    resolution_width INT,
    resolution_height INT,
    fps FLOAT,
    file_size_bytes BIGINT,
    uploaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    
    -- Metadata for analytics
    stroke_type VARCHAR(50),
    user_provided_notes TEXT,
    
    -- Soft delete support
    deleted_at TIMESTAMP_NTZ
);

-- Analysis results from the AI coach
CREATE OR REPLACE TABLE analyses (
    analysis_id VARCHAR(36) PRIMARY KEY,
    video_id VARCHAR(36) NOT NULL REFERENCES videos(video_id),
    
    -- What the AI observed
    stroke_type VARCHAR(50),
    summary TEXT,
    -- VARIANT columns: Store JSON data
    -- NOTE: snowflake-connector-python returns VARIANT as JSON strings
    --       Application code must parse with json.loads() before accessing
    observations VARIANT,  -- Array of observation objects
    feedback VARIANT,      -- Array of feedback objects with priorities
    
    -- Processing metadata
    frame_count_analyzed INT,
    model_used VARCHAR(100),
    analyzed_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    
    -- Cost tracking (good for budgeting)
    estimated_tokens_used INT,
    
    CONSTRAINT fk_video FOREIGN KEY (video_id) REFERENCES videos(video_id)
);

-- Coaching sessions tie together video + analysis + conversation
CREATE OR REPLACE TABLE coaching_sessions (
    session_id VARCHAR(36) PRIMARY KEY,
    video_id VARCHAR(36) REFERENCES videos(video_id),
    analysis_id VARCHAR(36) REFERENCES analyses(analysis_id),
    
    -- User identification (from Clerk)
    user_id VARCHAR(255),  -- Clerk user ID
    
    created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    updated_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    
    -- Session metadata
    status VARCHAR(50) DEFAULT 'active',  -- active, completed, abandoned
    
    CONSTRAINT fk_video_session FOREIGN KEY (video_id) REFERENCES videos(video_id),
    CONSTRAINT fk_analysis FOREIGN KEY (analysis_id) REFERENCES analyses(analysis_id)
);

-- Index for faster user session queries
CREATE INDEX idx_coaching_sessions_user_id ON coaching_sessions(user_id);

-- Conversation messages within a session
CREATE OR REPLACE TABLE messages (
    message_id VARCHAR(36) PRIMARY KEY,
    session_id VARCHAR(36) NOT NULL REFERENCES coaching_sessions(session_id),
    
    role VARCHAR(20) NOT NULL,  -- 'user' or 'assistant'
    content TEXT NOT NULL,
    
    created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    
    -- Order matters for conversations
    sequence_number INT NOT NULL,
    
    CONSTRAINT fk_session FOREIGN KEY (session_id) REFERENCES coaching_sessions(session_id)
);

-- ---------------------------------------------------------------------------
-- Analytics Views
-- ---------------------------------------------------------------------------

-- Common technique issues across all analyses
CREATE OR REPLACE VIEW common_technique_issues AS
SELECT 
    f.value:category::STRING AS category,
    f.value:priority::STRING AS priority,
    COUNT(*) AS occurrence_count,
    LISTAGG(DISTINCT f.value:observation:description::STRING, ' | ') 
        WITHIN GROUP (ORDER BY a.analyzed_at DESC) AS example_descriptions
FROM analyses a,
    LATERAL FLATTEN(input => a.feedback) f
WHERE a.analyzed_at >= DATEADD(month, -3, CURRENT_TIMESTAMP())
GROUP BY 1, 2
ORDER BY occurrence_count DESC;

-- Session engagement metrics
CREATE OR REPLACE VIEW session_engagement AS
SELECT 
    s.session_id,
    v.stroke_type,
    s.created_at,
    s.status,
    COUNT(m.message_id) AS message_count,
    DATEDIFF('minute', s.created_at, MAX(m.created_at)) AS session_duration_minutes,
    MAX(CASE WHEN m.role = 'user' THEN m.created_at END) AS last_user_message
FROM coaching_sessions s
LEFT JOIN messages m ON s.session_id = m.session_id
LEFT JOIN videos v ON s.video_id = v.video_id
GROUP BY 1, 2, 3, 4;

-- ---------------------------------------------------------------------------
-- Usage Limits Table (Rate Limiting)
-- ---------------------------------------------------------------------------

/*
Track usage limits for rate limiting.
Prevents abuse by limiting analyses per user/IP per day.
*/

CREATE OR REPLACE TABLE usage_limits (
    limit_id VARCHAR(36) PRIMARY KEY,
    
    -- Identifier (user_id from Clerk, or IP address for anonymous)
    identifier VARCHAR(255) NOT NULL,
    identifier_type VARCHAR(20) NOT NULL, -- 'user_id' or 'ip_address'
    
    -- Usage tracking
    resource_type VARCHAR(50) NOT NULL,  -- 'video_analysis'
    usage_count INT NOT NULL DEFAULT 0,
    limit_max INT NOT NULL,  -- Max allowed per period
    
    -- Time period tracking
    period_start TIMESTAMP_NTZ NOT NULL,
    period_end TIMESTAMP_NTZ NOT NULL,
    
    -- Metadata
    created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    updated_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Index for fast lookups
CREATE INDEX idx_usage_limits_lookup ON usage_limits(identifier, identifier_type, resource_type, period_start);

COMMENT ON TABLE usage_limits IS 'Rate limiting: track resource usage per user/IP per time period';

-- ---------------------------------------------------------------------------
-- RAG Knowledge Base (Coaching Content)
-- ---------------------------------------------------------------------------

/*
Stores curated swimming technique knowledge for RAG.
Content is embedded using Snowflake Cortex for semantic search.
Used to augment AI coaching with expert knowledge from sources like USMS.
*/

CREATE OR REPLACE TABLE coaching_knowledge (
    knowledge_id VARCHAR(36) PRIMARY KEY,
    
    -- Content categorization
    source VARCHAR(100) NOT NULL,      -- 'usms', 'effortless_swimming', 'swimsmooth', etc.
    topic VARCHAR(100) NOT NULL,       -- 'freestyle_catch', 'breathing', 'flip_turns'
    subtopic VARCHAR(100),             -- Optional finer categorization
    
    -- The actual content
    title VARCHAR(500),
    content TEXT NOT NULL,
    
    -- Vector embedding for semantic search (Snowflake Cortex)
    -- Using e5-base-v2 model which produces 768-dimensional vectors
    content_embedding VECTOR(FLOAT, 768),
    
    -- Metadata
    created_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    updated_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    
    -- Quality/relevance scoring (can be updated based on usage)
    relevance_score FLOAT DEFAULT 1.0
);

-- Indexes for fast lookups
CREATE INDEX idx_knowledge_topic ON coaching_knowledge(topic);
CREATE INDEX idx_knowledge_source ON coaching_knowledge(source);

COMMENT ON TABLE coaching_knowledge IS 'RAG knowledge base: curated swimming technique content with embeddings';

-- ---------------------------------------------------------------------------
-- Stored Procedures
-- ---------------------------------------------------------------------------

-- Create a new coaching session with all related records
CREATE OR REPLACE PROCEDURE create_coaching_session(
    p_video_id VARCHAR,
    p_filename VARCHAR,
    p_storage_path VARCHAR,
    p_duration_seconds FLOAT,
    p_stroke_type VARCHAR
)
RETURNS VARIANT
LANGUAGE SQL
AS
$$
DECLARE
    v_session_id VARCHAR;
BEGIN
    v_session_id := UUID_STRING();
    
    -- Insert video metadata
    INSERT INTO videos (video_id, filename, storage_path, duration_seconds, stroke_type)
    VALUES (p_video_id, p_filename, p_storage_path, p_duration_seconds, p_stroke_type);
    
    -- Create session
    INSERT INTO coaching_sessions (session_id, video_id)
    VALUES (v_session_id, p_video_id);
    
    RETURN OBJECT_CONSTRUCT(
        'session_id', v_session_id,
        'video_id', p_video_id,
        'status', 'created'
    );
END;
$$;

-- Record analysis results
CREATE OR REPLACE PROCEDURE record_analysis(
    p_analysis_id VARCHAR,
    p_session_id VARCHAR,
    p_video_id VARCHAR,
    p_stroke_type VARCHAR,
    p_summary TEXT,
    p_observations VARIANT,
    p_feedback VARIANT,
    p_frame_count INT,
    p_model_used VARCHAR
)
RETURNS VARIANT
LANGUAGE SQL
AS
$$
BEGIN
    -- Insert analysis
    INSERT INTO analyses (
        analysis_id, video_id, stroke_type, summary, 
        observations, feedback, frame_count_analyzed, model_used
    )
    VALUES (
        p_analysis_id, p_video_id, p_stroke_type, p_summary,
        p_observations, p_feedback, p_frame_count, p_model_used
    );
    
    -- Link to session
    UPDATE coaching_sessions
    SET analysis_id = p_analysis_id,
        updated_at = CURRENT_TIMESTAMP()
    WHERE session_id = p_session_id;
    
    RETURN OBJECT_CONSTRUCT(
        'analysis_id', p_analysis_id,
        'status', 'recorded'
    );
END;
$$;

-- Add message to conversation
CREATE OR REPLACE PROCEDURE add_message(
    p_session_id VARCHAR,
    p_role VARCHAR,
    p_content TEXT
)
RETURNS VARIANT
LANGUAGE SQL
AS
$$
DECLARE
    v_message_id VARCHAR;
    v_sequence INT;
BEGIN
    v_message_id := UUID_STRING();
    
    -- Get next sequence number
    SELECT COALESCE(MAX(sequence_number), 0) + 1 
    INTO v_sequence
    FROM messages 
    WHERE session_id = p_session_id;
    
    -- Insert message
    INSERT INTO messages (message_id, session_id, role, content, sequence_number)
    VALUES (v_message_id, p_session_id, p_role, p_content, v_sequence);
    
    -- Update session timestamp
    UPDATE coaching_sessions
    SET updated_at = CURRENT_TIMESTAMP()
    WHERE session_id = p_session_id;
    
    RETURN OBJECT_CONSTRUCT(
        'message_id', v_message_id,
        'sequence_number', v_sequence
    );
END;
$$;

-- ---------------------------------------------------------------------------
-- Sample Queries for the Application
-- ---------------------------------------------------------------------------

-- Get full session with all data (for loading a session)
-- This would be wrapped in a repository method
/*
SELECT 
    s.session_id,
    s.created_at,
    s.status,
    v.filename,
    v.duration_seconds,
    v.stroke_type,
    a.summary AS analysis_summary,
    a.feedback AS analysis_feedback,
    ARRAY_AGG(
        OBJECT_CONSTRUCT(
            'role', m.role,
            'content', m.content,
            'timestamp', m.created_at
        )
    ) WITHIN GROUP (ORDER BY m.sequence_number) AS conversation
FROM coaching_sessions s
LEFT JOIN videos v ON s.video_id = v.video_id
LEFT JOIN analyses a ON s.analysis_id = a.analysis_id
LEFT JOIN messages m ON s.session_id = m.session_id
WHERE s.session_id = ?
GROUP BY 1, 2, 3, 4, 5, 6, 7, 8;
*/

-- ---------------------------------------------------------------------------
-- Grants (adjust for your role structure)
-- ---------------------------------------------------------------------------

-- Application service account
-- GRANT USAGE ON DATABASE SWIMCOACH TO ROLE swimcoach_app;
-- GRANT USAGE ON SCHEMA SWIMCOACH.COACHING TO ROLE swimcoach_app;
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA SWIMCOACH.COACHING TO ROLE swimcoach_app;
-- GRANT USAGE ON ALL PROCEDURES IN SCHEMA SWIMCOACH.COACHING TO ROLE swimcoach_app;
