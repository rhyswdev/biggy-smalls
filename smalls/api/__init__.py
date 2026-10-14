"""The API the web UI talks to (docs/decisions/ui-build.md): FastAPI over the agent, in process. It holds the
model keys, one in-memory SQLite database with the app's state, the run records and the checkpoints, and the
runs. The browser never calls it directly: the web app's server does."""
