CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS documents (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), source text NOT NULL,
 slug text NOT NULL UNIQUE, title text NOT NULL, url text,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chunks (
 id bigserial PRIMARY KEY, document_id uuid NOT NULL REFERENCES documents ON DELETE CASCADE,
 ord integer NOT NULL, content text NOT NULL, embedding vector(768) NOT NULL,
 UNIQUE(document_id, ord)
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE TABLE IF NOT EXISTS conversations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS messages (
 id bigserial PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE,
 role text NOT NULL CHECK(role IN ('user','assistant','tool')), content text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tool_calls (
 id bigserial PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES conversations ON DELETE CASCADE,
 message_id bigint REFERENCES messages ON DELETE CASCADE,
 name text NOT NULL, args jsonb NOT NULL, ok boolean NOT NULL, ms integer NOT NULL,
 result_summary text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pending_actions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), conversation_id uuid NOT NULL REFERENCES conversations,
 kind text NOT NULL CHECK(kind IN ('confirmar','cancelar')), order_ref text NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','confirmed_simulated','cancelled')),
 created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz
);
CREATE TABLE IF NOT EXISTS eval_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), created_at timestamptz NOT NULL DEFAULT now(),
 total integer NOT NULL, passed integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS eval_results (
 id bigserial PRIMARY KEY, run_id uuid NOT NULL REFERENCES eval_runs ON DELETE CASCADE,
 case_name text NOT NULL, expected_tool text, used_tools jsonb NOT NULL,
 correct boolean NOT NULL, ms integer NOT NULL, answer text NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,id);
CREATE INDEX IF NOT EXISTS tool_calls_conversation ON tool_calls(conversation_id,id);
