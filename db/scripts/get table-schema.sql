-- Change ONE thing: the table name on the line below.
WITH t AS (
    SELECT 'core' AS schema_name, 'stores' AS table_name
)
SELECT
    t.schema_name AS schema,
    t.table_name AS table_name,
    c.column_name,
    CASE
        WHEN c.data_type = 'USER-DEFINED' THEN c.udt_name
        WHEN c.data_type = 'character varying' AND c.character_maximum_length IS NOT NULL
            THEN 'varchar(' || c.character_maximum_length || ')'
        WHEN c.data_type = 'numeric' AND c.numeric_precision IS NOT NULL
            THEN 'numeric(' || c.numeric_precision || ',' || COALESCE(c.numeric_scale, 0) || ')'
        ELSE c.data_type
    END AS data_type,
    CASE WHEN c.is_nullable = 'NO' THEN 'NOT NULL' ELSE 'NULL' END AS nullable,
    c.column_default AS default_value,
    NULLIF(
        TRIM(BOTH ' ' FROM
            COALESCE((SELECT 'PK ' FROM information_schema.table_constraints tc
                      JOIN information_schema.key_column_usage k
                        ON k.constraint_name = tc.constraint_name
                       AND k.table_schema = tc.table_schema
                       AND k.table_name = tc.table_name
                       AND k.column_name = c.column_name
                      WHERE tc.constraint_type = 'PRIMARY KEY'
                        AND tc.table_schema = c.table_schema
                        AND tc.table_name = c.table_name
                      LIMIT 1), '')
            ||
            COALESCE((SELECT 'UQ ' FROM information_schema.table_constraints tc
                      JOIN information_schema.key_column_usage k
                        ON k.constraint_name = tc.constraint_name
                       AND k.table_schema = tc.table_schema
                       AND k.table_name = tc.table_name
                       AND k.column_name = c.column_name
                      WHERE tc.constraint_type = 'UNIQUE'
                        AND tc.table_schema = c.table_schema
                        AND tc.table_name = c.table_name
                      LIMIT 1), '')
            ||
            COALESCE((SELECT 'FK->' || ccu.table_name || '.' || ccu.column_name
                      FROM information_schema.table_constraints tc
                      JOIN information_schema.key_column_usage k
                        ON k.constraint_name = tc.constraint_name
                       AND k.table_schema = tc.table_schema
                       AND k.table_name = tc.table_name
                       AND k.column_name = c.column_name
                      JOIN information_schema.constraint_column_usage ccu
                        ON ccu.constraint_name = tc.constraint_name
                       AND ccu.table_schema = tc.table_schema
                      WHERE tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_schema = c.table_schema
                        AND tc.table_name = c.table_name
                      LIMIT 1), '')
        ),
        ''
    ) AS constraints
FROM information_schema.columns c
JOIN t ON t.schema_name = c.table_schema AND t.table_name = c.table_name
ORDER BY c.ordinal_position;