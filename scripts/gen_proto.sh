#!/usr/bin/env bash
# Gera os stubs Python a partir dos .proto do dop-core.
#
# Os contratos são a FONTE DA VERDADE e vivem no dop-core (api/proto). Aqui só
# se gera — nada neste diretório é editado à mão.
#
# Uso:
#   ./scripts/gen_proto.sh                 # usa ../dop-core/api/proto
#   DOP_CORE_PROTO=/caminho ./scripts/gen_proto.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_ROOT="${DOP_CORE_PROTO:-$(cd "$ROOT/.." && pwd)/dop-core/api/proto}"
OUT="$ROOT/app/coreclient/gen"
PKG="app.coreclient.gen"

if [[ ! -d "$PROTO_ROOT/dop/v1" ]]; then
  echo "erro: protos não encontrados em $PROTO_ROOT" >&2
  echo "      aponte DOP_CORE_PROTO para <dop-core>/api/proto" >&2
  exit 1
fi

echo "protos: $PROTO_ROOT"
echo "saída:  $OUT"

rm -rf "$OUT"
mkdir -p "$OUT"

# -I na RAIZ dos protos: o caminho do import ("dop/v1/common.proto") é o que
# vira o caminho do módulo gerado. Os well-known types (timestamp, struct) já
# vêm no include embutido do grpc_tools.
uv run python -m grpc_tools.protoc \
  -I "$PROTO_ROOT" \
  --python_out="$OUT" \
  --pyi_out="$OUT" \
  --grpc_python_out="$OUT" \
  "$PROTO_ROOT"/dop/v1/*.proto

# ── o problema dos imports ───────────────────────────────────────────────────
# protoc gera "from dop.v1 import common_pb2", que só resolve se `gen/` estiver
# na raiz do sys.path. Em vez de mexer no sys.path em tempo de execução (que
# quebra de formas difíceis de depurar), reescrevemos o import para o caminho
# real do pacote. Determinístico, visível no diff, e o resto do app importa
# como qualquer outro módulo.
uv run python - "$OUT" "$PKG" <<'PY'
import pathlib
import re
import sys

out, pkg = pathlib.Path(sys.argv[1]), sys.argv[2]
padrao = re.compile(r"^from dop\.v1 import ", flags=re.MULTILINE)
tocados = 0
for arquivo in sorted([*out.rglob("*.py"), *out.rglob("*.pyi")]):
    texto = arquivo.read_text(encoding="utf-8")
    novo = padrao.sub(f"from {pkg}.dop.v1 import ", texto)
    if novo != texto:
        arquivo.write_text(novo, encoding="utf-8")
        tocados += 1
print(f"imports reescritos em {tocados} arquivo(s)")
PY

# Pacotes reais (com __init__.py), não namespace packages implícitos: assim o
# empacotamento pelo hatchling leva os stubs junto.
for dir in "$OUT" "$OUT/dop" "$OUT/dop/v1"; do
  cat > "$dir/__init__.py" <<'PY'
"""Gerado por scripts/gen_proto.sh a partir dos .proto do dop-core. Não editar."""
PY
done

echo "ok: $(find "$OUT" -name '*_pb2.py' | wc -l) módulos de mensagem, $(find "$OUT" -name '*_pb2_grpc.py' | wc -l) de serviço"
