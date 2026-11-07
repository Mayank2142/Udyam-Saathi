CREATE TABLE IF NOT EXISTS activation_cases (
    owner_key char(64) PRIMARY KEY REFERENCES owner_cases(owner_key) ON DELETE CASCADE,
    quote_id uuid NOT NULL REFERENCES quote_revisions(id),
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_outbox (
    order_id uuid NOT NULL,
    ordinal integer NOT NULL,
    owner_key char(64) NOT NULL REFERENCES activation_cases(owner_key) ON DELETE CASCADE,
    step text NOT NULL,
    due_at timestamptz NOT NULL,
    applied_at timestamptz,
    PRIMARY KEY (order_id, ordinal)
);
