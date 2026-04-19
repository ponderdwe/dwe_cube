// Cube.js configuration
// Reference: https://cube.dev/docs/config

module.exports = {
  // Schema (model) directory — populated by dbt-cube-sync
  schemaPath: "model",

  // Expose Postgres-compatible wire protocol on port 15432
  pgSqlPort: 15432,

  // Cache schema version so Cube.js picks up schema changes automatically
  schemaVersion: ({ authInfo }) => {
    return process.env.CUBEJS_SCHEMA_VERSION || "1";
  },

  // Query rewrite hook — add row-level security or tenant filtering here
  queryRewrite: (query, { authInfo }) => {
    return query;
  },

  // Context to app ID — used for cache isolation per tenant if needed
  contextToAppId: ({ tenantId }) => {
    return tenantId ? `CUBE_APP_${tenantId}` : "CUBE_APP_DEFAULT";
  },
};
