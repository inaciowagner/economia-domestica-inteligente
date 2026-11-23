"""Ponto de entrada: python main.py  (servidor de desenvolvimento em http://127.0.0.1:5000)"""
import os

from app import app

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=os.environ.get("FLASK_DEBUG", "0") == "1"
    )

