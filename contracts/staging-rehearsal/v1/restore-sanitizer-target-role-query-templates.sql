-- CosmaNeura staging sanitizer target-role observer query templates v1
-- Contract data only. Execute one named statement at a time in exact order.
-- frame: session_and_role
SELECT pg_catalog.current_database()::text AS database_name,
       session_user::text AS session_user_name,
       current_user::text AS current_user_name,
       r.oid::bigint AS role_oid,
       r.rolname::text AS role_name,
       r.rolcanlogin::boolean AS can_login,
       r.rolsuper::boolean AS is_superuser,
       r.rolinherit::boolean AS inherits_privileges,
       r.rolcreaterole::boolean AS can_create_role,
       r.rolcreatedb::boolean AS can_create_database,
       r.rolreplication::boolean AS can_replicate,
       r.rolbypassrls::boolean AS bypasses_row_security,
       r.rolconnlimit::integer AS connection_limit,
       pg_catalog.current_setting('session_replication_role')::text AS session_replication_role
FROM pg_catalog.pg_roles AS r
WHERE r.oid = pg_catalog.to_regrole(current_user)
ORDER BY r.oid;

-- frame: memberships
SELECT 'outbound'::text AS direction,
       granted.oid::bigint AS granted_role_oid,
       granted.rolname::text AS granted_role_name,
       member.oid::bigint AS member_role_oid,
       member.rolname::text AS member_role_name,
       membership.admin_option::boolean,
       membership.inherit_option::boolean,
       membership.set_option::boolean
FROM pg_catalog.pg_auth_members AS membership
JOIN pg_catalog.pg_roles AS granted ON granted.oid = membership.roleid
JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
WHERE membership.member = pg_catalog.to_regrole(current_user)
UNION ALL
SELECT 'inbound'::text AS direction,
       granted.oid::bigint AS granted_role_oid,
       granted.rolname::text AS granted_role_name,
       member.oid::bigint AS member_role_oid,
       member.rolname::text AS member_role_name,
       membership.admin_option::boolean,
       membership.inherit_option::boolean,
       membership.set_option::boolean
FROM pg_catalog.pg_auth_members AS membership
JOIN pg_catalog.pg_roles AS granted ON granted.oid = membership.roleid
JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member
WHERE membership.roleid = pg_catalog.to_regrole(current_user)
ORDER BY direction, granted_role_name, member_role_name, granted_role_oid, member_role_oid;

-- frame: role_settings
SELECT settings.setdatabase::bigint AS database_oid,
       database_role.datname::text AS database_name,
       settings.setrole::bigint AS role_oid,
       CASE WHEN settings.setrole = 0 THEN 'PUBLIC'::text ELSE configured_role.rolname::text END AS role_name,
       CASE
         WHEN settings.setdatabase = 0 AND settings.setrole = pg_catalog.to_regrole(current_user) THEN 'role_global'::text
         WHEN settings.setdatabase = target_database.oid AND settings.setrole = 0 THEN 'database_all_roles'::text
         WHEN settings.setdatabase = target_database.oid AND settings.setrole = pg_catalog.to_regrole(current_user) THEN 'role_and_database'::text
         ELSE 'unexpected'::text
       END AS source_scope
FROM pg_catalog.pg_db_role_setting AS settings
CROSS JOIN pg_catalog.pg_database AS target_database
LEFT JOIN pg_catalog.pg_database AS database_role ON database_role.oid = settings.setdatabase
LEFT JOIN pg_catalog.pg_roles AS configured_role ON configured_role.oid = settings.setrole
WHERE target_database.datname = pg_catalog.current_database()
  AND settings.setdatabase IN (0, target_database.oid)
  AND settings.setrole IN (0, pg_catalog.to_regrole(current_user))
ORDER BY settings.setdatabase, settings.setrole;

-- frame: schemas
SELECT namespace.oid::bigint AS schema_oid,
       namespace.nspname::text AS schema_name,
       owner_role.oid::bigint AS owner_role_oid,
       owner_role.rolname::text AS owner_role_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantor_role.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'::text ELSE grantee_role.rolname::text END AS grantee_role_name,
       acl.privilege_type::text,
       acl.is_grantable::boolean,
       pg_catalog.has_schema_privilege(current_user, namespace.oid, 'USAGE')::boolean AS current_user_has_usage,
       pg_catalog.has_schema_privilege(current_user, namespace.oid, 'CREATE')::boolean AS current_user_has_create
