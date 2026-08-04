-- Bootstrap for the local development stack's Postgres.
--
-- Runs once, on first initialisation of an empty postgres_data volume, via
-- /docker-entrypoint-initdb.d. Creates roles and databases only; no application
-- DDL lives here. Table DDL for the `martyrology` database belongs in
-- alembic/versions/ and is applied by the api-migrate service.
--
-- Zitadel creates its own database from the admin credentials it is given, so
-- only the openfga and martyrology databases are created here.

CREATE ROLE openfga WITH LOGIN PASSWORD 'openfga_secure_password';
CREATE DATABASE openfga OWNER openfga;

CREATE ROLE martyrology WITH LOGIN PASSWORD 'martyrology_secure_password';
CREATE DATABASE martyrology OWNER martyrology;
