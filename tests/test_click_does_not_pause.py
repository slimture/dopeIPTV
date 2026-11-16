"""A single click on the picture brings the controls up and pauses nothing;
the centre button pauses (the maintainer: only the pause button should)."""
from dopeiptv.media.embedded import EmbeddedPlayer


class _Fake:
    current_url = "http://example.invalid/live.ts"

    def __init__(self) -> None:
        self.paused = 0
        self.revealed = 0

    def toggle_pause(self) -> None:
        self.paused += 1

    def _reveal_center(self) -> None:
        self.revealed += 1


def test_a_click_on_the_picture_only_shows_the_controls():
    f = _Fake()
    EmbeddedPlayer._video_single_click(f)
    assert f.paused == 0 and f.revealed == 1


def test_nothing_happens_with_nothing_playing():
    f = _Fake()
    f.current_url = None
    EmbeddedPlayer._video_single_click(f)
    assert f.paused == 0 and f.revealed == 0
