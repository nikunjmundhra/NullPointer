"""Serve the project root so dashboard/index.html can fetch ../data/*.csv."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    address = ("127.0.0.1", 8000)
    print("AirLens dashboard: http://127.0.0.1:8000/dashboard/")
    print("Press Ctrl+C to stop the server.")
    ThreadingHTTPServer(address, SimpleHTTPRequestHandler).serve_forever()
