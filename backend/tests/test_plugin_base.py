from pathlib import Path

from app.plugins.base import PluginContext, tail_text_file


def test_tail_text_file_caps_lines_and_resumes(tmp_path):
    path: Path = tmp_path / "test.log"
    path.write_text("".join(f"line{i}\n" for i in range(10)))

    first = tail_text_file(path, 0, None, max_lines=4)
    assert first.lines == ["line0\n", "line1\n", "line2\n", "line3\n"]
    assert first.more_available is True

    second = tail_text_file(path, first.offset, first.inode, max_lines=4)
    assert second.lines == ["line4\n", "line5\n", "line6\n", "line7\n"]
    assert second.more_available is True

    third = tail_text_file(path, second.offset, second.inode, max_lines=4)
    assert third.lines == ["line8\n", "line9\n"]
    assert third.more_available is False


def test_tail_text_file_picks_up_appended_content(tmp_path):
    path: Path = tmp_path / "test.log"
    path.write_text("a\nb\n")

    result = tail_text_file(path, 0, None)
    assert result.lines == ["a\n", "b\n"]
    assert result.more_available is False

    with path.open("a") as handle:
        handle.write("c\n")

    followup = tail_text_file(path, result.offset, result.inode)
    assert followup.lines == ["c\n"]


def test_tail_text_file_resets_offset_on_truncation(tmp_path):
    path: Path = tmp_path / "test.log"
    path.write_text("a\nb\nc\n")
    first = tail_text_file(path, 0, None)

    path.write_text("x\n")
    second = tail_text_file(path, first.offset, first.inode)
    assert second.lines == ["x\n"]


def test_tail_text_file_offset_alone_misses_copytruncate_after_regrowth(tmp_path):
    # Demonstrates the gap `last_size` closes: with a capped read, `offset`
    # lags behind the real file size. If a copytruncate rotation is followed
    # by enough new writes to push the file size back past that lagging
    # offset before the next call, comparing only to `offset` misses it.
    path: Path = tmp_path / "test.log"
    path.write_text("".join(f"line{i}\n" for i in range(20)))  # 130 bytes

    first = tail_text_file(path, 0, None, max_lines=5)
    assert first.offset == 30  # only 5 of 20 lines read - offset lags behind the 130-byte file

    path.write_text("".join(f"new{i}\n" for i in range(10)))  # rotated + regrown to 50 bytes: >= offset (30), < last size (130)

    without_last_size = tail_text_file(path, first.offset, first.inode, max_lines=None)
    assert without_last_size.lines != ["new0\n", "new1\n", "new2\n", "new3\n", "new4\n", "new5\n", "new6\n", "new7\n", "new8\n", "new9\n"]

    with_last_size = tail_text_file(path, first.offset, first.inode, max_lines=None, last_size=first.file_size)
    assert with_last_size.lines == ["new0\n", "new1\n", "new2\n", "new3\n", "new4\n", "new5\n", "new6\n", "new7\n", "new8\n", "new9\n"]


def test_plugin_context_report_backlog_clears_percent_when_not_pending():
    context = PluginContext(db=None, settings={})
    assert context.backlog_pending is False
    assert context.backlog_progress_percent is None

    context.report_backlog(True, 42)
    assert context.backlog_pending is True
    assert context.backlog_progress_percent == 42

    context.report_backlog(False, 99)
    assert context.backlog_pending is False
    assert context.backlog_progress_percent is None
