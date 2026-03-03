#!/bin/bash
set -e

# Build the backend into a standalone directory
pyinstaller --onedir \
  --name run_app \
  --add-data "../index-files-template.md:." \
  --collect-all docling \
  --collect-all easyocr \
  --hidden-import uvicorn \
  --hidden-import fastapi \
  --hidden-import pydantic_settings \
  --hidden-import email.mime.multipart \
  --hidden-import email.mime.text \
  --hidden-import email.mime.base \
  run_app.py

echo "Backend build completed in dist/run_app"
