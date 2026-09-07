-- CosmaNeura staging sanitizer privileged/global observer query templates v1
-- Contract data only. Execute server-once frames first and database frames once per authenticated database.
-- frame: entra_principal_mappings
SELECT labels.objoid::bigint AS role_oid,
       roles.rolname::text AS role_name,
       labels.provider::text AS label_provider,
       labels.label::text AS label
FROM pg_catalog.pg_shseclabel AS labels
JOIN pg_catalog.pg_roles AS roles ON roles.oid = labels.objoid
WHERE labels.classoid = 'pg_catalog.pg_authid'::pg_catalog.regclass
  AND labels.provider = 'pgaadauth'
ORDER BY labels.objoid, labels.provider, labels.label;
-- frame: password_null_state
SELECT roles.oid::bigint AS role_oid,
       roles.rolname::text AS role_name,
       (roles.rolpassword IS NULL)::boolean AS password_is_null
FROM pg_catalog.pg_authid AS roles
ORDER BY roles.oid;
-- frame: role_shared_dependencies
SELECT dependencies.dbid::bigint AS database_oid,
       databases.datname::text AS database_name,
       dependencies.classid::bigint AS dependent_class_oid,
       dependencies.objid::bigint AS dependent_object_oid,
       dependencies.objsubid::integer AS dependent_object_sub_id,
       dependencies.refobjid::bigint AS referenced_role_oid,
       roles.rolname::text AS referenced_role_name,
       dependencies.deptype::text AS dependency_type
FROM pg_catalog.pg_shdepend AS dependencies
JOIN pg_catalog.pg_roles AS roles ON roles.oid = dependencies.refobjid
LEFT JOIN pg_catalog.pg_database AS databases ON databases.oid = dependencies.dbid
WHERE dependencies.refclassid = 'pg_catalog.pg_authid'::pg_catalog.regclass
ORDER BY dependencies.refobjid,
         dependencies.dbid,
         dependencies.classid,
         dependencies.objid,
         dependencies.objsubid,
         dependencies.deptype;
-- frame: database_inventory
SELECT databases.oid::bigint AS database_oid,
       databases.datname::text AS database_name,
       databases.datdba::bigint AS owner_role_oid,
       owners.rolname::text AS owner_role_name,
       databases.datistemplate::boolean AS is_template,
       databases.datallowconn::boolean AS allows_connections,
       databases.datconnlimit::integer AS connection_limit,
       databases.dattablespace::bigint AS tablespace_oid
FROM pg_catalog.pg_database AS databases
JOIN pg_catalog.pg_roles AS owners ON owners.oid = databases.datdba
ORDER BY databases.oid;
-- frame: database_acl_sources
SELECT databases.oid::bigint AS database_oid,
       databases.datname::text AS database_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantors.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       grantees.rolname::text AS grantee_role_name,
       acl.privilege_type::text AS privilege_type,
       acl.is_grantable::boolean AS is_grantable
FROM pg_catalog.pg_database AS databases
CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(
        databases.datacl,
        pg_catalog.acldefault('d', databases.datdba)
    )
) AS acl
LEFT JOIN pg_catalog.pg_roles AS grantors ON grantors.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantees ON grantees.oid = acl.grantee
ORDER BY databases.oid,
         acl.grantee,
         acl.privilege_type,
         acl.grantor,
         acl.is_grantable;
-- frame: database_effective_privileges
SELECT roles.oid::bigint AS role_oid,
       roles.rolname::text AS role_name,
       databases.oid::bigint AS database_oid,
       databases.datname::text AS database_name,
       (databases.datdba = roles.oid)::boolean AS role_is_owner,
       pg_catalog.has_database_privilege(
           roles.oid,
           databases.oid,
           'CONNECT'
       )::boolean AS effective_connect,
       pg_catalog.has_database_privilege(
           roles.oid,
           databases.oid,
           'CREATE'
       )::boolean AS effective_create,
       pg_catalog.has_database_privilege(
           roles.oid,
           databases.oid,
           'TEMPORARY'
       )::boolean AS effective_temporary
