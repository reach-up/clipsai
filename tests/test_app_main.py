import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, call # Import call

from app.main import app # Assuming your FastAPI app instance is named 'app'
# Import the function that will be called by background_tasks.add_task
from app.main import process_trailer_generation_task

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

@patch("app.main.BackgroundTasks.add_task") # Patch where BackgroundTasks is used
@patch("app.main.parse_s3_url") # Patch where parse_s3_url is used
def test_generate_trailer_success(mock_parse_s3_url, mock_add_task):
    # Configure mocks
    mock_parse_s3_url.return_value = ("valid-bucket", "movie.mp4") # Simulate valid S3 URL

    # Make the request
    s3_url = "s3://valid-bucket/movie.mp4"
    response = client.post("/generate-trailer/", json={"s3_movie_url": s3_url})

    # Assertions for the response
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["message"] == "Trailer generation task started in background."
    assert "processing_id" in response_json
    assert response_json["s3_movie_url"] == s3_url

    # Assertions for mocks
    mock_parse_s3_url.assert_called_once_with(s3_url)

    # Check that add_task was called once
    mock_add_task.assert_called_once()
    # Check the arguments of the add_task call
    # call_args is a tuple (args, kwargs)
    # We are interested in the first positional argument, which is the task function
    args, kwargs = mock_add_task.call_args
    assert args[0] == process_trailer_generation_task # Check if the correct function was scheduled
    assert kwargs.get("s3_url") == s3_url # Check a named argument
    assert "processing_id" in kwargs # Check if processing_id was passed
    assert "output_trailer_base_dir" in kwargs # Check if output_trailer_base_dir was passed


@patch("app.main.parse_s3_url") # Patch where parse_s3_url is used
def test_generate_trailer_invalid_s3_url(mock_parse_s3_url):
    # Configure mock
    mock_parse_s3_url.return_value = None # Simulate invalid S3 URL

    # Make the request
    s3_url = "invalid-url"
    response = client.post("/generate-trailer/", json={"s3_movie_url": s3_url})

    # Assertions
    assert response.status_code == 400
    response_json = response.json()
    assert "Invalid S3 URL format" in response_json["detail"]
    mock_parse_s3_url.assert_called_once_with(s3_url)
