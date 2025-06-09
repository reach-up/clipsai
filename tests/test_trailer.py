import pytest
import os
import shutil
import uuid
from unittest import mock
from unittest.mock import MagicMock, patch, call

from clipsai.trailer.trailer import TrailerGenerator
from clipsai.clip.clipfinder import ClipFinder
from clipsai.media.editor import MediaEditor
from clipsai.transcribe.transcriber import Transcriber
from clipsai.transcribe.transcription import Transcription, Utterance
from clipsai.clip.clip import Clip # Ensure Clip is imported
from clipsai.media.video_file import VideoFile
from clipsai.media.temporal_media_file import TemporalMediaFile
from clipsai.filesys.exceptions import FileSystemError as FSError
from clipsai.filesys.manager import FileSystemManager

# Path for patching items within the TrailerGenerator's module
clipsai_trailer_trailer_path = "clipsai.trailer.trailer"

# Dummy data for reuse in tests
dummy_utterances = [Utterance(0, 5, "Hello world"), Utterance(10, 15, "Another clip")]
dummy_transcription = Transcription(dummy_utterances)

# Updated dummy_clips_list with scores for testing sorting
# These are now actual Clip objects, not mocks by default here
scored_clip_low = Clip(start_time=0, end_time=5, start_char=0, end_char=10, score=0.2)
scored_clip_high = Clip(start_time=10, end_time=15, start_char=11, end_char=20, score=0.9)
scored_clip_medium = Clip(start_time=20, end_time=25, start_char=21, end_char=30, score=0.5)
# This list is intentionally not sorted by score
dummy_scored_clips = [scored_clip_low, scored_clip_high, scored_clip_medium]


mock_temporal_file = MagicMock(spec=TemporalMediaFile)
mock_temporal_file.path = "mock_trimmed_clip.mp4"
mock_final_video_file = MagicMock(spec=VideoFile)
mock_final_video_file.path = "mock_output_trailer.mp4"


