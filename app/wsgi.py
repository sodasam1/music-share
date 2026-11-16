"""Production entry point: python3 -m waitress --listen=127.0.0.1:8101 --threads=4 wsgi:app"""
from musicshare import create_app

app = create_app()
