# Day-to-day shortcuts. Everything goes through `uv` — no hand-activated venv.
.PHONY: proto proto-bff proto-all test lint run

## proto: regenerate the Python stubs from dop-core's .proto files
proto:
	./scripts/gen_proto.sh

## proto-bff: regenerate the stubs of the EDGE contract (api/proto, in this repo)
proto-bff:
	./scripts/gen_bff_proto.sh

## proto-all: both generations — the core's contract and ours
proto-all: proto proto-bff

## test: run the suite
test:
	uv run pytest -q

## lint: ruff over the hand-written code (the generated code is excluded in pyproject)
lint:
	uv run ruff check app tests

## run: bring the BFF up in development mode
run:
	uv run uvicorn app.main:app --reload --port 8000