FROM pg_catalog.pg_namespace AS namespace
JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = namespace.nspowner
LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(namespace.nspacl, pg_catalog.acldefault('n', namespace.nspowner))) AS acl ON TRUE
LEFT JOIN pg_catalog.pg_roles AS grantor_role ON grantor_role.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
WHERE namespace.nspname = 'public'
ORDER BY namespace.nspname, namespace.oid, acl.grantee, acl.privilege_type, acl.grantor;

-- frame: relations
SELECT namespace.nspname::text AS schema_name,
       relation.oid::bigint AS relation_oid,
       relation.relname::text AS relation_name,
       relation.relkind::text AS relation_kind,
       owner_role.oid::bigint AS owner_role_oid,
       owner_role.rolname::text AS owner_role_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantor_role.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'::text ELSE grantee_role.rolname::text END AS grantee_role_name,
       acl.privilege_type::text,
       acl.is_grantable::boolean,
       pg_catalog.has_table_privilege(current_user, relation.oid, 'SELECT')::boolean AS current_user_has_select,
       pg_catalog.has_table_privilege(current_user, relation.oid, 'INSERT')::boolean AS current_user_has_insert,
       pg_catalog.has_table_privilege(current_user, relation.oid, 'UPDATE')::boolean AS current_user_has_update,
       pg_catalog.has_table_privilege(current_user, relation.oid, 'DELETE')::boolean AS current_user_has_delete,
       pg_catalog.has_table_privilege(current_user, relation.oid, 'TRUNCATE')::boolean AS current_user_has_truncate,
       pg_catalog.has_table_privilege(current_user, relation.oid, 'REFERENCES')::boolean AS current_user_has_references,
       pg_catalog.has_table_privilege(current_user, relation.oid, 'TRIGGER')::boolean AS current_user_has_trigger
FROM pg_catalog.pg_class AS relation
JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = relation.relowner
LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(relation.relacl, pg_catalog.acldefault('r', relation.relowner))) AS acl ON TRUE
LEFT JOIN pg_catalog.pg_roles AS grantor_role ON grantor_role.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
WHERE namespace.nspname = 'public'
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY namespace.nspname, relation.relname, relation.oid, acl.grantee, acl.privilege_type, acl.grantor;

-- frame: columns
SELECT namespace.nspname::text AS schema_name,
       relation.oid::bigint AS relation_oid,
       relation.relname::text AS relation_name,
       attribute.attnum::smallint AS column_number,
       attribute.attname::text AS column_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantor_role.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'::text ELSE grantee_role.rolname::text END AS grantee_role_name,
       acl.privilege_type::text,
       acl.is_grantable::boolean,
       pg_catalog.has_column_privilege(current_user, relation.oid, attribute.attnum, 'SELECT')::boolean AS current_user_has_select,
       pg_catalog.has_column_privilege(current_user, relation.oid, attribute.attnum, 'INSERT')::boolean AS current_user_has_insert,
       pg_catalog.has_column_privilege(current_user, relation.oid, attribute.attnum, 'UPDATE')::boolean AS current_user_has_update,
       pg_catalog.has_column_privilege(current_user, relation.oid, attribute.attnum, 'REFERENCES')::boolean AS current_user_has_references
FROM pg_catalog.pg_attribute AS attribute
JOIN pg_catalog.pg_class AS relation ON relation.oid = attribute.attrelid
JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
LEFT JOIN LATERAL pg_catalog.aclexplode(attribute.attacl) AS acl ON TRUE
LEFT JOIN pg_catalog.pg_roles AS grantor_role ON grantor_role.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
WHERE namespace.nspname = 'public'
  AND relation.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND attribute.attnum > 0
  AND NOT attribute.attisdropped
  AND attribute.attacl IS NOT NULL
ORDER BY namespace.nspname, relation.relname, relation.oid, attribute.attnum, acl.grantee, acl.privilege_type, acl.grantor;

