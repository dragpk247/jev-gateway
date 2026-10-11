import os
import sys
from pathlib import Path

# Ensure root repository is in Python search path
root_dir = str(Path(__file__).parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from server import JevGatewayHandler

# Direct subclass named 'handler' as required by Vercel Serverless Python runtime
class handler(JevGatewayHandler):
    pass
