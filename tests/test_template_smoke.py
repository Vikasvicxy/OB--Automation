"""Template / route smoke test — recovery verification.

Asserts every HTML page route renders HTTP 200 with a non-truncated body
(contains </main>, </html>, and a minimum size), and that key JS hooks /
element IDs are present. This guards against the truncated templates produced
by a bad layout transformation.

Read-only: uses Starlette's TestClient; performs NO real uploads.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from starlette.testclient import TestClient

from app.main import app

PAGE_ROUTES = [
    "/",
    "/smart-upload",
    "/manual-entry",
    "/candidates",
    "/batch-review",
    "/portal",
    "/live-upload",
    "/admin/master-data",
    "/validation",
    "/reports",
    "/settings",
    "/data-quality",
]

# Route -> substrings that must be present (JS hooks / structural markers).
REQUIRED_MARKERS = {
    "/smart-upload": ["</main>", "</html>"],
    "/manual-entry": ["RULES_DATA", "candidateForm", "btnReview", "reviewScreen", "</html>"],
    "/candidates": ["/manual-entry?edit=", "</html>"],
    "/batch-review": ["/batch-review?batch_id=", "</html>"],
    "/portal": ["/portal?batch_id=", "</html>"],
    "/live-upload": ["REAL_UPLOAD_ENABLED", "ESAMPARK_LIVE_TEST_MODE", "</html>"],
    "/admin/master-data": ["</html>"],
    "/validation": ["/api/validation/run", "</html>"],
}


def test_all_pages_render_non_truncated():
    client = TestClient(app)
    checked = 0
    for url in PAGE_ROUTES:
        resp = client.get(url)
        assert resp.status_code == 200, f"{url} returned {resp.status_code}"
        text = resp.text or ""
        assert len(text) > 1500, f"{url} unexpectedly small ({len(text)} bytes)"
        assert "</main>" in text, f"{url} missing </main>"
        assert "</html>" in text, f"{url} missing </html> (truncated)"
        for marker in REQUIRED_MARKERS.get(url, []):
            assert marker in text, f"{url} missing marker {marker!r}"
        checked += 1
    assert checked == len(PAGE_ROUTES)


def test_query_variants_render():
    client = TestClient(app)
    for url in ["/manual-entry?edit=1", "/candidates?search=a", "/portal?batch_id=1"]:
        resp = client.get(url)
        assert resp.status_code == 200, f"{url} returned {resp.status_code}"
        assert len((resp.text) or "") > 1500
        assert "</html>" in (resp.text or "")


if __name__ == "__main__":
    test_all_pages_render_non_truncated()
    test_query_variants_render()
    print("template smoke: OK")
