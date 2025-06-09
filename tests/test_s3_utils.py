import pytest
import os
from unittest.mock import patch, MagicMock
from botocore.exceptions import NoCredentialsError, ClientError

from app.s3_utils import parse_s3_url, download_s3_file, upload_file_to_s3
from app.config import settings # To verify default path usage if needed

# --- Tests for parse_s3_url ---
def test_parse_s3_url_valid():
    assert parse_s3_url("s3://my-bucket/my/key/file.mp4") == ("my-bucket", "my/key/file.mp4")
    assert parse_s3_url("s3://another-bucket/a_simple_key.txt") == ("another-bucket", "a_simple_key.txt")

def test_parse_s3_url_invalid_scheme():
    assert parse_s3_url("http://my-bucket/my/key/file.mp4") is None

def test_parse_s3_url_no_bucket():
    assert parse_s3_url("s3:///my/key/file.mp4") is None # urlparse treats empty netloc as path

def test_parse_s3_url_no_key():
    assert parse_s3_url("s3://my-bucket/") is None
    assert parse_s3_url("s3://my-bucket") is None # Technically no path

def test_parse_s3_url_empty_string():
    assert parse_s3_url("") is None

def test_parse_s3_url_malformed():
    assert parse_s3_url("s3:/my-bucket/key") is None # Malformed, missing //


# --- Tests for download_s3_file ---
@patch("app.s3_utils.boto3.client")
@patch("app.s3_utils.os.makedirs")
@patch("app.s3_utils.os.path.exists") # To control flow for directory creation
@patch("app.s3_utils.uuid.uuid4") # To make temp download path predictable
def test_download_s3_file_success(mock_uuid, mock_os_path_exists, mock_os_makedirs, mock_boto_client):
    mock_s3_client_instance = mock_boto_client.return_value
    mock_uuid.return_value.hex = "testuuid" # Used in unique sub-directory creation by s3_utils

    # Simulate that the unique download instance directory doesn't exist initially
    mock_os_path_exists.return_value = False

    # Expected local path construction
    # download_s3_file creates a UUID subdir inside local_temp_dir
    # local_temp_dir defaults to settings.S3_DOWNLOAD_TEMP_BASE_DIR
    base_download_dir = settings.S3_DOWNLOAD_TEMP_BASE_DIR
    instance_download_dir = os.path.join(base_download_dir, mock_uuid.return_value.hex)
    expected_local_path = os.path.join(instance_download_dir, "key.mp4")

    result_path = download_s3_file("s3://test-bucket/key.mp4")

    assert result_path == expected_local_path
    mock_boto_client.assert_called_once_with("s3")
    mock_s3_client_instance.download_file.assert_called_once_with("test-bucket", "key.mp4", expected_local_path)
    mock_os_makedirs.assert_called_once_with(instance_download_dir, exist_ok=True)


@patch("app.s3_utils.boto3.client")
def test_download_s3_file_no_credentials(mock_boto_client):
    mock_s3_client_instance = mock_boto_client.return_value
    mock_s3_client_instance.download_file.side_effect = NoCredentialsError()

    result_path = download_s3_file("s3://test-bucket/key.mp4", "/test/temp")
    assert result_path is None
    mock_boto_client.assert_called_once_with("s3")

@patch("app.s3_utils.boto3.client")
def test_download_s3_file_not_found(mock_boto_client):
    mock_s3_client_instance = mock_boto_client.return_value
    client_error_response = {'Error': {'Code': '404', 'Message': 'Not Found'}}
    mock_s3_client_instance.download_file.side_effect = ClientError(client_error_response, "GetObject")

    result_path = download_s3_file("s3://test-bucket/nonexistentkey.mp4", "/test/temp")
    assert result_path is None
    mock_boto_client.assert_called_once_with("s3")


# --- Tests for upload_file_to_s3 ---
@patch("app.s3_utils.boto3.client")
@patch("app.s3_utils.os.path.exists", return_value=True) # Assume local file exists
def test_upload_s3_file_success(mock_os_path_exists, mock_boto_client):
    mock_s3_client_instance = mock_boto_client.return_value

    local_path = "/local/test/file.mp4"
    bucket = "upload-bucket"
    key = "destination/trailer.mp4"
    expected_s3_url = f"s3://{bucket}/{key}"

    result_url = upload_file_to_s3(local_path, bucket, key)

    assert result_url == expected_s3_url
    mock_os_path_exists.assert_called_once_with(local_path)
    mock_boto_client.assert_called_once_with("s3")
    mock_s3_client_instance.upload_file.assert_called_once_with(local_path, bucket, key)

@patch("app.s3_utils.os.path.exists", return_value=False) # Simulate local file does NOT exist
def test_upload_s3_file_local_file_not_exists(mock_os_path_exists):
    result_url = upload_file_to_s3("/local/nonexistent/file.mp4", "bucket", "key")
    assert result_url is None
    mock_os_path_exists.assert_called_once_with("/local/nonexistent/file.mp4")

@patch("app.s3_utils.boto3.client")
@patch("app.s3_utils.os.path.exists", return_value=True)
def test_upload_s3_file_no_credentials(mock_os_path_exists, mock_boto_client):
    mock_s3_client_instance = mock_boto_client.return_value
    mock_s3_client_instance.upload_file.side_effect = NoCredentialsError()

    result_url = upload_file_to_s3("/local/file.mp4", "bucket", "key")
    assert result_url is None
    mock_boto_client.assert_called_once_with("s3")

@patch("app.s3_utils.boto3.client")
@patch("app.s3_utils.os.path.exists", return_value=True)
def test_upload_s3_file_client_error(mock_os_path_exists, mock_boto_client):
    mock_s3_client_instance = mock_boto_client.return_value
    client_error_response = {'Error': {'Code': 'AccessDenied', 'Message': 'Access Denied'}}
    mock_s3_client_instance.upload_file.side_effect = ClientError(client_error_response, "PutObject")

    result_url = upload_file_to_s3("/local/file.mp4", "bucket", "key")
    assert result_url is None
    mock_boto_client.assert_called_once_with("s3")
