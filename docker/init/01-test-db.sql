-- Create the isolated test database alongside the app's dev database `dsw`.
-- PostgreSQL's public `dsw` role is a superuser (default POSTGRES_USER), so it
-- can create databases. This runs only on first volume init (when the pgdata
-- volume is created/`docker compose down -v`).
CREATE DATABASE dsw_test OWNER dsw;