class TestTrailerGenerator:
    """Test suite for the TrailerGenerator class."""

    def setup_method(self, method):
        """Set up test environment for each test method."""
        self.temp_dir = f"temp_test_trailer_outputs_{uuid.uuid4().hex}"
        os.makedirs(self.temp_dir, exist_ok=True)

        self.mock_clip_finder = MagicMock(spec=ClipFinder)
        self.mock_media_editor = MagicMock(spec=MediaEditor)

        self.trailer_generator = TrailerGenerator(
            clip_finder=self.mock_clip_finder,
            media_editor=self.mock_media_editor
        )

        self.trailer_generator._fs_manager.delete_dir = MagicMock()
        self.trailer_generator._fs_manager.assert_parent_dir_exists = MagicMock()

    def teardown_method(self, method):
        """Clean up test environment after each test method."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def _get_temp_path(self, filename):
        """Helper to get a full path for a temp file within the test's temp_dir."""
        return os.path.join(self.temp_dir, filename)

    @patch(f'{clipsai_trailer_trailer_path}.Transcriber')
    @patch(f'{clipsai_trailer_trailer_path}.VideoFile')
    def test_successful_generation_with_scoring(self, MockVideoFile, MockTranscriber):
        """Test successful trailer generation, considering clip scores."""
        mock_transcriber_instance = MockTranscriber.return_value
        mock_transcriber_instance.transcribe.return_value = dummy_transcription

        mock_source_video_instance = MockVideoFile.return_value
        mock_source_video_instance.assert_exists.return_value = None

        # Use the scored clips, find_clips returns them (e.g. unsorted by score initially)
        self.mock_clip_finder.find_clips.return_value = [scored_clip_low, scored_clip_high, scored_clip_medium]

        # Clips that should be selected due to scoring (high, then medium)
        expected_selected_clips_in_order = [scored_clip_high, scored_clip_medium]

        mock_trimmed_clip_high = MagicMock(spec=TemporalMediaFile, path=self._get_temp_path("clip_high.mp4"))
        mock_trimmed_clip_medium = MagicMock(spec=TemporalMediaFile, path=self._get_temp_path("clip_medium.mp4"))
        # Trim should be called with high score clip first, then medium score clip
        self.mock_media_editor.trim.side_effect = [mock_trimmed_clip_high, mock_trimmed_clip_medium]

        self.mock_media_editor.concatenate.return_value = mock_final_video_file

        source_video_p = "dummy_source.mp4"
        output_trailer_p = self._get_temp_path("final_output_trailer.mp4")

        num_clips_to_select = 2

        with patch(f'{clipsai_trailer_trailer_path}.uuid.uuid4') as mock_uuid, \
             patch(f'{clipsai_trailer_trailer_path}.os.makedirs') as mock_os_makedirs:
            mock_uuid.return_value.hex = "dummyuuid_score_success"
            expected_temp_dir_path = os.path.join("/tmp/clipsai_trailers", f"trailer_{mock_uuid.return_value.hex}")

            def mock_path_join_for_clips(base, filename):
                if base == expected_temp_dir_path and filename.startswith("clip_"):
                    return os.path.normpath(os.path.join(base, filename))
                return os.path.join(base, filename)

            with patch(f'{clipsai_trailer_trailer_path}.os.path.join', side_effect=mock_path_join_for_clips):
                result = self.trailer_generator.generate_basic_trailer(
                    source_video_path=source_video_p,
                    output_trailer_path=output_trailer_p,
                    num_clips_to_select=num_clips_to_select
                )

        assert result == mock_final_video_file
        MockTranscriber.assert_called_once()
        mock_transcriber_instance.transcribe.assert_called_once_with(source_video_p)
        MockVideoFile.assert_called_once_with(source_video_p)
        mock_source_video_instance.assert_exists.assert_called_once()

        self.trailer_generator._fs_manager.assert_parent_dir_exists.assert_called_once()
        args_parent_dir, _ = self.trailer_generator._fs_manager.assert_parent_dir_exists.call_args
        assert isinstance(args_parent_dir[0], VideoFile)
        assert args_parent_dir[0].path == output_trailer_p

        mock_os_makedirs.assert_any_call("/tmp/clipsai_trailers", exist_ok=True)
        mock_os_makedirs.assert_any_call(expected_temp_dir_path, exist_ok=True)

        self.mock_clip_finder.find_clips.assert_called_once_with(dummy_transcription)

        # Assert trim calls based on score (highest scores first)
        # Clip paths for trim are generated as clip_0.mp4, clip_1.mp4, etc. based on the *selected and sorted* order.
        expected_clip_path_0_for_high_score = os.path.normpath(os.path.join(expected_temp_dir_path, "clip_0.mp4"))
        expected_clip_path_1_for_medium_score = os.path.normpath(os.path.join(expected_temp_dir_path, "clip_1.mp4"))

        self.mock_media_editor.trim.assert_has_calls([
            call(media_file=mock_source_video_instance, start_time=expected_selected_clips_in_order[0].start_time, end_time=expected_selected_clips_in_order[0].end_time, trimmed_media_file_path=expected_clip_path_0_for_high_score, overwrite=True),
            call(media_file=mock_source_video_instance, start_time=expected_selected_clips_in_order[1].start_time, end_time=expected_selected_clips_in_order[1].end_time, trimmed_media_file_path=expected_clip_path_1_for_medium_score, overwrite=True),
        ])

        self.mock_media_editor.concatenate.assert_called_once_with(
            media_files=[mock_trimmed_clip_high, mock_trimmed_clip_medium], # These correspond to the high and medium score clips
            concatenated_media_file_path=output_trailer_p,
            overwrite=True
        )
        self.trailer_generator._fs_manager.delete_dir.assert_called_once_with(expected_temp_dir_path)

    @patch(f'{clipsai_trailer_trailer_path}.Transcriber')
    @patch(f'{clipsai_trailer_trailer_path}.VideoFile')
    @patch(f'{clipsai_trailer_trailer_path}.uuid.uuid4')
    @patch(f'{clipsai_trailer_trailer_path}.os.makedirs')
    def test_selection_respects_score_sorting(self, mock_os_makedirs, mock_uuid, MockVideoFile, MockTranscriber):
        """Test that clip selection respects score-based sorting."""
        mock_uuid.return_value.hex = "score_sort_uuid"
        mock_transcriber_instance = MockTranscriber.return_value
        mock_transcriber_instance.transcribe.return_value = dummy_transcription
        mock_source_video_instance = MockVideoFile.return_value
        mock_source_video_instance.assert_exists.return_value = None

        # Clips provided by find_clips are NOT sorted by score initially
        clips_from_finder = [scored_clip_low, scored_clip_high, scored_clip_medium] # scores: 0.2, 0.9, 0.5
        self.mock_clip_finder.find_clips.return_value = clips_from_finder

        # Expected order after sorting: high, medium, low
        expected_processing_order = [scored_clip_high, scored_clip_medium, scored_clip_low]

        # If we select 1, it should be scored_clip_high
        # If we select 2, it should be scored_clip_high, then scored_clip_medium

        num_clips_to_select = 2
        expected_selected_for_trim = expected_processing_order[:num_clips_to_select]

        # Mock trim to return distinct objects for easier assertion of order
        mock_trimmed_clips_in_order = [MagicMock(spec=TemporalMediaFile) for _ in expected_selected_for_trim]
        self.mock_media_editor.trim.side_effect = mock_trimmed_clips_in_order
        self.mock_media_editor.concatenate.return_value = mock_final_video_file

        expected_temp_dir_path = os.path.join("/tmp/clipsai_trailers", f"trailer_{mock_uuid.return_value.hex}")
        def mock_path_join_for_clips(base, filename):
            if base == expected_temp_dir_path and filename.startswith("clip_"):
                return os.path.normpath(os.path.join(base, filename))
            return os.path.join(base, filename)

        with patch(f'{clipsai_trailer_trailer_path}.os.path.join', side_effect=mock_path_join_for_clips):
            self.trailer_generator.generate_basic_trailer(
                "dummy_source.mp4",
                self._get_temp_path("score_sorted_output.mp4"),
                num_clips_to_select=num_clips_to_select
            )

        assert self.mock_media_editor.trim.call_count == num_clips_to_select

        trim_calls = self.mock_media_editor.trim.call_args_list
        for i in range(num_clips_to_select):
            actual_call_args = trim_calls[i][1] # Get kwargs of the call
            expected_clip_for_trim = expected_selected_for_trim[i]

            assert actual_call_args['start_time'] == expected_clip_for_trim.start_time
            assert actual_call_args['end_time'] == expected_clip_for_trim.end_time
            # Path for trimmed clip is generated as clip_0, clip_1 based on selected order
            assert actual_call_args['trimmed_media_file_path'] == os.path.normpath(os.path.join(expected_temp_dir_path, f"clip_{i}.mp4"))

        self.mock_media_editor.concatenate.assert_called_once_with(
            media_files=mock_trimmed_clips_in_order, # These are the mocks returned by trim.side_effect
            concatenated_media_file_path=self._get_temp_path("score_sorted_output.mp4"),
            overwrite=True
        )
        self.trailer_generator._fs_manager.delete_dir.assert_called_once_with(expected_temp_dir_path)


    @patch(f'{clipsai_trailer_trailer_path}.Transcriber')
    @patch(f'{clipsai_trailer_trailer_path}.VideoFile')
    @patch(f'{clipsai_trailer_trailer_path}.uuid.uuid4')
    @patch(f'{clipsai_trailer_trailer_path}.os.makedirs')
    def test_no_clips_found(self, mock_os_makedirs, mock_uuid, MockVideoFile, MockTranscriber):
        mock_uuid.return_value.hex = "no_clips_uuid"
        mock_transcriber_instance = MockTranscriber.return_value
        mock_transcriber_instance.transcribe.return_value = dummy_transcription
        MockVideoFile.return_value.assert_exists.return_value = None
        self.mock_clip_finder.find_clips.return_value = []

        result = self.trailer_generator.generate_basic_trailer("s.mp4", "o.mp4")

        assert result is None
        self.mock_media_editor.trim.assert_not_called()
        self.mock_media_editor.concatenate.assert_not_called()
        expected_temp_dir_path = os.path.join("/tmp/clipsai_trailers", f"trailer_{mock_uuid.return_value.hex}")
        self.trailer_generator._fs_manager.delete_dir.assert_called_once_with(expected_temp_dir_path)

    @patch(f'{clipsai_trailer_trailer_path}.Transcriber')
    @patch(f'{clipsai_trailer_trailer_path}.VideoFile')
    @patch(f'{clipsai_trailer_trailer_path}.uuid.uuid4')
    @patch(f'{clipsai_trailer_trailer_path}.os.makedirs')
    def test_trim_fails_for_all_clips(self, mock_os_makedirs, mock_uuid, MockVideoFile, MockTranscriber):
        mock_uuid.return_value.hex = "trim_fails_uuid"
        mock_transcriber_instance = MockTranscriber.return_value
        mock_transcriber_instance.transcribe.return_value = dummy_transcription
        MockVideoFile.return_value.assert_exists.return_value = None
        self.mock_clip_finder.find_clips.return_value = dummy_scored_clips[:2] # Use scored clips
        self.mock_media_editor.trim.return_value = None # All trims fail

        result = self.trailer_generator.generate_basic_trailer("s.mp4", "o.mp4", num_clips_to_select=2)

        assert result is None
        assert self.mock_media_editor.trim.call_count == 2
        self.mock_media_editor.concatenate.assert_not_called()
        expected_temp_dir_path = os.path.join("/tmp/clipsai_trailers", f"trailer_{mock_uuid.return_value.hex}")
        self.trailer_generator._fs_manager.delete_dir.assert_called_once_with(expected_temp_dir_path)

    @patch(f'{clipsai_trailer_trailer_path}.Transcriber')
    @patch(f'{clipsai_trailer_trailer_path}.VideoFile')
    @patch(f'{clipsai_trailer_trailer_path}.uuid.uuid4')
    @patch(f'{clipsai_trailer_trailer_path}.os.makedirs')
    def test_concatenation_fails(self, mock_os_makedirs, mock_uuid, MockVideoFile, MockTranscriber):
        mock_uuid.return_value.hex = "concat_fails_uuid"
        mock_transcriber_instance = MockTranscriber.return_value
        mock_transcriber_instance.transcribe.return_value = dummy_transcription
        MockVideoFile.return_value.assert_exists.return_value = None
        self.mock_clip_finder.find_clips.return_value = dummy_scored_clips[:1] # Use scored clips
        self.mock_media_editor.trim.return_value = MagicMock(spec=TemporalMediaFile, path="trimmed.mp4")
        self.mock_media_editor.concatenate.return_value = None # Concatenation fails

        result = self.trailer_generator.generate_basic_trailer("s.mp4", "o.mp4", num_clips_to_select=1)

        assert result is None
        self.mock_media_editor.trim.assert_called_once()
        self.mock_media_editor.concatenate.assert_called_once()
        expected_temp_dir_path = os.path.join("/tmp/clipsai_trailers", f"trailer_{mock_uuid.return_value.hex}")
        self.trailer_generator._fs_manager.delete_dir.assert_called_once_with(expected_temp_dir_path)

    @patch(f'{clipsai_trailer_trailer_path}.Transcriber')
    @patch(f'{clipsai_trailer_trailer_path}.VideoFile')
    @patch(f'{clipsai_trailer_trailer_path}.uuid.uuid4')
    @patch(f'{clipsai_trailer_trailer_path}.os.makedirs')
    def test_transcription_fails(self, mock_os_makedirs, mock_uuid, MockVideoFile, MockTranscriber):
        mock_uuid.return_value.hex = "trans_fails_uuid"
        mock_transcriber_instance = MockTranscriber.return_value
        mock_transcriber_instance.transcribe.return_value = None # Transcription fails
        MockVideoFile.return_value.assert_exists.return_value = None

        result = self.trailer_generator.generate_basic_trailer("s.mp4", "o.mp4")

        assert result is None
        self.mock_clip_finder.find_clips.assert_not_called()
        self.mock_media_editor.trim.assert_not_called()
        self.mock_media_editor.concatenate.assert_not_called()
        expected_temp_dir_path = os.path.join("/tmp/clipsai_trailers", f"trailer_{mock_uuid.return_value.hex}")
        self.trailer_generator._fs_manager.delete_dir.assert_called_once_with(expected_temp_dir_path)


    @patch(f'{clipsai_trailer_trailer_path}.VideoFile')
    def test_source_video_does_not_exist(self, MockVideoFile):
        mock_source_video_instance = MockVideoFile.return_value
        mock_source_video_instance.assert_exists.side_effect = FSError("File not found")

        result = self.trailer_generator.generate_basic_trailer("non_existent.mp4", "o.mp4")

        assert result is None
        MockVideoFile.assert_called_once_with("non_existent.mp4")
        mock_source_video_instance.assert_exists.assert_called_once()
        self.mock_clip_finder.find_clips.assert_not_called()
        self.trailer_generator._fs_manager.delete_dir.assert_not_called()

    @patch(f'{clipsai_trailer_trailer_path}.Transcriber')
    @patch(f'{clipsai_trailer_trailer_path}.VideoFile')
    @patch(f'{clipsai_trailer_trailer_path}.uuid.uuid4')
    @patch(f'{clipsai_trailer_trailer_path}.os.makedirs')
    def test_temp_dir_cleanup_on_error_during_concat(self, mock_os_makedirs, mock_uuid, MockVideoFile, MockTranscriber):
        mock_uuid.return_value.hex = "cleanup_test_uuid"
        mock_transcriber_instance = MockTranscriber.return_value
        mock_transcriber_instance.transcribe.return_value = dummy_transcription
        MockVideoFile.return_value.assert_exists.return_value = None
        self.mock_clip_finder.find_clips.return_value = dummy_scored_clips[:1] # Use scored clips
        self.mock_media_editor.trim.return_value = MagicMock(spec=TemporalMediaFile, path="trimmed.mp4")
        self.mock_media_editor.concatenate.side_effect = Exception("Simulated concat error")

        result = self.trailer_generator.generate_basic_trailer("s.mp4", "o.mp4", num_clips_to_select=1)

        assert result is None
        self.mock_media_editor.concatenate.assert_called_once()
        expected_temp_dir_path = os.path.join("/tmp/clipsai_trailers", f"trailer_{mock_uuid.return_value.hex}")
        self.trailer_generator._fs_manager.delete_dir.assert_called_once_with(expected_temp_dir_path)
