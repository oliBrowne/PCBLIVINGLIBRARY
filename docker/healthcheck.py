"""Container health check: the team server answers /api/session on 127.0.0.1:8765, over HTTPS (own certificate)
or plain HTTP (behind Caddy). Exit 0 when it answers, 1 otherwise. Standard library only."""
import http.client, ssl, sys


def answers(conn):
    try:
        conn.request("GET", "/api/session", headers={"Host": "127.0.0.1"})
        return conn.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()


ctx = ssl.create_default_context()
ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE        # loopback probe of our own listener only
ok = answers(http.client.HTTPSConnection("127.0.0.1", 8765, timeout=4, context=ctx)) or \
    answers(http.client.HTTPConnection("127.0.0.1", 8765, timeout=4))
sys.exit(0 if ok else 1)
