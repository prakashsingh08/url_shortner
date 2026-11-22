-- add-platform-foundation: baseline schema.
-- Key/value settings written once at first boot and checked on later boots
-- (e.g. the CODE_PERMUTATION_KEY fingerprint added by add-url-shortening, LLD §6.2).
CREATE TABLE system_settings (
    name        TEXT        PRIMARY KEY,
    value       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