-- frame: sequences
SELECT namespace.nspname::text AS schema_name,
       sequence_relation.oid::bigint AS sequence_oid,
       sequence_relation.relname::text AS sequence_name,
       owner_role.oid::bigint AS owner_role_oid,
       owner_role.rolname::text AS owner_role_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantor_role.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'::text ELSE grantee_role.rolname::text END AS grantee_role_name,
       acl.privilege_type::text,
       acl.is_grantable::boolean,
       pg_catalog.has_sequence_privilege(current_user, sequence_relation.oid, 'USAGE')::boolean AS current_user_has_usage,
       pg_catalog.has_sequence_privilege(current_user, sequence_relation.oid, 'SELECT')::boolean AS current_user_has_select,
       pg_catalog.has_sequence_privilege(current_user, sequence_relation.oid, 'UPDATE')::boolean AS current_user_has_update
FROM pg_catalog.pg_class AS sequence_relation
JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = sequence_relation.relnamespace
JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = sequence_relation.relowner
LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(sequence_relation.relacl, pg_catalog.acldefault('s', sequence_relation.relowner))) AS acl ON TRUE
LEFT JOIN pg_catalog.pg_roles AS grantor_role ON grantor_role.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
WHERE namespace.nspname = 'public'
  AND sequence_relation.relkind = 'S'
ORDER BY namespace.nspname, sequence_relation.relname, sequence_relation.oid, acl.grantee, acl.privilege_type, acl.grantor;

-- frame: routines
SELECT namespace.nspname::text AS schema_name,
       routine.oid::bigint AS routine_oid,
       routine.proname::text AS routine_name,
       pg_catalog.pg_get_function_identity_arguments(routine.oid)::text AS identity_arguments,
       routine.prokind::text AS routine_kind,
       owner_role.oid::bigint AS owner_role_oid,
       owner_role.rolname::text AS owner_role_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantor_role.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'::text ELSE grantee_role.rolname::text END AS grantee_role_name,
       acl.privilege_type::text,
       acl.is_grantable::boolean,
       pg_catalog.has_function_privilege(current_user, routine.oid, 'EXECUTE')::boolean AS current_user_has_execute
FROM pg_catalog.pg_proc AS routine
JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = routine.pronamespace
JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = routine.proowner
LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(routine.proacl, pg_catalog.acldefault('f', routine.proowner))) AS acl ON TRUE
LEFT JOIN pg_catalog.pg_roles AS grantor_role ON grantor_role.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
WHERE namespace.nspname = 'public'
ORDER BY namespace.nspname, routine.proname, identity_arguments, routine.oid, acl.grantee, acl.privilege_type, acl.grantor;

-- frame: types
SELECT namespace.nspname::text AS schema_name,
       observed_type.oid::bigint AS type_oid,
       observed_type.typname::text AS type_name,
       observed_type.typtype::text AS type_kind,
       observed_type.typcategory::text AS type_category,
       element_namespace.nspname::text AS element_schema_name,
       element_type.typname::text AS element_type_name,
       relation.oid::bigint AS relation_oid,
       relation.relname::text AS relation_name,
       owner_role.oid::bigint AS owner_role_oid,
       owner_role.rolname::text AS owner_role_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantor_role.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'::text ELSE grantee_role.rolname::text END AS grantee_role_name,
       acl.privilege_type::text,
       acl.is_grantable::boolean,
       pg_catalog.has_type_privilege(current_user, observed_type.oid, 'USAGE')::boolean AS current_user_has_usage
FROM pg_catalog.pg_type AS observed_type
JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = observed_type.typnamespace
JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = observed_type.typowner
LEFT JOIN pg_catalog.pg_type AS element_type ON element_type.oid = observed_type.typelem
LEFT JOIN pg_catalog.pg_namespace AS element_namespace ON element_namespace.oid = element_type.typnamespace
LEFT JOIN pg_catalog.pg_class AS relation ON relation.oid = observed_type.typrelid
LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(observed_type.typacl, pg_catalog.acldefault('T', observed_type.typowner))) AS acl ON TRUE
LEFT JOIN pg_catalog.pg_roles AS grantor_role ON grantor_role.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
WHERE namespace.nspname = 'public'
ORDER BY namespace.nspname, observed_type.typname, observed_type.oid, acl.grantee, acl.privilege_type, acl.grantor;

