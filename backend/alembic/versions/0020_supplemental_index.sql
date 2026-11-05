CREATE TABLE supplemental_documents (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 document_version_id uuid NOT NULL REFERENCES document_versions,
 product_model_id uuid NOT NULL REFERENCES product_models,
 publisher_url text NOT NULL CHECK(length(publisher_url) BETWEEN 1 AND 2048),
 parent_document_version_id uuid REFERENCES document_versions,
 parent_link_url text,
 status varchar(20) NOT NULL CHECK(status IN ('processing','ready','rejected','revoked')),
 page_count integer NOT NULL DEFAULT 0 CHECK(page_count BETWEEN 0 AND 400),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(document_version_id,product_model_id),
 CHECK((parent_document_version_id IS NULL)=(parent_link_url IS NULL)),
 CHECK(parent_document_version_id IS NULL OR parent_document_version_id<>document_version_id)
);
CREATE TABLE supplemental_chunks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 supplemental_document_id uuid NOT NULL REFERENCES supplemental_documents,
 chunk_no integer NOT NULL CHECK(chunk_no>0),
 page_start integer NOT NULL CHECK(page_start>0),
 page_end integer NOT NULL CHECK(page_end>=page_start),
 content text NOT NULL,
 embedding vector(1024) NOT NULL,
 embedding_model varchar(120) NOT NULL, embedding_revision varchar(40) NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(supplemental_document_id,chunk_no)
);
CREATE INDEX ix_supplemental_model ON supplemental_documents(product_model_id,status);
CREATE INDEX ix_supplemental_vectors ON supplemental_chunks USING hnsw(embedding vector_cosine_ops);
CREATE INDEX ix_supplemental_text ON supplemental_chunks USING gin(content gin_trgm_ops);
REVOKE INSERT,UPDATE,DELETE ON supplemental_documents,supplemental_chunks FROM itemtalk_app;
GRANT SELECT ON supplemental_documents,supplemental_chunks TO itemtalk_app;
-- FOR SHARE requires UPDATE on at least one column. These immutable foreign
-- keys permit row locks but the trigger below refuses any actual change.
GRANT UPDATE(document_version_id) ON supplemental_documents TO itemtalk_app;
GRANT UPDATE(supplemental_document_id) ON supplemental_chunks TO itemtalk_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON supplemental_documents,supplemental_chunks TO itemtalk_worker;
CREATE FUNCTION itemtalk_supplement_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_TABLE_NAME='supplemental_documents' THEN
  IF OLD.document_version_id IS DISTINCT FROM NEW.document_version_id OR OLD.product_model_id IS DISTINCT FROM NEW.product_model_id OR OLD.publisher_url IS DISTINCT FROM NEW.publisher_url OR OLD.parent_document_version_id IS DISTINCT FROM NEW.parent_document_version_id OR OLD.parent_link_url IS DISTINCT FROM NEW.parent_link_url THEN
   RAISE EXCEPTION 'Supplemental provenance is immutable';
  END IF;
 ELSIF OLD.supplemental_document_id IS DISTINCT FROM NEW.supplemental_document_id OR OLD.chunk_no IS DISTINCT FROM NEW.chunk_no OR OLD.content IS DISTINCT FROM NEW.content OR OLD.page_start IS DISTINCT FROM NEW.page_start OR OLD.page_end IS DISTINCT FROM NEW.page_end THEN
  RAISE EXCEPTION 'Supplemental content is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER supplemental_document_immutable BEFORE UPDATE ON supplemental_documents FOR EACH ROW EXECUTE FUNCTION itemtalk_supplement_immutable();
CREATE TRIGGER supplemental_chunk_immutable BEFORE UPDATE ON supplemental_chunks FOR EACH ROW EXECUTE FUNCTION itemtalk_supplement_immutable();
REVOKE ALL ON FUNCTION itemtalk_supplement_immutable() FROM PUBLIC;
