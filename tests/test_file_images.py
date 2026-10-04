import asyncio
import base64
import io
import json
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from app import file_images, workspace


def raster(fmt="PNG", size=(30, 20), **kwargs):
    out = io.BytesIO()
    Image.new("RGB", size, "red").save(out, format=fmt, **kwargs)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP", "GIF", "BMP", "TIFF", "ICO"])
def test_formats(fmt):
    data = raster(fmt, (32, 32))
    result = asyncio.run(file_images.decode_image(data))
    assert result["format"] == fmt
    assert result["frames"] == 1
    with Image.open(io.BytesIO(base64.b64decode(result["data"]))) as image:
        assert image.size == (32, 32)
        assert not image.getexif()


def test_orientation_resize_metadata():
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private metadata"
    result = asyncio.run(file_images.decode_image(raster("JPEG", (3000, 20), exif=exif)))
    assert (result["original_width"], result["original_height"]) == (20, 3000)
    assert result["height"] == 2048
    assert b"private metadata" not in base64.b64decode(result["data"])


@pytest.mark.parametrize("fmt", ["GIF", "PNG", "WEBP", "TIFF"])
def test_frames(fmt):
    out = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(out, format=fmt, save_all=True,
        append_images=[Image.new("RGB", (20, 20), "blue")], duration=100, loop=0)
    result = asyncio.run(file_images.decode_image(out.getvalue(), 1))
    assert result["frames"] == 2 and result["frame"] == 1
    with Image.open(io.BytesIO(base64.b64decode(result["data"]))) as image:
        assert image.getpixel((10, 10))[2] > 240
    with pytest.raises(ValueError, match="frame"):
        asyncio.run(file_images.decode_image(out.getvalue(), 2))


def test_invalid_and_limits():
    for data in (b"%PDF-1.7", b"<svg></svg>", b"xxxxftypavif", b"\x89PNG\r\n\x1a\ninvalid"):
        with pytest.raises(ValueError):
            asyncio.run(file_images.decode_image(data))
    with pytest.raises(ValueError, match="20 MiB"):
        asyncio.run(file_images.decode_image(b"x" * (file_images.MAX_SOURCE + 1)))
    with patch.object(file_images, "MAX_PIXELS", 10), patch.object(Image, "MAX_IMAGE_PIXELS", 10), pytest.raises(Image.DecompressionBombError):
        file_images._decode(raster(), 0)
    for frame in (-1, True, "1"):
        with pytest.raises(ValueError, match="frame"):
            asyncio.run(file_images.decode_image(raster(), frame))


def response(data, identity="same", size=None, eof=True):
    return {"ok": True, "data": base64.b64encode(data).decode(), "identity": identity,
            "size": len(data) if size is None else size, "eof": eof}


def test_connector_read_and_no_ledger():
    call = AsyncMock(return_value={"ok": True, "out": json.dumps(response(raster()))})
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_call", call), patch.object(workspace, "ledger_note") as ledger:
        text, rec = asyncio.run(workspace._read(1, 2, "box", {"path": "a.png"}, "/work", run_id="test"))
    assert rec["image"]["format"] == "PNG"
    assert rec["image"]["data"] not in text and rec["image"]["data"] not in rec["preview"]
    ledger.assert_not_called()
    assert call.call_args.args[2] == "read_binary"


@pytest.mark.parametrize("replies,match", [
    ([{"ok": False}], "connector"),
    ([response(b"x", size=file_images.MAX_SOURCE + 1)], "20 MiB"),
    ([response(b"x", size=2, eof=False), response(b"y", identity="changed", size=2)], "changed"),
    ([response(b"x", size=2)], "incomplete"),
    ([{"ok": True, "data": "!!!", "identity": "a", "size": 1, "eof": True}], "invalid"),
])
def test_connector_errors(replies, match):
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_call", AsyncMock(side_effect=replies)):
        with pytest.raises(workspace.WorkspaceError, match=match):
            asyncio.run(workspace._image_read(1, 2, "box", "/work/a", {}))


def test_connector_text_and_extensionless():
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_read_page", AsyncMock(return_value=("hello\r\n", 20))), patch.object(workspace, "ledger_note"):
        text, rec = asyncio.run(workspace._read(1, 2, "box", {"path": "a.txt"}, "/work"))
        assert text == "From host box:\n1: hello\n\n(End of file — 1 lines.)"
        assert "image" not in rec
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_read_page", AsyncMock(side_effect=workspace.WorkspaceError("file is binary"))), patch.object(workspace, "_agent_call", AsyncMock(return_value=response(raster()))):
        _, rec = asyncio.run(workspace._read(1, 2, "box", {"path": "no-extension"}, "/work"))
        assert "image" in rec