FROM pg_catalog.pg_roles AS roles
CROSS JOIN pg_catalog.pg_database AS databases
ORDER BY roles.oid, databases.oid;
-- frame: runtime_role_sessions
SELECT activity.datid::bigint AS database_oid,
       activity.datname::text AS database_name,
       activity.pid::integer AS process_id,
       activity.leader_pid::integer AS leader_process_id,
       activity.usesysid::bigint AS role_oid,
       activity.usename::text AS role_name,
       activity.backend_type::text AS backend_type,
       activity.state::text AS state
FROM pg_catalog.pg_stat_activity AS activity
ORDER BY activity.usesysid NULLS FIRST,
         activity.datid NULLS FIRST,
         activity.pid;
-- frame: tablespace_acl_sources
SELECT tablespaces.oid::bigint AS tablespace_oid,
       tablespaces.spcname::text AS tablespace_name,
       tablespaces.spcowner::bigint AS owner_role_oid,
       owners.rolname::text AS owner_role_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantors.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       grantees.rolname::text AS grantee_role_name,
       acl.privilege_type::text AS privilege_type,
       acl.is_grantable::boolean AS is_grantable
FROM pg_catalog.pg_tablespace AS tablespaces
JOIN pg_catalog.pg_roles AS owners ON owners.oid = tablespaces.spcowner
CROSS JOIN LATERAL pg_catalog.aclexplode(
    COALESCE(
        tablespaces.spcacl,
        pg_catalog.acldefault('t', tablespaces.spcowner)
    )
) AS acl
LEFT JOIN pg_catalog.pg_roles AS grantors ON grantors.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantees ON grantees.oid = acl.grantee
ORDER BY tablespaces.oid,
         acl.grantee,
         acl.privilege_type,
         acl.grantor,
         acl.is_grantable;
-- frame: tablespace_effective_privileges
SELECT roles.oid::bigint AS role_oid,
       roles.rolname::text AS role_name,
       tablespaces.oid::bigint AS tablespace_oid,
       tablespaces.spcname::text AS tablespace_name,
       (tablespaces.spcowner = roles.oid)::boolean AS role_is_owner,
       pg_catalog.has_tablespace_privilege(
           roles.oid,
           tablespaces.oid,
           'CREATE'
       )::boolean AS effective_create
FROM pg_catalog.pg_roles AS roles
CROSS JOIN pg_catalog.pg_tablespace AS tablespaces
ORDER BY roles.oid, tablespaces.oid;
-- frame: parameter_acl_sources
SELECT parameters.oid::bigint AS parameter_acl_oid,
       parameters.parname::text AS parameter_name,
       acl.grantor::bigint AS grantor_role_oid,
       grantors.rolname::text AS grantor_role_name,
       acl.grantee::bigint AS grantee_role_oid,
       grantees.rolname::text AS grantee_role_name,
       acl.privilege_type::text AS privilege_type,
       acl.is_grantable::boolean AS is_grantable
FROM pg_catalog.pg_parameter_acl AS parameters
CROSS JOIN LATERAL pg_catalog.aclexplode(parameters.paracl) AS acl
LEFT JOIN pg_catalog.pg_roles AS grantors ON grantors.oid = acl.grantor
LEFT JOIN pg_catalog.pg_roles AS grantees ON grantees.oid = acl.grantee
ORDER BY parameters.oid,
         acl.grantee,
         acl.privilege_type,
         acl.grantor,
         acl.is_grantable;
-- frame: parameter_effective_privileges
SELECT roles.oid::bigint AS role_oid,
       roles.rolname::text AS role_name,
       parameters.oid::bigint AS parameter_acl_oid,
       parameters.parname::text AS parameter_name,
       pg_catalog.has_parameter_privilege(
           roles.oid,
           parameters.parname,
           'SET'
       )::boolean AS effective_set,
       pg_catalog.has_parameter_privilege(
           roles.oid,
           parameters.parname,
           'ALTER SYSTEM'
       )::boolean AS effective_alter_system
FROM pg_catalog.pg_roles AS roles
CROSS JOIN pg_catalog.pg_parameter_acl AS parameters
ORDER BY roles.oid, parameters.oid;
-- frame: database_object_authority
SELECT authority.object_class,
       authority.object_oid,
       authority.object_name,
       authority.owner_role_oid,
       owners.rolname::text AS owner_role_name
