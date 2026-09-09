# Credential transaction integrity

Administrator setup, rotation and revocation pass the existing SQLAlchemy session to the vault. The tenant reference, fingerprint and encrypted value share one commit. Vault writes flush through that session without opening a second SQLite writer or caching an uncommitted credential. A failed write or commit rolls the transaction back and returns a safe failure; it cannot report setup complete on the strength of an in-memory secret.

Standalone vault writes and revocations still own their database transaction. They update the fallback cache only after a successful commit and raise `VaultPersistenceError` on persistence failure. Durable reads mark the reference as previously persisted, so a later deletion by another process cannot revive a cached credential. No encryption format, key, or existing ciphertext is migrated by this change.

Durability requires both committed ciphertext and a stable decryption key. Production must keep its configured non-default vault passphrase or a securely persisted installation key across restarts. The pre-existing local installation-key fallback can remain ephemeral if its directory is unwritable; this change does not certify that configuration or replace key management. Back up the encrypted database and protect the corresponding key separately. Never log either the key or the credential plaintext.

Historical references created by the previous failed-write fallback may point to ciphertext that never reached the database. This patch cannot reconstruct a credential already lost with its process cache. Such a reference needs the credential supplied again through the administrator workflow; a reference or fingerprint alone is not evidence of recoverable ciphertext.

The transaction regressions use file-backed SQLite, two independently persisted setup credentials, cache loss, failed second writes, failed commits, rotation, revocation and cross-process deletion. A queue that cannot durably revoke a one-time credential must stop before portal execution and report a retryable infrastructure failure.
