import boto3
import os
import uuid
import logging
from botocore.exceptions import NoCredentialsError, ClientError
from urllib.parse import urlparse

from .config import settings # Import settings

logger = logging.getLogger(__name__)
# Basic logging configuration will be handled by the main application (app/main.py)
# based on settings. This ensures consistency.


def parse_s3_url(s3_url: str) -> tuple[str, str] or None:
    """Parses an S3 URL into bucket and key.

    Args:
        s3_url: The S3 URL (e.g., s3://my-bucket/my/key/file.mp4).

    Returns:
        A tuple (bucket_name, key) or None if parsing fails.
    """
    try:
        parsed = urlparse(s3_url)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip('/'):
            logger.error(f"Invalid S3 URL format: {s3_url}")
            return None
        bucket_name = parsed.netloc
        key = parsed.path.lstrip('/')
        return bucket_name, key
    except Exception:
        logger.exception(f"Error parsing S3 URL {s3_url}")
        return None


def download_s3_file(s3_url: str, local_temp_dir: str = None) -> str or None:
    """Downloads a file from S3 to a local temporary directory.

    Args:
        s3_url: The S3 URL of the file to download.
        local_temp_dir: The base local directory to store downloaded files.
                       A subdirectory with a unique name will be created here.

    Returns:
        The local path to the downloaded file, or None if download fails.
    """
    if local_temp_dir is None:
        local_temp_dir = settings.S3_DOWNLOAD_TEMP_BASE_DIR

    parsed_s3_info = parse_s3_url(s3_url)
    if not parsed_s3_info:
        return None

    bucket_name, key = parsed_s3_info

    # Create a unique directory for this download
    download_instance_dir = os.path.join(local_temp_dir, str(uuid.uuid4()))
    if not os.path.exists(download_instance_dir):
        try:
            os.makedirs(download_instance_dir, exist_ok=True) # exist_ok=True for robustness
        except OSError as e:
            logger.error(f"Failed to create temporary download directory {download_instance_dir}: {e}")
            return None

    original_filename = os.path.basename(key)
    if not original_filename:
        original_filename = f"downloaded_file_{uuid.uuid4().hex}" # Ensure a filename if key ends with /

    local_file_path = os.path.join(download_instance_dir, original_filename)

    s3_client = boto3.client("s3")
    logger.info(f"Attempting to download s3://{bucket_name}/{key} to {local_file_path}")

    try:
        s3_client.download_file(bucket_name, key, local_file_path)
        logger.info(f"Successfully downloaded to {local_file_path}")
        return local_file_path
    except NoCredentialsError:
        logger.error("AWS credentials not found. Configure AWS credentials (e.g., via environment variables or IAM roles).")
        return None
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code")
        if error_code == '404' or error_code == 'NoSuchKey':
            logger.error(f"File not found in S3: s3://{bucket_name}/{key}")
        elif error_code == '403':
             logger.error(f"Access denied (403 Forbidden) for S3 object: s3://{bucket_name}/{key}. Check permissions.")
        else:
            logger.error(f"Error downloading from S3 (s3://{bucket_name}/{key}), error code {error_code}: {e}")
        return None
    except Exception:
        logger.exception(f"An unexpected error occurred during S3 download (s3://{bucket_name}/{key})")
        return None

if __name__ == "__main__":
    # Example Usage (requires AWS credentials configured and a test file in S3)
    # Note: To run this standalone, AWS credentials must be available in the environment.
    # example_s3_url = "s3://your-test-bucket-name/path/to/your/test-movie.mp4"
    # if "YOUR_TEST_S3_URL" in os.environ: # Check if an env var is set for testing
    #    example_s3_url = os.environ["YOUR_TEST_S3_URL"]
    #    logger.info(f"Testing S3 download for URL from env var: {example_s3_url}")
    #    downloaded_path = download_s3_file(example_s3_url)
    #    if downloaded_path:
    #        logger.info(f"Test download successful: {downloaded_path}")
    #        # Remember to clean up the downloaded file/directory after testing
    #        # For instance: import shutil; shutil.rmtree(os.path.dirname(downloaded_path))
    #        try:
    #            import shutil
    #            shutil.rmtree(os.path.dirname(downloaded_path))
    #            logger.info(f"Cleaned up test download directory: {os.path.dirname(downloaded_path)}")
    #        except Exception as e:
    #            logger.error(f"Error cleaning up test download: {e}")
    #    else:
    #        logger.error("Test download failed.")
    # else:
    #    logger.info("Skipping S3 download test in __main__ because YOUR_TEST_S3_URL env var is not set.")
    pass


def upload_file_to_s3(local_file_path: str, bucket_name: str, s3_key: str) -> str or None:
    """Uploads a local file to an S3 bucket.

    Args:
        local_file_path: Path to the local file to upload.
        bucket_name: Name of the S3 bucket.
        s3_key: The desired key (path) in the S3 bucket for the uploaded file.

    Returns:
        The S3 URL of the uploaded file (s3://bucket_name/s3_key), or None if upload fails.
    """
    if not os.path.exists(local_file_path):
        logger.error(f"Local file not found for upload: {local_file_path}")
        return None

    s3_client = boto3.client("s3")
    try:
        logger.info(f"Attempting to upload {local_file_path} to s3://{bucket_name}/{s3_key}")
        s3_client.upload_file(local_file_path, bucket_name, s3_key)
        # Construct the S3 URL for the uploaded file
        uploaded_s3_url = f"s3://{bucket_name}/{s3_key}"
        logger.info(f"Successfully uploaded to {uploaded_s3_url}")
        return uploaded_s3_url
    except NoCredentialsError:
        logger.error(f"AWS credentials not found for S3 upload to bucket {bucket_name}.")
        return None
    except ClientError as e:
        logger.error(f"S3 ClientError during upload to s3://{bucket_name}/{s3_key}: {e}")
        return None
    except Exception:
        logger.exception(f"An unexpected error occurred during S3 upload of {local_file_path} to s3://{bucket_name}/{s3_key}")
        return None
