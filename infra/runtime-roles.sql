-- 仅数据库管理员执行；可重复运行。运行角色不拥有表，也不能切换到守卫角色。
DO $$ DECLARE role_name text; BEGIN
 FOREACH role_name IN ARRAY ARRAY['itemtalk_app','itemtalk_worker','itemtalk_migrator','itemtalk_guard'] LOOP
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
   EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS', role_name);
  END IF;
 END LOOP;
END $$;
ALTER ROLE itemtalk_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE itemtalk_worker NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
ALTER ROLE itemtalk_guard NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
REVOKE itemtalk_guard FROM itemtalk_app,itemtalk_worker;
GRANT itemtalk_guard TO itemtalk_migrator;
GRANT USAGE,CREATE ON SCHEMA public TO itemtalk_migrator,itemtalk_guard;
GRANT USAGE ON SCHEMA public TO itemtalk_app,itemtalk_worker;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE itemtalk_migrator IN SCHEMA public
 GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO itemtalk_app,itemtalk_worker;
ALTER DEFAULT PRIVILEGES FOR ROLE itemtalk_migrator IN SCHEMA public
 GRANT USAGE,SELECT ON SEQUENCES TO itemtalk_app,itemtalk_worker;
