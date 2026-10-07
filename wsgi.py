"""WSGI-Einstieg für gunicorn: `gunicorn wsgi:app`."""
import logging

from tatilvakti import create_app

# Betrieb: App-Meldungen ab INFO (eingegangene Meldungen, Wartung, Warnungen) über stderr ins
# Journal; Flask gibt sonst erst ab WARNING aus. Die App loggt keine IP-Adressen.
logging.getLogger("tatilvakti").setLevel(logging.INFO)

app = create_app()
