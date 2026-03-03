import uvicorn
import os
import sys
from app.main import app

if __name__ == "__main__":
    # Get port from env or default to 8000
    port = int(os.environ.get("BACKEND_PORT", 8800))
    host = os.environ.get("BACKEND_HOST", "127.0.0.1")
    
    print(f"Starting backend on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
