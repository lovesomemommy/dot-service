import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.server import reset_repository, run_server


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Сервис полигонов и point-in-polygon (HTTP + веб-карта)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Адрес привязки (по умолчанию 127.0.0.1; env HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8080")),
        help="Порт (по умолчанию 8080; env PORT)",
    )
    parser.add_argument(
        "--cell-size",
        type=float,
        default=float(os.environ.get("CELL_SIZE", "1.0")),
        dest="cell_size",
        metavar="SIZE",
        help="Размер ячейки GridIndex (env CELL_SIZE)",
    )
    args = parser.parse_args()

    reset_repository(cell_size=args.cell_size)

    url = f"http://{args.host}:{args.port}"
    print(f"Откройте в браузере: {url}")
    try:
        run_server(host=args.host, port=args.port)
    except OSError as e:
        if getattr(e, "errno", None) == 48:
            print(
                f"\nПорт {args.port} уже занят. Пример:\n"
                f"  PORT=9090 python3 main.py\n"
                f"  python3 main.py --port 9090",
                file=sys.stderr,
            )
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
