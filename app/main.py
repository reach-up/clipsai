from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl, Field # Import Field
import uvicorn
import os
import shutil
import uuid
import logging

# Assuming s3_utils are exposed via app/__init__.py or directly accessible
from .s3_utils import download_s3_file, parse_s3_url, upload_file_to_s3
from .config import settings # Import settings

# Imports for ClipsAI components
from clipsai.trailer.trailer import TrailerGenerator
from clipsai.clip.clipfinder import ClipFinder
from clipsai.media.editor import MediaEditor
# Transcriber is used within TrailerGenerator, so not directly needed here.

# --- Logging Configuration ---
# Get log level from settings
numeric_level = getattr(logging, settings.LOG_LEVEL.upper(), None)
if not isinstance(numeric_level, int):
    # Fallback to INFO if LOG_LEVEL is invalid, and log a warning.
    logging.warning(f"Invalid log level: {settings.LOG_LEVEL}. Defaulting to INFO.")
    numeric_level = logging.INFO

logging.basicConfig(
    level=numeric_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(process)d - %(threadName)s - %(message)s"
)
# Set the logger for this specific module
logger = logging.getLogger(__name__)
# Example: Propagate level to other loggers if needed, or configure them separately
# logging.getLogger("clipsai").setLevel(numeric_level)
# logging.getLogger("app.s3_utils").setLevel(numeric_level)


# --- Pydantic Models ---
class TrailerRequest(BaseModel):
    s3_movie_url: str = Field(
        ...,
        description="The S3 URL of the movie file to process (e.g., s3://my-bucket/movies/movie.mp4).",
        example="s3://my-test-bucket-unique/test-movie.mp4"
    )

class BackgroundTaskResponse(BaseModel):
    message: str = Field(
        ...,
        description="Confirmation message indicating the task has been scheduled."
    )
    processing_id: str = Field(
        ...,
        description="A unique ID for tracking the background processing task."
    )
    s3_movie_url: str = Field(
        ...,
        description="The S3 URL of the movie being processed."
    )


# --- Background Task Function ---
async def process_trailer_generation_task(
    s3_url: str,
    output_trailer_base_dir: str, # Base dir for final trailer output
    processing_id: str # To keep temporary files organized
):
    """
    Background task to download a movie from S3, generate a trailer,
    and clean up.
    """
    logger.info(f"[{processing_id}] Background task started for S3 URL: {s3_url}")

    # Temporary directory for this specific task's intermediate files (downloads, trimmed clips)
    base_processing_dir = os.path.join(settings.PROCESSING_TEMP_BASE_DIR, processing_id)
    local_movie_path = None
    final_trailer_path = None

    try:
        # Ensure the base temporary processing directory for this task exists
        os.makedirs(base_processing_dir, exist_ok=True)
        logger.info(f"[{processing_id}] Ensured temporary processing directory exists: {base_processing_dir}")

        # 1. Download S3 file
        logger.info(f"[{processing_id}] Downloading S3 file...")
        # download_s3_file creates its own unique subdirectory within base_processing_dir
        local_movie_path = download_s3_file(s3_url, base_processing_dir)

        if not local_movie_path:
            logger.error(f"[{processing_id}] Failed to download movie from {s3_url}. Exiting task.")
            # Cleanup is handled in finally block
            return

        logger.info(f"[{processing_id}] Movie downloaded to: {local_movie_path}")

        # 2. Instantiate necessary components
        # Consider if these components have internal state that could cause issues in a concurrent environment.
        # For now, instantiating per task is safer.
        # If model loading for Transcriber (within TrailerGenerator) is heavy, this might be slow.
        clip_finder = ClipFinder()
        media_editor = MediaEditor()
        trailer_generator = TrailerGenerator(clip_finder=clip_finder, media_editor=media_editor)

        # 3. Define output path for the trailer
        os.makedirs(output_trailer_base_dir, exist_ok=True) # Ensure final output storage dir exists
        trailer_filename = f"{processing_id}_trailer.mp4"
        final_trailer_path = os.path.join(output_trailer_base_dir, trailer_filename)
        logger.info(f"[{processing_id}] Final trailer will be saved to: {final_trailer_path}")

        # 4. Generate the trailer
        logger.info(f"[{processing_id}] Starting trailer generation...")
        generated_video_file_obj = trailer_generator.generate_basic_trailer(
            source_video_path=local_movie_path,
            output_trailer_path=final_trailer_path,
            # num_clips_to_select=5 # Using default from TrailerGenerator
        )

        if generated_video_file_obj and os.path.exists(final_trailer_path):
            logger.info(f"[{processing_id}] Trailer generated locally: {final_trailer_path}")

            # Define S3 upload parameters
            S3_UPLOAD_BUCKET = settings.S3_UPLOAD_BUCKET
            s3_trailer_key = f"trailers/{processing_id}/{os.path.basename(final_trailer_path)}"

            logger.info(f"[{processing_id}] Attempting to upload trailer to s3://{S3_UPLOAD_BUCKET}/{s3_trailer_key}")
            uploaded_s3_url = upload_file_to_s3(final_trailer_path, S3_UPLOAD_BUCKET, s3_trailer_key)

            if uploaded_s3_url:
                logger.info(f"[{processing_id}] Trailer successfully uploaded to: {uploaded_s3_url}")
                # Clean up local trailer file after successful S3 upload
                try:
                    os.remove(final_trailer_path)
                    logger.info(f"[{processing_id}] Removed local trailer file: {final_trailer_path}")
                except OSError as e_remove:
                    logger.error(f"[{processing_id}] Error removing local trailer file {final_trailer_path}: {e_remove}")
            else:
                logger.error(f"[{processing_id}] Failed to upload trailer to S3. Local file kept at {final_trailer_path}")
        else:
            logger.error(f"[{processing_id}] Trailer generation failed or output file not found. No S3 upload attempted.")

    except Exception as e:
        logger.exception(f"[{processing_id}] An error occurred during background trailer processing for {s3_url}: {e}")
    finally:
        # 5. Cleanup: Remove the entire base_processing_dir for this task
        # This includes the downloaded S3 file and any intermediate clips created by TrailerGenerator.
        # The final trailer in output_trailer_base_dir is kept.
        if os.path.exists(base_processing_dir):
            try:
                shutil.rmtree(base_processing_dir)
                logger.info(f"[{processing_id}] Cleaned up temporary processing directory: {base_processing_dir}")
            except Exception as e:
                logger.error(f"[{processing_id}] Error during cleanup of {base_processing_dir}: {e}")


