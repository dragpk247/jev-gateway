import os
import sys
from pathlib import Path

# Ensure root repository is in Python module search path
root_dir = str(Path(__file__).parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from server import JevGatewayHandler

# Vercel Serverless Python entrypoint
# Vercel expects a WSGI/ASGI application or a BaseHTTPRequestHandler subclass named 'handler'
handler = JevGatewayHandler
