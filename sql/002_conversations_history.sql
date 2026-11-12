-- Migración 002: Historial de conversaciones en base de agente
-- Agrega columnas para título, fecha de actualización y borrado suave.

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS title text;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

-- Backfill updated_at con base en el último mensaje o created_at
UPDATE conversations c
SET updated_at = COALESCE(
    (SELECT MAX(m.created_at) FROM messages m WHERE m.conversation_id = c.id),
    c.created_at,
    now()
)
WHERE c.updated_at IS NULL;

-- Backfill title con base en el primer mensaje de usuario (primeras 6-8 palabras)
UPDATE conversations c
SET title = sub.auto_title
FROM (
    SELECT DISTINCT ON (conversation_id)
        conversation_id,
        substring(regexp_replace(content, '\s+', ' ', 'g') from '^(\S+(\s+\S+){0,7})') AS auto_title
    FROM messages
    WHERE role = 'user'
    ORDER BY conversation_id, id ASC
) sub
WHERE c.id = sub.conversation_id AND (c.title IS NULL OR c.title = '');

-- Default para conversaciones sin título
UPDATE conversations
SET title = 'Nueva conversación'
WHERE title IS NULL OR title = '';

-- Índice para búsqueda y ordenamiento por fecha de actualización
CREATE INDEX IF NOT EXISTS idx_conversations_history 
ON conversations (deleted_at, updated_at DESC);

-- Trigger para mantener actualizado updated_at automáticamente ante nuevos mensajes
CREATE OR REPLACE FUNCTION trg_update_conversation_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE conversations
    SET updated_at = NEW.created_at
    WHERE id = NEW.conversation_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_messages_update_conversation ON messages;
CREATE TRIGGER trg_messages_update_conversation
AFTER INSERT ON messages
FOR EACH ROW
EXECUTE FUNCTION trg_update_conversation_timestamp();
