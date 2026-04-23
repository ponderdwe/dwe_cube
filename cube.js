// Cube.js configuration
// Reference: https://cube.dev/docs/config

const fs = require('fs');
const path = require('path');

const SCHEMA_VERSION_FILE = path.join(__dirname, '.schema-version');

function getSchemaVersion() {
  try {
    return fs.readFileSync(SCHEMA_VERSION_FILE, 'utf8').trim();
  } catch (err) {
    return '1';
  }
}

module.exports = {
  // Schema (model) directory — populated by dbt-cube-sync
  schemaPath: "model/cubes",

  // Expose Postgres-compatible wire protocol on port 15432
  pgSqlPort: 15432,

  // Version written to .schema-version by EC2 bootstrap (git SHA) — forces
  // cache invalidation on every deployment without a manual bump.
  schemaVersion: () => getSchemaVersion(),

  // Query rewrite hook — add row-level security or tenant filtering here
  queryRewrite: (query, { authInfo }) => {
    return query;
  },

  // Context to app ID — used for cache isolation per tenant if needed
  contextToAppId: ({ tenantId }) => {
    return tenantId ? `CUBE_APP_${tenantId}` : "CUBE_APP_DEFAULT";
  },
};
