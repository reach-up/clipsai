from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    APP_NAME: str = "ClipsAI Service"
    LOG_LEVEL: str = "INFO"

    # S3 Configuration
    S3_UPLOAD_BUCKET: str = "clipsai-trailer-outputs-placeholder" # Default, should be overridden by env var
    # AWS_ACCESS_KEY_ID: str = None # boto3 handles this via env/roles
    # AWS_SECRET_ACCESS_KEY: str = None # boto3 handles this via env/roles
    # AWS_REGION: str = "us-east-1" # boto3 can also handle this

    # File Paths / Directories
    # Base directory for temporary files created during S3 downloads by s3_utils
    # (download_s3_file creates a UUID subdir within this)
    S3_DOWNLOAD_TEMP_BASE_DIR: str = "/tmp/clipsai_s3_downloads"

    # Base directory for temporary files created during video processing by background tasks
    # (process_trailer_generation_task creates a UUID subdir within this)
    PROCESSING_TEMP_BASE_DIR: str = "/tmp/clipsai_processing"

    # Base directory for storing final trailer outputs locally (before S3 upload, or if S3 upload fails)
    # The background task currently uses this and then uploads from here.
    OUTPUT_TRAILER_STORAGE_DIR: str = "/tmp/clipsai_trailers_output"

    class Config:
        env_file = ".env" # Load .env file if present
        env_file_encoding = "utf-8"
        extra = "ignore" # Ignore extra fields from env if present in .env

settings = Settings()

# Optional: Ensure directories exist at startup
# This can be useful, but also consider if the application has permissions
# or if it's better to let components create them as needed with error handling.
# For this iteration, we'll let the components (like s3_utils and main.py task)
# handle directory creation with os.makedirs(..., exist_ok=True).
# Example:
# os.makedirs(settings.S3_DOWNLOAD_TEMP_BASE_DIR, exist_ok=True)
# os.makedirs(settings.PROCESSING_TEMP_BASE_DIR, exist_ok=True)
# os.makedirs(settings.OUTPUT_TRAILER_STORAGE_DIR, exist_ok=True)
