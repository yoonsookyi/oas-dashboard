import argparse

from .config import load_config
from .web import AppContext, serve


def main(argv=None):
    parser = argparse.ArgumentParser(description="OAS Admin Lite")
    parser.add_argument("--config", default="", help="path to app.yaml")
    parser.add_argument("--check", action="store_true", help="load config and initialize directories, then exit")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    ctx = AppContext(cfg)
    if args.check:
        print("ok")
        return 0
    serve(ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())