# --- FastAPI Application ---
app = FastAPI(title="ClipsAI Service", version="0.1.0")

@app.get("/health", summary="Health Check", tags=["General"])
async def health_check():
    """Perform a health check. Returns the current operational status of the service."""
    return {"status": "ok"}


@app.post(
    "/generate-trailer/",
    response_model=BackgroundTaskResponse,
    tags=["Trailer Generation"],
    summary="Schedule Trailer Generation from S3 Movie"
)
async def create_trailer(request: TrailerRequest, background_tasks: BackgroundTasks):
    """
    Accepts an S3 URL for a movie file.

    The endpoint will validate the S3 URL format and then schedule an asynchronous
    background task to:
    1. Download the movie from S3.
    2. Process the movie to generate a trailer (details of generation depend on `TrailerGenerator`).
    3. Upload the generated trailer back to a configured S3 bucket.
    4. Clean up temporary local files.

    The response indicates that the task has been successfully scheduled and includes
    a `processing_id` which can be used (in future implementations) to check the status
    of the generation task.
    """
    logger.info(f"Received trailer generation request for S3 URL: {request.s3_movie_url}")

    parsed_url = parse_s3_url(request.s3_movie_url)
    if not parsed_url:
        logger.error(f"Invalid S3 URL provided: {request.s3_movie_url}")
        raise HTTPException(status_code=400, detail="Invalid S3 URL format.")

    processing_id = str(uuid.uuid4())
    # Define a directory where final trailers will be stored by background tasks.
    output_trailer_storage_dir = settings.OUTPUT_TRAILER_STORAGE_DIR

    logger.info(f"[{processing_id}] Scheduling background task for S3 URL: {request.s3_movie_url}")

    # The background task will handle creating its own specific temporary working directory
    # based on the processing_id, and also cleaning that up.
    # output_trailer_storage_dir is for the *final* output of the task.
    background_tasks.add_task(
        process_trailer_generation_task,
        s3_url=request.s3_movie_url,
        output_trailer_base_dir=output_trailer_storage_dir,
        processing_id=processing_id
    )

    return BackgroundTaskResponse(
        message="Trailer generation task started in background.",
        processing_id=processing_id,
        s3_movie_url=request.s3_movie_url
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
