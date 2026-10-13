"""The downloader must follow CDSCO's link page to the real PDF."""
import pandas as pd

from nsq import config, download

JSP = ("https://cdsco.gov.in/opencms/opencms/system/modules/CDSCO.WEB/elements/"
       "download_file_division.jsp?num_id=MTI5Mjc=")
LINK_PAGE = b"""<html><body>
<a href="/opencms/resources/UploadCDSCOWeb/2018/UploadAlertsFiles/CDSCO NSQ june25.pdf">CDSCO NSQ june25.pdf</a>
</body></html>"""
PDF_URL = ("https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadAlertsFiles/"
           "CDSCO%20NSQ%20june25.pdf")


class FakeResp:
    def __init__(self, content, url):
        self.content, self.url, self.status_code = content, url, 200

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")


class FakeSession:
    def __init__(self, pages):
        self.pages, self.calls, self.headers = pages, [], {}

    def get(self, url, timeout=None):
        self.calls.append(url)
        return FakeResp(self.pages.get(url, b"<html>not found</html>"), url)


def test_find_pdf_link_handles_spaces():
    assert download.find_pdf_link(LINK_PAGE.decode(), JSP) == PDF_URL


def test_find_pdf_link_meta_refresh_and_js():
    meta = '<meta http-equiv="refresh" content="0; url=/files/a b.pdf">'
    assert download.find_pdf_link(meta, JSP) == "https://cdsco.gov.in/files/a%20b.pdf"
    js = "<script>window.location.href = '/files/c.pdf';</script>"
    assert download.find_pdf_link(js, JSP) == "https://cdsco.gov.in/files/c.pdf"


def test_fetch_pdf_follows_link_page(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)
    s = FakeSession({JSP: LINK_PAGE, PDF_URL: b"\r\n%PDF-1.4 fake"})
    data, url = download.fetch_pdf(s, JSP, debug_dir=tmp_path)
    assert data.startswith(b"%PDF") and url == PDF_URL
    assert s.calls == [JSP, PDF_URL]


def test_fetch_pdf_direct_pdf(monkeypatch):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)
    s = FakeSession({JSP: b"%PDF-1.7 direct"})
    data, url = download.fetch_pdf(s, JSP)
    assert data == b"%PDF-1.7 direct" and url == JSP


def test_fetch_pdf_saves_page_when_no_pdf(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)
    s = FakeSession({JSP: b"<html>Session expired</html>"})
    data, url = download.fetch_pdf(s, JSP, debug_dir=tmp_path, debug_name="x.pdf")
    assert data is None and (tmp_path / "x.html").exists()


def test_download_all_writes_pdf_and_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "REQUEST_DELAY_SECONDS", 0)
    p = config.get_paths(tmp_path).ensure()
    pd.DataFrame([{"num_id": 12927, "title": "List of NSQ for June 2025", "url": JSP,
                   "doc_type": "central_nsq", "is_revised": False,
                   "report_year": 2025, "report_month": 6}]).to_csv(p.alert_index, index=False)
    s = FakeSession({JSP: LINK_PAGE, PDF_URL: b"%PDF-1.4 fake"})
    m = download.download_all(p, start_year=2019, session=s)
    assert m.loc[0, "status"] == "downloaded" and m.loc[0, "pdf_url"] == PDF_URL
    assert (p.pdfs / "2025-06_central_nsq_12927.pdf").read_bytes().startswith(b"%PDF")


def test_model_skips_with_too_few_months(monkeypatch, tmp_path):
    """A test run (--limit 5) gives about 2 months; the model must skip, not crash."""
    import json

    from nsq import model
    monkeypatch.setattr(model, "query", lambda p, sql: pd.DataFrame({"report_month": ["2025-05-01", "2025-06-01"]}))
    p = config.get_paths(tmp_path).ensure()
    m = model.train_and_evaluate(p)
    assert m["skipped"] and "has 2" in m["reason"]
    assert json.loads((p.exports / "model_metrics.json").read_text())["skipped"]
