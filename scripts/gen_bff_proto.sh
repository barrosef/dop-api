#!/usr/bin/env bash
# Gera os stubs Python do contrato de BORDA (dop.bff.v1).
#
# Diferença essencial para o gen_proto.sh: estes .proto são NOSSOS — vivem
# neste repositório (api/proto), porque quem define o contrato da borda é o
# BFF. Os do núcleo vivem no dop-core e aqui só se consomem.
#
# O diretório de saída é regenerável e não se edita à mão.
#
# Uso:
#   ./scripts/gen_bff_proto.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_ROOT="${DOP_BFF_PROTO:-$ROOT/api/proto}"
OUT="$ROOT/app/grpcapi/gen"
PKG="app.grpcapi.gen"

if [[ ! -d "$PROTO_ROOT/dop/bff/v1" ]]; then
  echo "erro: protos da borda não encontrados em $PROTO_ROOT" >&2
  exit 1
fi

echo "protos: $PROTO_ROOT"
echo "saída:  $OUT"

rm -rf "$OUT"
mkdir -p "$OUT"

# -I na RAIZ dos protos: o caminho do import ("dop/bff/v1/identity.proto") é o
# que vira o caminho do módulo gerado.
uv run python -m grpc_tools.protoc \
  -I "$PROTO_ROOT" \
  --python_out="$OUT" \
  --pyi_out="$OUT" \
  --grpc_python_out="$OUT" \
  "$PROTO_ROOT"/dop/bff/v1/*.proto

# ── o mesmo problema de import do gen_proto.sh ───────────────────────────────
# protoc gera "from dop.bff.v1 import identity_pb2", que só resolveria com
# `gen/` na raiz do sys.path. Mexer no sys.path em tempo de execução quebra de
# formas difíceis de depurar (e de maneira diferente sob pytest e sob uvicorn),
# então reescrevemos o import para o caminho real do pacote: determinístico,
# visível no diff, e o resto do app importa como qualquer outro módulo.
uv run python - "$OUT" "$PKG" <<'PY'
import pathlib
import re
import sys

out, pkg = pathlib.Path(sys.argv[1]), sys.argv[2]
padrao = re.compile(r"^from dop\.bff\.v1 import ", flags=re.MULTILINE)
tocados = 0
for arquivo in sorted([*out.rglob("*.py"), *out.rglob("*.pyi")]):
    texto = arquivo.read_text(encoding="utf-8")
    novo = padrao.sub(f"from {pkg}.dop.bff.v1 import ", texto)
    if novo != texto:
        arquivo.write_text(novo, encoding="utf-8")
        tocados += 1
print(f"imports reescritos em {tocados} arquivo(s)")
PY

# Pacotes reais (com __init__.py), não namespace packages implícitos: assim o
# empacotamento pelo hatchling leva os stubs junto.
for dir in "$OUT" "$OUT/dop" "$OUT/dop/bff" "$OUT/dop/bff/v1"; do
  cat > "$dir/__init__.py" <<'PY'
"""Gerado por scripts/gen_bff_proto.sh a partir de api/proto. Não editar."""
PY
done

echo "ok: $(find "$OUT" -name '*_pb2.py' | wc -l) módulos de mensagem, $(find "$OUT" -name '*_pb2_grpc.py' | wc -l) de serviço"
