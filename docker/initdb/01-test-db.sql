-- Separate database for the test suite, so tests never touch real data.
-- Runs only when the postgres volume is created for the first time.
CREATE DATABASE prices_test;
