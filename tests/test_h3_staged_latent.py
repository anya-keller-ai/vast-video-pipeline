import sys
import types

import pytest
import torch

folder_paths_stub = types.ModuleType("folder_paths")
folder_paths_stub.get_output_directory = lambda: "/tmp"
sys.modules.setdefault("folder_paths", folder_paths_stub)

from postprocess.h3_staged_latent import _nested_parts  # noqa: E402


class FakeNestedLatent:
    is_nested = True

    def __init__(self, video: torch.Tensor, audio: torch.Tensor) -> None:
        self._parts = (video, audio)

    def unbind(self) -> tuple[torch.Tensor, torch.Tensor]:
        return self._parts


def test_nested_parts_extracts_video_and_audio_without_contiguous() -> None:
    video = torch.zeros((1, 24, 2, 3, 4))
    audio = torch.zeros((1, 32, 2, 7))

    actual_video, actual_audio = _nested_parts(
        {"samples": FakeNestedLatent(video, audio)}
    )

    assert torch.equal(actual_video, video)
    assert torch.equal(actual_audio, audio)
    assert actual_video.device.type == "cpu"
    assert actual_audio.device.type == "cpu"


def test_nested_parts_rejects_non_nested_samples() -> None:
    with pytest.raises(ValueError, match="nested video/audio"):
        _nested_parts({"samples": torch.zeros((1, 2, 3))})


def test_nested_parts_requires_exactly_two_parts() -> None:
    class ThreePartNestedLatent:
        is_nested = True

        @staticmethod
        def unbind() -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
            tensor = torch.zeros((1,))
            return tensor, tensor, tensor

    with pytest.raises(ValueError, match="Expected two H3 latent parts"):
        _nested_parts({"samples": ThreePartNestedLatent()})
