from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "templates"

def render_template(filename: str, **context) -> str:
    path = TEMPLATES_DIR / filename
    html = path.read_text(encoding="utf-8")
    for k, v in context.items():
        html = html.replace(f"{{{{{k}}}}}", str(v))
    return html

def index_html() -> str:
    return render_template("index.html")

def preview_html(url: str) -> str:
    return render_template("preview.html", url=url)