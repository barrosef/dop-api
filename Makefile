# Atalhos do dia a dia. Tudo passa por `uv` — sem venv ativado à mão.
.PHONY: proto test lint run

## proto: regenera os stubs Python a partir dos .proto do dop-core
proto:
	./scripts/gen_proto.sh

## test: roda a suíte
test:
	uv run pytest -q

## lint: ruff sobre o código escrito à mão (o gerado é excluído no pyproject)
lint:
	uv run ruff check app tests

## run: sobe o BFF em modo desenvolvimento
run:
	uv run uvicorn app.main:app --reload --port 8000
