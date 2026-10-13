CREATE TABLE guidance_sessions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users,
 user_product_instance_id uuid NOT NULL, topic varchar(200) NOT NULL,
 status varchar(30) NOT NULL CHECK(status IN ('active','paused','completed','failed','blocked_unsafe')),
 risk_level varchar(20) NOT NULL CHECK(risk_level IN ('low','medium','high')),
 current_step_no integer CHECK(current_step_no >= 1), version integer NOT NULL DEFAULT 1 CHECK(version >= 1),
 resume_context jsonb NOT NULL DEFAULT '{}', deleted_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,user_id), FOREIGN KEY(user_product_instance_id,user_id) REFERENCES user_product_instances(id,user_id),
 CHECK(status <> 'blocked_unsafe' OR current_step_no IS NULL)
);
CREATE INDEX ix_guidance_owner ON guidance_sessions(user_id,user_product_instance_id,updated_at DESC) WHERE deleted_at IS NULL;
CREATE TABLE guidance_steps (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), session_id uuid NOT NULL REFERENCES guidance_sessions ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES users, step_no integer NOT NULL CHECK(step_no > 0),
 instruction text NOT NULL CHECK(length(instruction) BETWEEN 1 AND 2000), success_criteria text NOT NULL,
 risk_level varchar(20) NOT NULL CHECK(risk_level IN ('low','medium')),
 preconditions jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(preconditions)='array'),
 safety_notes jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(safety_notes)='array'), failure_of_step_id uuid,
 status varchar(20) NOT NULL CHECK(status IN ('pending','active','completed','failed','skipped')),
 feedback jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(session_id,step_no), UNIQUE(id,user_id), UNIQUE(id,session_id,user_id),
 FOREIGN KEY(session_id,user_id) REFERENCES guidance_sessions(id,user_id),
 FOREIGN KEY(failure_of_step_id,session_id,user_id) REFERENCES guidance_steps(id,session_id,user_id),
 CHECK(failure_of_step_id IS NULL OR failure_of_step_id <> id)
);
-- 当前动作唯一，推进先完成旧步骤再激活下一步骤，避免并发反馈生成两个当前动作。
CREATE UNIQUE INDEX uq_guidance_active_step ON guidance_steps(session_id) WHERE status='active';
CREATE TABLE guidance_step_citations (
 guidance_step_id uuid NOT NULL REFERENCES guidance_steps ON DELETE CASCADE,
 knowledge_chunk_id uuid NOT NULL REFERENCES knowledge_chunks, rank smallint NOT NULL CHECK(rank > 0),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(guidance_step_id,knowledge_chunk_id),
 UNIQUE(guidance_step_id,rank)
);
CREATE TABLE guidance_step_attachments (
 guidance_step_id uuid NOT NULL, asset_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES users,
 sort_order smallint NOT NULL CHECK(sort_order >= 0), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(guidance_step_id,asset_id), UNIQUE(guidance_step_id,sort_order),
 FOREIGN KEY(guidance_step_id,user_id) REFERENCES guidance_steps(id,user_id),
 FOREIGN KEY(asset_id,user_id) REFERENCES assets(id,owner_user_id)
);
CREATE TRIGGER touch_guidance_session BEFORE UPDATE ON guidance_sessions FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER touch_guidance_step BEFORE UPDATE ON guidance_steps FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
