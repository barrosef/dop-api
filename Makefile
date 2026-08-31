# Atalhos do dia a dia. Tudo passa por `uv` — sem venv ativado à mão.
.PHONY: proto proto-bff proto-all test lint run

## proto: regenera os stubs Python a partir dos .proto do dop-core
proto:
	./scripts/gen_proto.sh

## proto-bff: regenera os stubs do contrato de BORDA (api/proto, deste repo)
proto-bff:
	./scripts/gen_bff_proto.sh

## proto-all: as duas gerações — o contrato do núcleo e o nosso
proto-all: proto proto-bff

## test: roda a suíte
test:
	uv run pytest -q

## lint: ruff sobre o código escrito à mão (o gerado é excluído no pyproject)
lint:
	uv run ruff check app tests

## run: sobe o BFF em modo desenvolvimento
run:
	uv run uvicorn app.main:app --reload --port 8000
