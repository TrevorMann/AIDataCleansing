1. Understand the Request
Parse the user's description to identify:

Output columns: What fields should the result include?
Filters: What conditions limit the data (time ranges, segments, statuses)?
Aggregations: Are there GROUP BY operations, counts, sums, averages?
Joins: Does this require combining multiple tables?
Ordering: How should results be sorted?
Limits: Is there a top-N or sample requirement?

2. Determine SQL Dialect
If the user's SQL dialect is not already known, ask which they use:

PostgreSQL (including Aurora, RDS, Supabase, Neon)
Snowflake
BigQuery (Google Cloud)
Redshift (Amazon)
Databricks SQL
MySQL (including Aurora MySQL, PlanetScale)
SQL Server (Microsoft)
DuckDB
SQLite
Other (ask for specifics)

Remember the dialect for future queries in the same session.

4. Write the Query
Follow these best practices:
Structure:

Use CTEs (WITH clauses) for readability when queries have multiple logical steps
One CTE per logical transformation or data source
Name CTEs descriptively (e.g., daily_signups, active_users, revenue_by_product)

Performance:

Never use SELECT * in production queries -- specify only needed columns
Filter early (push WHERE clauses as close to the base tables as possible)
Use partition filters when available (especially date partitions)
Prefer EXISTS over IN for subqueries with large result sets
Use appropriate JOIN types (don't use LEFT JOIN when INNER JOIN is correct)
Avoid correlated subqueries when a JOIN or window function works
Be mindful of exploding joins (many-to-many)

Readability:

Add comments explaining the "why" for non-obvious logic
Use consistent indentation and formatting
Alias tables with meaningful short names (not just a, b, c)
Put each major clause on its own line

Dialect-specific optimizations:

Apply dialect-specific syntax and functions (see sql-queries skill for details)
Use dialect-appropriate date functions, string functions, and window syntax
Note any dialect-specific performance features (e.g., Snowflake clustering, BigQuery partitioning)