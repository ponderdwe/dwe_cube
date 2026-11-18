// Copyright 2026 Ponder
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Cube.js configuration
// Reference: https://cube.dev/docs/config

const fs   = require('fs');
const path = require('path');

// In production, model/cubes is mounted from object storage (S3 via Mountpoint,
// Azure Blob via blobfuse2). CI writes schemas and .schema-version there via
// dbt-cube-sync. In dev, model/cubes is a plain local directory.
const SCHEMA_VERSION_FILE = path.join(__dirname, 'model/cubes/.schema-version');

function getSchemaVersion() {
  try {
    return fs.readFileSync(SCHEMA_VERSION_FILE, 'utf8').trim();
  } catch {
    return '1';
  }
}

module.exports = {
  schemaPath: 'model/cubes',

  pgSqlPort: 15432,

  schemaVersion: () => getSchemaVersion(),

  queryRewrite: (query, { authInfo }) => {
    return query;
  },

  contextToAppId: ({ tenantId }) => {
    return tenantId ? `CUBE_APP_${tenantId}` : 'CUBE_APP_DEFAULT';
  },
};
