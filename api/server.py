import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from geometry.types import Point, geometry_from_geojson
from geometry.wkt import parse_wkt, to_wkt
from storage.repository import PolygonRepository

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

_repository: PolygonRepository | None = None


def get_repository() -> PolygonRepository:
    global _repository
    if _repository is None:
        _repository = PolygonRepository(index_cell_size=1.0)
    return _repository


def reset_repository(cell_size: float = 1.0) -> None:
    global _repository
    _repository = PolygonRepository(cell_size)


def _json(handler: BaseHTTPRequestHandler, status: int, data: dict) -> None:
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Невалидный JSON: {e}")


def _parse_path(path: str) -> tuple[str, str | None]:
    parts = path.strip("/").split("/")
    return parts[0], parts[1] if len(parts) > 1 else None


def _render(template: str, **kwargs) -> str:
    with open(os.path.join(TEMPLATES_DIR, template), encoding="utf-8") as f:
        content = f.read()
    for key, val in kwargs.items():
        content = content.replace("{" + key + "}", str(val))
    return content


def _parse_geometry(body: dict):
    """
    Извлечь геометрию из тела запроса.
    Поддерживаются два варианта: GeoJSON в поле 'geometry' или WKT в поле 'wkt'.
    """
    if "wkt" in body and body["wkt"]:
        return parse_wkt(body["wkt"])
    if "geometry" in body and body["geometry"]:
        return geometry_from_geojson(body["geometry"])
    raise ValueError("Нужно указать одно из полей: 'geometry' (GeoJSON) или 'wkt'")


def _enrich_record(record_dict: dict, geometry) -> dict:
    """Добавить к ответу WKT-представление, чтобы клиент мог выбирать формат."""
    record_dict["wkt"] = to_wkt(geometry)
    return record_dict


def handle_root(handler: BaseHTTPRequestHandler) -> None:
    html = _render("index.html",
                   polygon_count=get_repository().count(),
                   python_version=sys.version.split()[0])
    body = html.encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_health(handler: BaseHTTPRequestHandler) -> None:
    _json(handler, 200, {"status": "ok", "polygon_count": get_repository().count()})


def handle_stats(handler: BaseHTTPRequestHandler) -> None:
    repo = get_repository()
    _json(handler, 200, {"polygon_count": repo.count(), "index": repo.index_stats()})


def handle_list(handler: BaseHTTPRequestHandler) -> None:
    records = get_repository().list_all()
    _json(handler, 200, {
        "count": len(records),
        "polygons": [_enrich_record(r.to_dict(), r.geometry) for r in records],
    })


def handle_create(handler: BaseHTTPRequestHandler) -> None:
    body = _read_body(handler)

    name = body.get("name", "")
    if not name:
        raise ValueError("Поле 'name' обязательно")

    geometry = _parse_geometry(body)
    record = get_repository().create(
        name=name,
        geometry=geometry,
        properties=body.get("properties", {}),
    )
    _json(handler, 201, _enrich_record(record.to_dict(), record.geometry))


def handle_get(handler: BaseHTTPRequestHandler, polygon_id: str) -> None:
    record = get_repository().get(polygon_id)
    if record is None:
        _json(handler, 404, {"error": f"Полигон '{polygon_id}' не найден"})
        return
    _json(handler, 200, _enrich_record(record.to_dict(), record.geometry))


def handle_update(handler: BaseHTTPRequestHandler, polygon_id: str) -> None:
    body = _read_body(handler)
    geometry = None
    if "geometry" in body or "wkt" in body:
        geometry = _parse_geometry(body)

    record = get_repository().update(
        polygon_id=polygon_id,
        name=body.get("name"),
        geometry=geometry,
        properties=body.get("properties"),
    )
    if record is None:
        _json(handler, 404, {"error": f"Полигон '{polygon_id}' не найден"})
        return
    _json(handler, 200, _enrich_record(record.to_dict(), record.geometry))


def handle_delete(handler: BaseHTTPRequestHandler, polygon_id: str) -> None:
    if not get_repository().delete(polygon_id):
        _json(handler, 404, {"error": f"Полигон '{polygon_id}' не найден"})
        return
    _json(handler, 200, {"message": f"Полигон '{polygon_id}' удалён"})


def handle_pip(handler: BaseHTTPRequestHandler) -> None:
    body = _read_body(handler)

    raw_point = body.get("point")
    if not raw_point or len(raw_point) < 2:
        raise ValueError("Поле 'point' обязательно: [x, y]")

    algorithm = body.get("algorithm", "ray_casting")
    if algorithm not in ("ray_casting", "winding_number"):
        raise ValueError("algorithm: 'ray_casting' или 'winding_number'")

    point = Point.from_list(raw_point)
    include = body.get("include_boundary", True)
    records = get_repository().find_containing_point(point, algorithm, include)

    _json(handler, 200, {
        "point": raw_point,
        "algorithm": algorithm,
        "include_boundary": include,
        "matching_count": len(records),
        "matching_polygons": [_enrich_record(r.to_dict(), r.geometry) for r in records],
    })


class PolygonServiceHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def _dispatch(self) -> None:
        path = urlparse(self.path).path
        method = self.command
        resource, rid = _parse_path(path)

        if method == "GET" and path == "/":
            return handle_root(self)
        if method == "GET" and resource == "health":
            return handle_health(self)
        if method == "GET" and resource == "stats":
            return handle_stats(self)
        if method == "POST" and path == "/query/point-in-polygon":
            return handle_pip(self)

        if resource == "polygons":
            if method == "GET" and rid is None:
                return handle_list(self)
            if method == "POST" and rid is None:
                return handle_create(self)
            if method == "GET" and rid:
                return handle_get(self, rid)
            if method == "PUT" and rid:
                return handle_update(self, rid)
            if method == "DELETE" and rid:
                return handle_delete(self, rid)

        _json(self, 404, {"error": f"Маршрут не найден: {method} {path}"})

    def _handle(self) -> None:
        try:
            self._dispatch()
        except ValueError as e:
            _json(self, 400, {"error": str(e)})
        except Exception:
            print(f"[ERROR]\n{traceback.format_exc()}")
            _json(self, 500, {"error": "Внутренняя ошибка сервера"})

    do_GET = do_POST = do_PUT = do_DELETE = _handle


def run_server(host: str = "127.0.0.1", port: int = 8080) -> None:
    server = HTTPServer((host, port), PolygonServiceHandler)
    print(f"Listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер остановлен")
        server.server_close()
