Forked from Sevyn8/ithina-retail-admin-backend @ e13a9aa098c69da08ad75ae53bf3841a08fddfa7 on 2026-06-21.
Hard fork: Cortex identity plane. Internal "ithina" names (163 files, incl. schema SQL, openapi, dockerignore) retained at fork; de-Ithina as a separate task with tests green. Port only security/correctness fixes from the client repo, by hand. Do not sync features.

Scope: backend only. The CM admin UI (Sevyn8/ithina-retail-admin-frontend) was deliberately NOT forked; it predates the experience-plane archetype model and is a later decision (fork-to-salvage vs rebuild as archetypes).
