"""Start the official SDK API web demo; no hand/session is created automatically."""
import argparse
from pathlib import Path
import threading
import webbrowser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    from omnihand_server import OmniHandServer, ServerOptions

    options = ServerOptions()
    options.host = "127.0.0.1"
    options.port = args.port
    import omnihand_server
    options.static_file_root = str(Path(omnihand_server.__file__).resolve().parent)
    server = OmniHandServer(options)
    server.start()
    print(f"Official SDK web: http://127.0.0.1:{server.port}/", flush=True)
    if args.open:
        opener = threading.Timer(1.0, webbrowser.open, args=(f"http://127.0.0.1:{args.port}/",))
        opener.daemon = True
        opener.start()
    try:
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