-- frame: languages
SELECT language.oid::bigint AS language_oid,
       language.lanname::text AS language_name,
       language.lanispl::boolean AS is_procedural,
       language.lanpltrusted::boolean AS is_trusted,
       owner_role.oid::bigint AS owner_role_oid,
       owner_role.rolname::text AS owner_role_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantor_role.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'::text ELSE grantee_role.rolname::text END AS grantee_role_name,
       acl.privilege_type::text,
       acl.is_grantable::boolean,
       pg_catalog.has_language_privilege(current_user, language.oid, 'USAGE')::boolean AS current_user_has_usage
FROM pg_catalog.pg_language AS language
JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = language.lanowner
LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(language.lanacl, pg_catalog.acldefault('l', language.lanowner))) AS acl ON TRUE
LEFT JOIN pg_catalog.pg_roles AS grantor_role ON grantor_role.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
WHERE language.lanname NOT IN ('internal', 'c', 'sql')
ORDER BY language.lanname, language.oid, acl.grantee, acl.privilege_type, acl.grantor;

-- frame: foreign_data_wrappers
SELECT wrapper.oid::bigint AS wrapper_oid,
       wrapper.fdwname::text AS wrapper_name,
       owner_role.oid::bigint AS owner_role_oid,
       owner_role.rolname::text AS owner_role_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantor_role.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'::text ELSE grantee_role.rolname::text END AS grantee_role_name,
       acl.privilege_type::text,
       acl.is_grantable::boolean,
       pg_catalog.has_foreign_data_wrapper_privilege(current_user, wrapper.oid, 'USAGE')::boolean AS current_user_has_usage
FROM pg_catalog.pg_foreign_data_wrapper AS wrapper
JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = wrapper.fdwowner
LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(wrapper.fdwacl, pg_catalog.acldefault('F', wrapper.fdwowner))) AS acl ON TRUE
LEFT JOIN pg_catalog.pg_roles AS grantor_role ON grantor_role.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
ORDER BY wrapper.fdwname, wrapper.oid, acl.grantee, acl.privilege_type, acl.grantor;

-- frame: foreign_servers
SELECT server.oid::bigint AS server_oid,
       server.srvname::text AS server_name,
       wrapper.fdwname::text AS wrapper_name,
       owner_role.oid::bigint AS owner_role_oid,
       owner_role.rolname::text AS owner_role_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantor_role.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       CASE WHEN acl.grantee = 0 THEN 'PUBLIC'::text ELSE grantee_role.rolname::text END AS grantee_role_name,
       acl.privilege_type::text,
       acl.is_grantable::boolean,
       pg_catalog.has_server_privilege(current_user, server.oid, 'USAGE')::boolean AS current_user_has_usage
FROM pg_catalog.pg_foreign_server AS server
JOIN pg_catalog.pg_foreign_data_wrapper AS wrapper ON wrapper.oid = server.srvfdw
JOIN pg_catalog.pg_roles AS owner_role ON owner_role.oid = server.srvowner
LEFT JOIN LATERAL pg_catalog.aclexplode(COALESCE(server.srvacl, pg_catalog.acldefault('S', server.srvowner))) AS acl ON TRUE
LEFT JOIN pg_catalog.pg_roles AS grantor_role ON grantor_role.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
ORDER BY server.srvname, server.oid, acl.grantee, acl.privilege_type, acl.grantor;

-- frame: user_mappings
SELECT mapping.umid::bigint AS mapping_oid,
       mapping.srvid::bigint AS server_oid,
       mapping.srvname::text AS server_name,
       mapping.umuser::bigint AS mapped_role_oid,
       CASE WHEN mapping.umuser = 0 THEN 'PUBLIC'::text ELSE mapping.usename::text END AS mapped_role_name
FROM pg_catalog.pg_user_mappings AS mapping
WHERE mapping.umuser IN (0, pg_catalog.to_regrole(current_user))
ORDER BY mapping.srvname, mapping.srvid, mapping.umuser, mapping.umid;
