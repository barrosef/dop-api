"""O BFF traduz a semântica de err do core — não inventa a sua."""

from grpc import StatusCode

from app.platform.errors import http_status_for


def test_mapeamento_grpc_para_http():
    casos = {
        StatusCode.NOT_FOUND: 404,
        StatusCode.PERMISSION_DENIED: 403,
        StatusCode.UNAUTHENTICATED: 401,
        StatusCode.INVALID_ARGUMENT: 400,
        StatusCode.ALREADY_EXISTS: 409,
        StatusCode.FAILED_PRECONDITION: 412,
        StatusCode.UNAVAILABLE: 503,
        StatusCode.DEADLINE_EXCEEDED: 504,
    }
    for code, expected in casos.items():
        assert http_status_for(code) == expected, f"{code} deveria virar {expected}"


def test_codigo_desconhecido_vira_500():
    assert http_status_for(StatusCode.UNKNOWN) == 500
