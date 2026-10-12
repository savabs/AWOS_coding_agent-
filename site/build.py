"""Wrap site/index.html into a deployable public/index.html."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="description" content="AWOS works through your small coding tickets in the background '
        'and hands you review branches with test results, for cents per ticket.">'
        '<style>:root{padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
        'body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style></head><body>')

out = HERE / "public" / "index.html"
out.parent.mkdir(exist_ok=True)
out.write_text(HEAD + (HERE / "index.html").read_text(encoding="utf-8") + "</body></html>\n", encoding="utf-8")
print("wrote", out)
