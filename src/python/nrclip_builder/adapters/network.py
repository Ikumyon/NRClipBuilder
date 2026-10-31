import json
import urllib.parse
from uuid import uuid4

from PySide6.QtCore import QByteArray, QEventLoop, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .. import _native

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SERVER_TIMEOUT = 180
COMMUNICATION_TIMEOUT = 240


def parse_overpass(data, token):
    nodes = {e["id"]: [e["lon"], e["lat"]] for e in data["elements"] if e.get("type") == "node"}
    features = []
    for way in data["elements"]:
        token.check()
        if way.get("type") != "way":
            continue
        # Missing nodes split the geometry rather than inventing connecting segments.
        lines, line = [], []
        for node in way.get("nodes", []):
            if node in nodes:
                line.append(nodes[node])
            else:
                if len(line) >= 2:
                    lines.append(line)
                line = []
        if len(line) >= 2:
            lines.append(line)
        if lines:
            features.append({"type": "Feature", "id": uuid4().hex,
                             "properties": {**way.get("tags", {}), "osm_id": way["id"]},
                             "geometry": {"type": "MultiLineString", "coordinates": lines}})
    return features


def fetch(bbox, token):
    _native.validate_bbox(bbox)
    w, s, e, n = bbox
    query = f'[out:json][timeout:{SERVER_TIMEOUT}];way["railway"~"^(rail|subway|tram|light_rail|monorail|funicular|abandoned|disused|construction|proposed)$"]({s},{w},{n},{e});out body;>;out skel qt;'
    return parse_overpass(json.loads(request_overpass(query, token)), token)


def request_overpass(query, token):
    """Qt network I/O runs in the worker's event loop and can always be aborted."""
    token.check()
    loop = QEventLoop()
    manager = QNetworkAccessManager()
    request = QNetworkRequest(QUrl(OVERPASS_URL))
    request.setRawHeader(b"User-Agent", b"NRClipBuilder")
    request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/x-www-form-urlencoded")
    reply = manager.post(request, QByteArray(urllib.parse.urlencode({"data": query}).encode()))
    chunks = []
    reply.readyRead.connect(lambda: chunks.append(bytes(reply.readAll())))
    reply.finished.connect(loop.quit)
    cancelled = False
    timed_out = False
    poll = QTimer()
    poll.setInterval(50)

    def check_cancel():
        nonlocal cancelled
        try:
            token.check()
        except Exception:
            cancelled = True
            reply.abort()

    def timeout():
        nonlocal timed_out
        timed_out = True
        reply.abort()

    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(timeout)
    poll.timeout.connect(check_cancel)
    poll.start()
    deadline.start(COMMUNICATION_TIMEOUT * 1000)
    if not reply.isFinished():
        loop.exec()
    poll.stop()
    deadline.stop()
    chunks.append(bytes(reply.readAll()))
    body = b"".join(chunks)
    status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
    failure = reply.errorString() if reply.error() != QNetworkReply.NetworkError.NoError else ""
    if cancelled:
        token.check()
    if timed_out:
        raise TimeoutError(f"Overpass: {COMMUNICATION_TIMEOUT} s")
    token.check()
    if failure or status != 200:
        raise RuntimeError(f"Overpass HTTP {status}: {failure}\n{body.decode('utf-8', errors='replace')[:500]}")
    data = json.loads(body)
    if data.get("remark"):
        raise RuntimeError(f"Overpass: {data['remark']}")
    return body