def test_sftp_image():
    payload = raster()
    attrs = SimpleNamespace(size=len(payload), permissions=stat.S_IFREG | 0o644, mtime=1, mtime_ns=0)
    handle = AsyncMock()
    handle.__aenter__.return_value = handle
    handle.stat.return_value = attrs
    handle.read.side_effect = [payload, b""]
    sftp = AsyncMock()
    sftp.open = lambda *args: handle
    sftp.__aenter__.return_value = sftp
    with patch.object(workspace, "_is_agent", return_value=False), patch.object(workspace, "_sftp", AsyncMock(return_value=sftp)), patch.object(workspace, "_sftp_expand_home", AsyncMock(return_value="/work/a.png")):
        _, rec = asyncio.run(workspace._image_read(1, 2, "box", "/work/a.png", {}))
        assert rec["image"]["width"] == 30


def test_permission_denial_never_reads_pixels():
    row = {"id": 2, "alias": "box", "root": "/work"}
    verdict = SimpleNamespace(allowed=False, detail="denied")
    with patch.object(workspace.hosts, "settings_for", return_value={}), patch.object(workspace, "resolve_host_row", return_value=row), patch.object(workspace, "host_root", return_value="/work"), patch.object(workspace.hosts, "effective_permission", return_value="project"), patch("app.permissions.review", AsyncMock(return_value=verdict)), patch.object(workspace, "_image_read", AsyncMock()) as read:
        _, rec = asyncio.run(workspace.execute("read_file", '{"path":"/secret/a.png"}', user_id=1, gate=object()))
        assert rec["status"] == "error"
        read.assert_not_called()


def test_old_connector_refuses_named_image():
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_call", AsyncMock(return_value={"ok": False, "error": "unknown operation"})), patch.object(workspace, "_agent_read_page", AsyncMock()) as text_read:
        with pytest.raises(workspace.WorkspaceError, match="update the connector"):
            asyncio.run(workspace._read(1, 2, "box", {"path": "a.png"}, "/work"))
        text_read.assert_not_called()


def test_decode_timeout_kills_child():
    proc = SimpleNamespace(returncode=None, communicate=AsyncMock(side_effect=asyncio.TimeoutError), wait=AsyncMock())
    from unittest.mock import Mock
    proc.kill = Mock()
    with patch.object(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)):
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(file_images.decode_image(raster()))
    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


def test_text_with_image_extension_uses_magic():
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_call", AsyncMock(return_value=response(b"plain text"))), patch.object(workspace, "_agent_read_page", AsyncMock(return_value=("plain text", 20))), patch.object(workspace, "ledger_note"):
        text, rec = asyncio.run(workspace._read(1, 2, "box", {"path": "fake.png"}, "/work"))
        assert "1: plain text" in text
        assert "image" not in rec


@pytest.mark.parametrize("args", [{"offset": 1}, {"limit": 2000}, {"offset": None}])
def test_image_rejects_text_pagination(args):
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_call", AsyncMock(return_value=response(raster()))):
        with pytest.raises(workspace.WorkspaceError, match="offset and limit"):
            asyncio.run(workspace._read(1, 2, "box", {"path": "a.png", **args}, "/work"))


def test_frame_rejected_on_text():
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_call", AsyncMock(return_value=response(b"plain text"))):
        with pytest.raises(workspace.WorkspaceError, match="frame applies only"):
            asyncio.run(workspace._read(1, 2, "box", {"path": "a.txt", "frame": 0}, "/work"))


@pytest.mark.parametrize("source", ["\u00ff\u00d8\u00ff", "\ufffd\ufffd\ufffd", r"\xff\xd8\xff", r"\u00ff\u00d8\u00ff"])
def test_extensionless_jpeg_text_encodings(source):
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_read_page", AsyncMock(return_value=(source, 20))), patch.object(workspace, "_agent_call", AsyncMock(return_value=response(raster("JPEG")))), patch.object(workspace, "ledger_note") as ledger:
        text, rec = asyncio.run(workspace._read(1, 2, "box", {"path": "no-extension"}, "/work"))
        assert rec["image"]["format"] == "JPEG"
        assert "Image: JPEG" in text
        ledger.assert_not_called()


@pytest.mark.parametrize("nested", [False, True])
def test_access_denied_is_not_upgrade_error(nested):
    error = {"ok": False, "error": "read_binary failed", "err": "Permission denied"}
    reply = {"ok": True, "out": json.dumps(error)} if nested else error
    with patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_call", AsyncMock(return_value=reply)):
        with pytest.raises(workspace.WorkspaceError, match="Permission denied") as caught:
            asyncio.run(workspace._read(1, 2, "box", {"path": "a.png"}, "/work"))
        assert "update" not in str(caught.value)


def test_execute_decode_timeout_has_no_image():
    row = {"id": 2, "alias": "box", "root": "/work"}
    with patch.object(workspace.hosts, "settings_for", return_value={}), patch.object(workspace, "resolve_host_row", return_value=row), patch.object(workspace, "host_root", return_value="/work"), patch.object(workspace.hosts, "effective_permission", return_value="project"), patch.object(workspace, "_is_agent", return_value=True), patch.object(workspace, "_agent_call", AsyncMock(return_value=response(raster()))), patch.object(file_images, "decode_image", AsyncMock(side_effect=asyncio.TimeoutError)):
        text, rec = asyncio.run(workspace.execute("read_file", '{"path":"a.png"}', user_id=1))
        assert rec["status"] == "error"
        assert "time limit" in text
        assert "image" not in rec
