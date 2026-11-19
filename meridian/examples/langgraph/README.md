# LangGraph example

Phase 5 of Meridian runs a Strands Graph on AgentCore Runtime. This directory is a
persistence and pause/resume example for LangGraph: it saves checkpoints in Amazon Aurora
and resumes a paused graph from any process that can reach the cluster. It is not a copy of
Meridian's governed workflow. Its graph makes up two options from the request and marks the
first as held after the resume, even for an ordinary question; it places no real hold,
calls no Gateway tool, and has no worker lease or recovery. The governed booking and
recovery behavior is in Meridian's Strands path. The application does not import this
directory, and the backend image does not install LangGraph.

## When to choose it

Choose LangGraph when you already build on LangGraph and want its checkpoints in Amazon
Aurora without opening a database connection from the client. `AuroraDataApiSaver`
writes the standard LangGraph checkpoint tables through the RDS Data API, so a
paused workflow resumes from any process that can reach the cluster.

Choose the Strands Graph on AgentCore Runtime when you want the managed runtime, the
saved steps in `workflow_snapshots`, and the Phase 5 behavior the rest of Meridian
shows.

## What is here

| File | Role |
| --- | --- |
| `aurora_dataapi_saver.py` | LangGraph checkpoint saver over the RDS Data API |
| `blob_windows.py` | Splits large values into rows the Data API can return |
| `pause_resume_graph.py` | Classify, search, pause for review, then hold |
| `requirements.txt` | The pinned LangGraph packages for this example only |
| `tests/` | Unit tests, live Amazon Aurora tests and the conformance suite |

## Run the unit tests

From `meridian/`, install the backend requirements and then the example's:

```sh
venv/bin/python -m pip install -r examples/langgraph/requirements.txt
venv/bin/python -m pytest examples/langgraph/tests -m "not database" -q -p no:cacheprovider
```

These tests use an in-memory saver and mocked clients. They run in CI.

## Run the live tests

The tests marked `database` and the vendored conformance suite in
`tests/conformance/` write to a real Amazon Aurora cluster over the Data API. They need
AWS credentials, the cluster settings from `.env`, and migration 007 applied (it
creates `checkpoints`, `checkpoint_blobs` and `checkpoint_writes`). Each live test
writes under a thread id generated for that test or, in the conformance suite, for the
run and the pytest-xdist worker (`tests/conformance/thread_ids.py`), and deletes only
those ids. A checkpoint thread of yours or of another participant, even one named
`thread-1`, is never read or deleted.

```sh
AWS_PROFILE=<profile> venv/bin/python -m pytest examples/langgraph/tests -m database -q -p no:cacheprovider
```
