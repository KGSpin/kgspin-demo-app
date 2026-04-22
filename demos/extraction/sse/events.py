"""Server-Sent Events emission helper.

The single ``sse_event(event_type, data)`` function is the one serialization
point for every SSE payload the demo app emits. Route handlers / pipeline
orchestrators call this to produce the ``event:`` + ``data:`` wire frames
that the ``EventSource`` client in ``static/compare.html`` consumes.
"""

from __future__ import annotations

import json


def sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"
