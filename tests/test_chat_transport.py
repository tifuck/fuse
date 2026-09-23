"""Compression must shrink snapshots without buffering token streams."""
import gzip
import unittest

from starlette.middleware.gzip import GZipMiddleware


class ChatCompressionTest(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_compression_and_stream_passthrough(self):
        for content_type in (b"application/json", b"text/event-stream"):
            with self.subTest(content_type=content_type):
                sent = []
                body = b'{"content":"' + b"repeated conversation text " * 1000 + b'"}'

                async def send(message):
                    sent.append(message)

                async def receive():
                    return {"type": "http.request", "body": b""}

                async def app(scope, receive, send):
                    await send({"type": "http.response.start", "status": 200,
                                "headers": [(b"content-type", content_type)]})
                    if content_type == b"text/event-stream":
                        await send({"type": "http.response.body", "body": b"data: first\n\n", "more_body": True})
                        # The first token must have reached the client before
                        # the producer yields the next chunk or completes.
                        self.assertEqual(sent[-1]["body"], b"data: first\n\n")
                        self.assertNotIn(b"content-encoding", dict(sent[0]["headers"]))
                    await send({"type": "http.response.body", "body": body})

                await GZipMiddleware(app, minimum_size=1024, compresslevel=3)(
                    {"type": "http", "headers": [(b"accept-encoding", b"gzip")]}, receive, send)
                if content_type == b"application/json":
                    self.assertEqual(dict(sent[0]["headers"])[b"content-encoding"], b"gzip")
                    compressed = b"".join(message.get("body", b"") for message in sent)
                    self.assertEqual(gzip.decompress(compressed), body)
                    self.assertLess(len(compressed), len(body) // 10)
