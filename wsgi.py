"""WSGI-Einstieg für gunicorn: `gunicorn wsgi:app`."""
from tatilvakti import create_app

app = create_app()
