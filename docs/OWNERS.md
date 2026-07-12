# Architecture ownership

The repository uses GitHub team owners so architecture decisions have an
accountable review path even when individual maintainers change. `@zlong928`
is included as the current repository-owner fallback until the role teams are
created in the GitHub organization.

| Area | Owner | Scope |
|---|---|---|
| Backend | `@extraction/backend` | FastAPI routes, application services, ORM/API contracts |
| Content pipeline | `@extraction/content-pipeline` | `content_pipeline/`, phase contracts, quality gates |
| Frontend | `@extraction/frontend` | React/Vite workspace and HTTP DTO usage |
| Platform | `@extraction/platform` | Redis, SQLite/Alembic, storage, Docker and runtime recovery |
| Data quality | `@extraction/data-quality` | Audit/CSV schema, provenance and release policy |
| Engineering | `@extraction/engineering` | Cross-boundary architecture and final ownership escalation |

The team aliases are the source of truth for CODEOWNERS. If the GitHub
organization renames a team, update `.github/CODEOWNERS` and this table in the
same change.
