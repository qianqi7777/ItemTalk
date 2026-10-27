ALTER TABLE identification_attempts ADD COLUMN session_id uuid;
ALTER TABLE identification_attempts ADD COLUMN round_no integer NOT NULL DEFAULT 1 CHECK(round_no BETWEEN 1 AND 4);
ALTER TABLE identification_attempts ADD COLUMN analysis jsonb NOT NULL DEFAULT '{}'::jsonb
 CHECK(jsonb_typeof(analysis)='object' AND pg_column_size(analysis)<=1000000);
ALTER TABLE identification_attempts ADD CONSTRAINT identification_session_owner_fk
 FOREIGN KEY(session_id,user_id) REFERENCES identification_attempts(id,user_id);
CREATE UNIQUE INDEX ix_identification_single_successor ON identification_attempts(supersedes_attempt_id)
 WHERE session_id IS NOT NULL AND supersedes_attempt_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX ix_identification_session ON identification_attempts(user_id,session_id,round_no);
