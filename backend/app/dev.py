"""Dev-only smoke-test routes.

The SSE number stream exists to prove progressive streaming works through the
FULL production path (gunicorn -> nginx `proxy_buffering off` -> TLS ->
browser EventSource) BEFORE the real note-generation stream is built on the
same transport. It is deliberately kept in the repo as evidence of
infra-first sequencing.
"""

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()


async def _count_to_twenty() -> AsyncGenerator[str, None]:
    for i in range(1, 21):
        yield f"data: {i}\n\n"
        await asyncio.sleep(0.2)
    # Named terminal event so the client can close cleanly instead of
    # treating the server's connection close as an error and reconnecting.
    yield "event: done\ndata: end\n\n"


@router.get("/dev/stream-test")
async def stream_test() -> StreamingResponse:
    return StreamingResponse(
        _count_to_twenty(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Belt-and-suspenders: also disables buffering per-response in
            # nginx. The primary fix is `proxy_buffering off` in the nginx
            # location block (infra/nginx/ai-scribe.conf).
            "X-Accel-Buffering": "no",
        },
    )
