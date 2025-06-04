import os
import uuid
import logging

from clipsai.clip.clipfinder import ClipFinder
from clipsai.media.editor import MediaEditor
from clipsai.media.video_file import VideoFile
from clipsai.media.temporal_media_file import TemporalMediaFile
from clipsai.filesys.manager import FileSystemManager
from clipsai.transcribe.transcriber import Transcriber
from clipsai.transcribe.transcription import Transcription
from clipsai.clip.clip import Clip


class TrailerGenerator:
    """
    Generates trailers from video content using provided clips or by finding them.
    """

    def __init__(self, clip_finder: ClipFinder, media_editor: MediaEditor):
        """
        Initializes the TrailerGenerator.

        Parameters
        ----------
        clip_finder: ClipFinder
            An instance of ClipFinder to find relevant clips in a video.
        media_editor: MediaEditor
            An instance of MediaEditor to perform video editing tasks.
        """
        if not isinstance(clip_finder, ClipFinder):
            raise TypeError("clip_finder must be an instance of ClipFinder.")
        if not isinstance(media_editor, MediaEditor):
            raise TypeError("media_editor must be an instance of MediaEditor.")

        self._clip_finder = clip_finder
        self._media_editor = media_editor
        self._fs_manager = FileSystemManager()
        # Logger will be configured by the application's main entry point (e.g., app/main.py)
        # This ensures consistent logging levels and formatting.
        self._logger = logging.getLogger(__name__)

    def generate_basic_trailer(
        self,
        source_video_path: str,
        output_trailer_path: str,
        num_clips_to_select: int = 5,
    ) -> VideoFile or None:
        """
        Generates a basic trailer by transcribing a source video, finding suitable
        clips, trimming them, and concatenating them.

        Parameters
        ----------
        source_video_path : str
            The absolute path to the source video file.
        output_trailer_path : str
            The absolute path where the generated trailer video will be saved.
        num_clips_to_select : int, optional
            The number of top clips to select for the trailer. If 0 or negative,
            all found clips will be used. Defaults to 5.

        Returns
        -------
        VideoFile or None
            A VideoFile object representing the generated trailer if successful,
            otherwise None.
        """
        self._logger.info(f"Starting basic trailer generation for: {source_video_path}")

        # 1. Initial Validation
        try:
            source_video_file = VideoFile(source_video_path)
            source_video_file.assert_exists()
        except Exception as e:
            self._logger.error(f"Invalid source video path: {source_video_path}. Error: {e}")
            return None

        # For output_trailer_path, MediaEditor.concatenate will handle overwrite.
        # We should ensure parent directory exists for output_trailer_path.
        try:
            self._fs_manager.assert_parent_dir_exists(VideoFile(output_trailer_path))
        except Exception as e:
            self._logger.error(f"Invalid output trailer path: {output_trailer_path}. Error: {e}")
            return None

        # Create a unique temporary directory for trimmed clips
        # Using a sub-directory within a general temp location for better organization
        # Example: /tmp/clipsai_trailers/trailer_uuid
        base_temp_dir = "/tmp/clipsai_trailers" # Or use tempfile.gettempdir()
        os.makedirs(base_temp_dir, exist_ok=True) # Ensure base temp dir exists
        temp_dir_name = f"trailer_{uuid.uuid4().hex}"
        temp_dir_path = os.path.join(base_temp_dir, temp_dir_name)

        try:
            os.makedirs(temp_dir_path, exist_ok=True)
            self._logger.debug(f"Created temporary directory for clips: {temp_dir_path}")

            # 2. Transcription
            self._logger.info("Transcribing video...")
            transcriber = Transcriber()
            transcription = transcriber.transcribe(source_video_path)
            if transcription is None:
                self._logger.error("Transcription failed.")
                return None
            if not transcription.utterances:
                self._logger.warning("No utterances found in transcription.")
                # Depending on clipfinder, this might still yield clips (e.g. silence based)
                # For now, proceed and let clipfinder handle it.
            self._logger.info("Transcription successful.")

            # 3. Clip Finding
            self._logger.info("Finding clips...")
            clips = self._clip_finder.find_clips(transcription)
            if not clips:
                self._logger.warning("No clips found by ClipFinder.")
                return None
            self._logger.info(f"Found {len(clips)} clips initially.")

            # Sort clips by score in descending order (higher score is better)
            clips.sort(key=lambda clip: clip.score, reverse=True)
            self._logger.info(
                f"Sorted {len(clips)} clips by score. "
                f"Top score: {clips[0].score if clips else 'N/A'}, "
                f"Lowest score: {clips[-1].score if clips else 'N/A'}"
            )

            # 4. Clip Selection (now acts on sorted clips)
            selected_clips = []
            if num_clips_to_select > 0:
                # Ensure we don't try to select more clips than available
                actual_num_to_select = min(num_clips_to_select, len(clips))
                selected_clips = clips[:actual_num_to_select]
                self._logger.info(f"Selected top {len(selected_clips)} clips for the trailer (max requested: {num_clips_to_select}).")
            else:
                selected_clips = clips # Use all clips if num_clips_to_select is not positive
                self._logger.info(f"Selected all {len(selected_clips)} found (and sorted) clips for the trailer.")

            if not selected_clips:
                self._logger.warning("No clips selected for trailer.")
                return None

            # 5. Trimming Clips
            self._logger.info("Trimming selected clips...")
            trimmed_clip_files: list[TemporalMediaFile] = []
            for i, clip_data in enumerate(selected_clips):
                if not isinstance(clip_data, Clip):
                    self._logger.warning(f"Item {i} in selected_clips is not a Clip object: {type(clip_data)}. Skipping.")
                    continue

                temp_clip_path = os.path.join(temp_dir_path, f"clip_{i}.mp4")
                self._logger.debug(f"Trimming clip {i}: start={clip_data.start_time}, end={clip_data.end_time} to {temp_clip_path}")

                trimmed_file = self._media_editor.trim(
                    media_file=source_video_file, # Important: pass the VideoFile object
                    start_time=clip_data.start_time,
                    end_time=clip_data.end_time,
                    trimmed_media_file_path=temp_clip_path,
                    overwrite=True # Overwrite if somehow exists
                )
                if trimmed_file and isinstance(trimmed_file, TemporalMediaFile):
                    trimmed_clip_files.append(trimmed_file)
                    self._logger.debug(f"Successfully trimmed clip {i} to {temp_clip_path}")
                else:
                    self._logger.error(f"Failed to trim clip {i} (start={clip_data.start_time}, end={clip_data.end_time}).")

            # 6. Concatenation
            if not trimmed_clip_files:
                self._logger.warning("No clips were successfully trimmed. Cannot generate trailer.")
                return None

            self._logger.info(f"Concatenating {len(trimmed_clip_files)} trimmed clips to {output_trailer_path}...")
            final_trailer_video = self._media_editor.concatenate(
                media_files=trimmed_clip_files,
                concatenated_media_file_path=output_trailer_path,
                overwrite=True
            )

            if final_trailer_video and isinstance(final_trailer_video, VideoFile):
                self._logger.info(f"Trailer generated successfully: {output_trailer_path}")
                return final_trailer_video
            else:
                self._logger.error("Failed to concatenate trimmed clips.")
                return None

        except Exception as e:
            self._logger.error(f"An unexpected error occurred during trailer generation: {e}", exc_info=True)
            return None
        finally:
            # 7. Cleanup
            if os.path.exists(temp_dir_path):
                try:
                    self._fs_manager.delete_dir(temp_dir_path)
                    self._logger.info(f"Successfully deleted temporary directory: {temp_dir_path}")
                except Exception as e:
                    self._logger.error(f"Failed to delete temporary directory {temp_dir_path}: {e}")
