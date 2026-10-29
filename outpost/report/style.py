"""The report's CSS: one page that reads well on screen and on paper."""

CSS = """
body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;color:#15191e;max-width:960px;margin:40px auto;padding:0 20px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:34px 0 10px;border-bottom:1px solid #ddd;padding-bottom:6px}h3{font-size:15px;margin:18px 0 6px}
.sub{color:#666;margin:0 0 18px}.verdict{padding:16px 20px;border-radius:10px;margin:18px 0;font-size:17px}
.ok{background:#e3f4ea;color:#14532d}.bad{background:#fbe7e8;color:#7f1d1d}
table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}th{text-align:left;color:#666;font-weight:600;padding:6px 8px;border-bottom:1px solid #ddd}
td{padding:6px 8px;border-bottom:1px solid #eee;vertical-align:top}code{font-family:ui-monospace,Menlo,monospace;font-size:13px;background:#f3f4f2;padding:0 4px;border-radius:4px}
.pass{color:#1a7f45;font-weight:600}.fail{color:#b4232c;font-weight:600}.muted{color:#777}.small{font-size:13px}
@media print{body{margin:0}h2{break-after:avoid}}
"""
