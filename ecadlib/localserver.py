"""Local HTTP servers (the web UI, the MCP server over HTTP, the Nexar sign-in's redirect listener) bind without a
name lookup.

http.server.HTTPServer.server_bind asks socket.getfqdn() for the bound address's host name, only to keep it as
server_name, which ecadlib never reads. On macOS that reverse lookup of 127.0.0.1 took 14.8 s (GitHub's macOS runner,
2026-11-22): the web UI did not answer for that long, and a Nexar sign-in started, signed out and started again found
its own port still taken. Python's http.server never needs the name."""
import socketserver


class NoLookupBind:
    """Mix in before HTTPServer / ThreadingHTTPServer: bind as TCPServer does, server_name = the address as given."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port
