ALTER TABLE model_configs DROP CONSTRAINT model_configs_role_protocol_check;
ALTER TABLE model_configs ADD CONSTRAINT model_configs_role_protocol_check CHECK(
 (role='chat' AND protocol IN ('openai_chat','dashscope_chat','openai_responses','dashscope_responses')) OR
 (role='vision' AND protocol IN ('openai_chat','dashscope_chat','openai_responses','dashscope_responses','dashscope_image_search')) OR
 (role='stt' AND protocol IN ('dashscope_audio','openai_transcription')) OR
 (role='embedding' AND protocol IN ('openai_embeddings','dashscope_embeddings'))
);