FROM (
    SELECT 'publication'::text AS object_class,
           publications.oid::bigint AS object_oid,
           publications.pubname::text AS object_name,
           publications.pubowner::bigint AS owner_role_oid
    FROM pg_catalog.pg_publication AS publications
    UNION ALL
    SELECT 'subscription'::text AS object_class,
           subscriptions.oid::bigint AS object_oid,
           subscriptions.subname::text AS object_name,
           subscriptions.subowner::bigint AS owner_role_oid
    FROM pg_catalog.pg_subscription AS subscriptions
    UNION ALL
    SELECT 'extension'::text AS object_class,
           extensions.oid::bigint AS object_oid,
           extensions.extname::text AS object_name,
           extensions.extowner::bigint AS owner_role_oid
    FROM pg_catalog.pg_extension AS extensions
    UNION ALL
    SELECT 'event_trigger'::text AS object_class,
           triggers.oid::bigint AS object_oid,
           triggers.evtname::text AS object_name,
           triggers.evtowner::bigint AS owner_role_oid
    FROM pg_catalog.pg_event_trigger AS triggers
) AS authority
JOIN pg_catalog.pg_roles AS owners ON owners.oid = authority.owner_role_oid
ORDER BY authority.object_class, authority.object_oid;
-- frame: system_object_acl_deltas
WITH acl_objects AS (
    SELECT 'schema'::text AS object_class,
           namespaces.oid::bigint AS object_oid,
           0::integer AS object_sub_id,
           namespaces.nspname::text AS object_identity,
           namespaces.nspowner::bigint AS owner_role_oid,
           COALESCE(
               namespaces.nspacl,
               pg_catalog.acldefault('n', namespaces.nspowner)
           ) AS current_acl,
           COALESCE(
               initial.initprivs,
               pg_catalog.acldefault('n', namespaces.nspowner)
           ) AS baseline_acl
    FROM pg_catalog.pg_namespace AS namespaces
    LEFT JOIN pg_catalog.pg_init_privs AS initial
      ON initial.classoid = 'pg_catalog.pg_namespace'::pg_catalog.regclass
     AND initial.objoid = namespaces.oid
     AND initial.objsubid = 0
     AND initial.privtype IN ('i', 'e')
    WHERE namespaces.nspname = 'pg_catalog'
       OR namespaces.nspname = 'information_schema'
    UNION ALL
    SELECT 'relation'::text AS object_class,
           relations.oid::bigint AS object_oid,
           0::integer AS object_sub_id,
           (namespaces.nspname || '.' || relations.relname)::text AS object_identity,
           relations.relowner::bigint AS owner_role_oid,
           COALESCE(
               relations.relacl,
               pg_catalog.acldefault(
                   (CASE
                       WHEN relations.relkind = 'S' THEN 's'
                       ELSE 'r'
                   END)::"char",
                   relations.relowner
               )
           ) AS current_acl,
           COALESCE(
               initial.initprivs,
               pg_catalog.acldefault(
                   (CASE
                       WHEN relations.relkind = 'S' THEN 's'
                       ELSE 'r'
                   END)::"char",
                   relations.relowner
               )
           ) AS baseline_acl
    FROM pg_catalog.pg_class AS relations
    JOIN pg_catalog.pg_namespace AS namespaces
      ON namespaces.oid = relations.relnamespace
    LEFT JOIN pg_catalog.pg_init_privs AS initial
      ON initial.classoid = 'pg_catalog.pg_class'::pg_catalog.regclass
     AND initial.objoid = relations.oid
     AND initial.objsubid = 0
     AND initial.privtype IN ('i', 'e')
    WHERE namespaces.nspname = 'pg_catalog'
       OR namespaces.nspname = 'information_schema'
    UNION ALL
    SELECT 'column'::text AS object_class,
           relations.oid::bigint AS object_oid,
           attributes.attnum::integer AS object_sub_id,
           (
               namespaces.nspname || '.' || relations.relname || '.' ||
               attributes.attname
           )::text AS object_identity,
           relations.relowner::bigint AS owner_role_oid,
           COALESCE(
               attributes.attacl,
               pg_catalog.acldefault('c', relations.relowner)
           ) AS current_acl,
           COALESCE(
               initial.initprivs,
               pg_catalog.acldefault('c', relations.relowner)
           ) AS baseline_acl
    FROM pg_catalog.pg_attribute AS attributes
    JOIN pg_catalog.pg_class AS relations ON relations.oid = attributes.attrelid
    JOIN pg_catalog.pg_namespace AS namespaces
      ON namespaces.oid = relations.relnamespace
    LEFT JOIN pg_catalog.pg_init_privs AS initial
      ON initial.classoid = 'pg_catalog.pg_class'::pg_catalog.regclass
     AND initial.objoid = relations.oid
     AND initial.objsubid = attributes.attnum
     AND initial.privtype IN ('i', 'e')
    WHERE attributes.attnum > 0
      AND NOT attributes.attisdropped
      AND (
          namespaces.nspname = 'pg_catalog'
          OR namespaces.nspname = 'information_schema'
      )
    UNION ALL
    SELECT 'routine'::text AS object_class,
           routines.oid::bigint AS object_oid,
           0::integer AS object_sub_id,
           (namespaces.nspname || '.' || routines.proname)::text AS object_identity,
           routines.proowner::bigint AS owner_role_oid,
           COALESCE(
               routines.proacl,
               pg_catalog.acldefault('f', routines.proowner)
           ) AS current_acl,
           COALESCE(
               initial.initprivs,
               pg_catalog.acldefault('f', routines.proowner)
           ) AS baseline_acl
    FROM pg_catalog.pg_proc AS routines
    JOIN pg_catalog.pg_namespace AS namespaces
      ON namespaces.oid = routines.pronamespace
    LEFT JOIN pg_catalog.pg_init_privs AS initial
      ON initial.classoid = 'pg_catalog.pg_proc'::pg_catalog.regclass
     AND initial.objoid = routines.oid
     AND initial.objsubid = 0
     AND initial.privtype IN ('i', 'e')
    WHERE namespaces.nspname = 'pg_catalog'
       OR namespaces.nspname = 'information_schema'
    UNION ALL
    SELECT 'type'::text AS object_class,
           types.oid::bigint AS object_oid,
           0::integer AS object_sub_id,
           (namespaces.nspname || '.' || types.typname)::text AS object_identity,
           types.typowner::bigint AS owner_role_oid,
           COALESCE(
               types.typacl,
               pg_catalog.acldefault('T', types.typowner)
           ) AS current_acl,
           COALESCE(
               initial.initprivs,
               pg_catalog.acldefault('T', types.typowner)
           ) AS baseline_acl
    FROM pg_catalog.pg_type AS types
    JOIN pg_catalog.pg_namespace AS namespaces
      ON namespaces.oid = types.typnamespace
    LEFT JOIN pg_catalog.pg_init_privs AS initial
      ON initial.classoid = 'pg_catalog.pg_type'::pg_catalog.regclass
     AND initial.objoid = types.oid
     AND initial.objsubid = 0
     AND initial.privtype IN ('i', 'e')
    WHERE namespaces.nspname = 'pg_catalog'
       OR namespaces.nspname = 'information_schema'
    UNION ALL
    SELECT 'language'::text AS object_class,
           languages.oid::bigint AS object_oid,
           0::integer AS object_sub_id,
           languages.lanname::text AS object_identity,
           languages.lanowner::bigint AS owner_role_oid,
           COALESCE(
               languages.lanacl,
               pg_catalog.acldefault('l', languages.lanowner)
           ) AS current_acl,
           COALESCE(
               initial.initprivs,
               pg_catalog.acldefault('l', languages.lanowner)
           ) AS baseline_acl
    FROM pg_catalog.pg_language AS languages
    LEFT JOIN pg_catalog.pg_init_privs AS initial
      ON initial.classoid = 'pg_catalog.pg_language'::pg_catalog.regclass
     AND initial.objoid = languages.oid
     AND initial.objsubid = 0
     AND initial.privtype IN ('i', 'e')
    UNION ALL
    SELECT 'foreign_data_wrapper'::text AS object_class,
           wrappers.oid::bigint AS object_oid,
           0::integer AS object_sub_id,
           wrappers.fdwname::text AS object_identity,
           wrappers.fdwowner::bigint AS owner_role_oid,
           COALESCE(
               wrappers.fdwacl,
               pg_catalog.acldefault('F', wrappers.fdwowner)
           ) AS current_acl,
           COALESCE(
               initial.initprivs,
               pg_catalog.acldefault('F', wrappers.fdwowner)
           ) AS baseline_acl
    FROM pg_catalog.pg_foreign_data_wrapper AS wrappers
    LEFT JOIN pg_catalog.pg_init_privs AS initial
      ON initial.classoid = 'pg_catalog.pg_foreign_data_wrapper'::pg_catalog.regclass
     AND initial.objoid = wrappers.oid
     AND initial.objsubid = 0
     AND initial.privtype IN ('i', 'e')
    UNION ALL
    SELECT 'foreign_server'::text AS object_class,
           servers.oid::bigint AS object_oid,
           0::integer AS object_sub_id,
           servers.srvname::text AS object_identity,
           servers.srvowner::bigint AS owner_role_oid,
           COALESCE(
               servers.srvacl,
               pg_catalog.acldefault('S', servers.srvowner)
           ) AS current_acl,
           COALESCE(
               initial.initprivs,
               pg_catalog.acldefault('S', servers.srvowner)
           ) AS baseline_acl
    FROM pg_catalog.pg_foreign_server AS servers
    LEFT JOIN pg_catalog.pg_init_privs AS initial
      ON initial.classoid = 'pg_catalog.pg_foreign_server'::pg_catalog.regclass
     AND initial.objoid = servers.oid
     AND initial.objsubid = 0
     AND initial.privtype IN ('i', 'e')
),
current_entries AS (
    SELECT objects.object_class,
           objects.object_oid,
           objects.object_sub_id,
           objects.object_identity,
           acl.grantor::bigint AS grantor_role_oid,
           acl.grantee::bigint AS grantee_role_oid,
           acl.privilege_type::text AS privilege_type,
           acl.is_grantable::boolean AS is_grantable
    FROM acl_objects AS objects
    CROSS JOIN LATERAL pg_catalog.aclexplode(objects.current_acl) AS acl
),
baseline_entries AS (
    SELECT objects.object_class,
           objects.object_oid,
           objects.object_sub_id,
           objects.object_identity,
           acl.grantor::bigint AS grantor_role_oid,
           acl.grantee::bigint AS grantee_role_oid,
           acl.privilege_type::text AS privilege_type,
           acl.is_grantable::boolean AS is_grantable
    FROM acl_objects AS objects
    CROSS JOIN LATERAL pg_catalog.aclexplode(objects.baseline_acl) AS acl
    UNION ALL
    SELECT objects.object_class,
           objects.object_oid,
           objects.object_sub_id,
           objects.object_identity,
           objects.owner_role_oid AS grantor_role_oid,
           0::bigint AS grantee_role_oid,
           CASE
               WHEN objects.object_class = 'schema' THEN 'USAGE'
               ELSE 'SELECT'
           END::text AS privilege_type,
           false::boolean AS is_grantable
    FROM acl_objects AS objects
    WHERE (
        objects.object_class = 'schema'
        AND objects.object_identity = 'information_schema'
    )
       OR (
           objects.object_class = 'relation'
           AND objects.object_identity IN (
               'information_schema.administrable_role_authorizations',
               'information_schema.applicable_roles',
               'information_schema.attributes',
               'information_schema.character_sets',
               'information_schema.check_constraint_routine_usage',
               'information_schema.check_constraints',
               'information_schema.collation_character_set_applicability',
               'information_schema.collations',
               'information_schema.column_column_usage',
               'information_schema.column_domain_usage',
               'information_schema.column_options',
               'information_schema.column_privileges',
               'information_schema.column_udt_usage',
               'information_schema.columns',
               'information_schema.constraint_column_usage',
               'information_schema.constraint_table_usage',
               'information_schema.data_type_privileges',
               'information_schema.domain_constraints',
               'information_schema.domain_udt_usage',
               'information_schema.domains',
               'information_schema.element_types',
               'information_schema.enabled_roles',
               'information_schema.foreign_data_wrapper_options',
               'information_schema.foreign_data_wrappers',
               'information_schema.foreign_server_options',
               'information_schema.foreign_servers',
               'information_schema.foreign_table_options',
               'information_schema.foreign_tables',
               'information_schema.information_schema_catalog_name',
               'information_schema.key_column_usage',
               'information_schema.parameters',
               'information_schema.referential_constraints',
               'information_schema.role_column_grants',
               'information_schema.role_routine_grants',
               'information_schema.role_table_grants',
               'information_schema.role_udt_grants',
               'information_schema.role_usage_grants',
               'information_schema.routine_column_usage',
               'information_schema.routine_privileges',
               'information_schema.routine_routine_usage',
               'information_schema.routine_sequence_usage',
               'information_schema.routine_table_usage',
               'information_schema.routines',
               'information_schema.schemata',
               'information_schema.sequences',
               'information_schema.sql_features',
               'information_schema.sql_implementation_info',
               'information_schema.sql_sizing',
               'information_schema.table_constraints',
               'information_schema.table_privileges',
               'information_schema.tables',
               'information_schema.triggered_update_columns',
               'information_schema.triggers',
               'information_schema.udt_privileges',
               'information_schema.usage_privileges',
               'information_schema.user_defined_types',
               'information_schema.user_mapping_options',
               'information_schema.user_mappings',
               'information_schema.view_column_usage',
               'information_schema.view_routine_usage',
               'information_schema.view_table_usage',
               'information_schema.views'
           )
       )
),
delta_entries AS (
    SELECT 'current_only'::text AS delta_side,
           current_entries.object_class,
           current_entries.object_oid,
           current_entries.object_sub_id,
           current_entries.object_identity,
           current_entries.grantor_role_oid,
           current_entries.grantee_role_oid,
           current_entries.privilege_type,
           current_entries.is_grantable
    FROM current_entries
    EXCEPT
    SELECT 'current_only'::text AS delta_side,
           baseline_entries.object_class,
           baseline_entries.object_oid,
           baseline_entries.object_sub_id,
           baseline_entries.object_identity,
           baseline_entries.grantor_role_oid,
           baseline_entries.grantee_role_oid,
           baseline_entries.privilege_type,
           baseline_entries.is_grantable
    FROM baseline_entries
    UNION ALL
    SELECT 'baseline_only'::text AS delta_side,
           baseline_entries.object_class,
           baseline_entries.object_oid,
           baseline_entries.object_sub_id,
           baseline_entries.object_identity,
           baseline_entries.grantor_role_oid,
           baseline_entries.grantee_role_oid,
           baseline_entries.privilege_type,
           baseline_entries.is_grantable
    FROM baseline_entries
    EXCEPT
    SELECT 'baseline_only'::text AS delta_side,
           current_entries.object_class,
           current_entries.object_oid,
           current_entries.object_sub_id,
           current_entries.object_identity,
           current_entries.grantor_role_oid,
           current_entries.grantee_role_oid,
           current_entries.privilege_type,
           current_entries.is_grantable
    FROM current_entries
)
SELECT delta_entries.delta_side,
       delta_entries.object_class,
       delta_entries.object_oid,
       delta_entries.object_sub_id,
       delta_entries.object_identity,
       delta_entries.grantor_role_oid,
       grantors.rolname::text AS grantor_role_name,
       delta_entries.grantee_role_oid,
       grantees.rolname::text AS grantee_role_name,
       delta_entries.privilege_type,
       delta_entries.is_grantable
FROM delta_entries
LEFT JOIN pg_catalog.pg_roles AS grantors
  ON grantors.oid = delta_entries.grantor_role_oid
LEFT JOIN pg_catalog.pg_roles AS grantees
  ON grantees.oid = delta_entries.grantee_role_oid
ORDER BY delta_entries.object_class,
         delta_entries.object_oid,
         delta_entries.object_sub_id,
         delta_entries.delta_side,
         delta_entries.grantee_role_oid,
         delta_entries.privilege_type,
         delta_entries.grantor_role_oid,
         delta_entries.is_grantable;
