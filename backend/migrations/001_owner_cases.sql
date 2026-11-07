CREATE TABLE IF NOT EXISTS owner_cases (
    owner_key char(64) PRIMARY KEY,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quote_revisions (
    id uuid PRIMARY KEY,
    owner_key char(64) NOT NULL REFERENCES owner_cases (owner_key) ON DELETE CASCADE,
    case_revision integer NOT NULL CHECK (case_revision > 0),
    payload jsonb NOT NULL,
    accepted_at timestamptz,
    UNIQUE (owner_key, case_revision)
);
