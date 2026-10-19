"""EXPERIMENT [datasheet] browser_headers: off by default; when on, only PDF downloads send browser headers."""
import io
import unittest
from unittest import mock

from ecadlib import datasheet as D
from ecadlib import rules


class Raw(io.BytesIO):
    def read(self, n=-1, decode_content=False):
        return super().read(n)


class FakeResp:
    def __init__(self, status, body=b"%PDF-1.4\n/Type /Page\n", headers=None):
        self.status_code, self.raw, self.headers, self.url = status, Raw(body), headers or {}, "u"

    def close(self):
        pass


class FakeSess:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def get(self, url, **kw):
        self.calls.append(kw["headers"])
        return self.answers.pop(0)


class FakeHTTP:
    offline, min_interval, _last = False, 0, {}

    def __init__(self, answers):
        self.s, self.log = FakeSess(answers), []


class BrowserHeaders(unittest.TestCase):
    def test_off_by_default(self):
        self.assertIs(D.SETTINGS["browser_headers"], False)
        self.assertIs(rules.DEFAULTS["datasheet"]["browser_headers"], False)
        self.assertIs(D.settings({})["browser_headers"], False)

    def test_honest_headers_by_default(self):
        h = FakeHTTP([FakeResp(200)])
        D.PdfStore("/nonexistent", h)._get("https://www.nexperia.com/a.pdf")
        self.assertNotIn("User-Agent", h.s.calls[0])

    def test_browser_headers_when_on(self):
        h = FakeHTTP([FakeResp(200)])
        D.PdfStore("/nonexistent", h, browser=True)._get("https://www.nexperia.com/docs/a.pdf")
        sent = h.s.calls[0]
        self.assertIn("Mozilla/5.0", sent["User-Agent"])
        self.assertEqual(sent["Accept"], "application/pdf,*/*;q=0.8")
        self.assertEqual(sent["Accept-Language"], "en-US,en;q=0.9")
        self.assertEqual(sent["Referer"], "https://www.nexperia.com/")

    def test_retries_5xx_then_gives_up(self):
        h = FakeHTTP([FakeResp(503), FakeResp(503), FakeResp(200)])
        with mock.patch.object(D.time, "sleep"):
            st = D.PdfStore("/nonexistent", h, retries=2)
            self.assertEqual(st._get("https://x.com/a.pdf")[0], 200)
        self.assertEqual(st.attempts, 3)
        h = FakeHTTP([FakeResp(403)])
        st = D.PdfStore("/nonexistent", h, retries=2)
        self.assertEqual(st._get("https://x.com/a.pdf")[0], 403)   # a refusal is not retried
        self.assertEqual(st.attempts, 1)


if __name__ == "__main__":
    unittest.main()
