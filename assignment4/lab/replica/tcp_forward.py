"""Byte-preserving private transport. No HTTP parsing, credentials or model decisions."""
import argparse
import select
import socket
import socketserver

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--upstream',required=True);p.add_argument('--upstream-port',type=int,default=30000);p.add_argument('--port',type=int,default=30000);a=p.parse_args()
class Forward(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection((a.upstream,a.upstream_port),timeout=10) as upstream:
                upstream.settimeout(None)
                peers={self.request:upstream,upstream:self.request}
                while peers:
                    readable,_,_=select.select(list(peers),[],[])
                    for source in readable:
                        data=source.recv(65536)
                        if data:peers[source].sendall(data)
                        else:
                            try:peers[source].shutdown(socket.SHUT_WR)
                            except OSError:pass
                            del peers[source]
        except OSError:
            # The caller sees the connection failure; never manufacture an API result.
            return
class Listener(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True
with Listener(('0.0.0.0',a.port),Forward) as server:server.serve_forever()
