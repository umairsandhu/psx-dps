#!/usr/bin/env python3
"""Build the GitHub Pages site from the repo's markdown.

Deliberately generates static HTML from the same files GitHub renders,
rather than keeping a parallel copy of the prose: the README and docs/ stay
the single source of truth, and nothing here can drift from them.

Jekyll would have needed YAML front matter in every doc, which GitHub
renders as an ugly table when you view the file in the repo. This costs a
build step and keeps the markdown clean.
"""

import html
import json
import os
import re
import shutil
import sys

import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_site")
BASE_URL = "https://umairsandhu.github.io/psx-dps"
REPO_URL = "https://github.com/umairsandhu/psx-dps"

PAGES = [
    ("README.md", "index.html", "psx-dps — Pakistan Stock Exchange (PSX) market data in Python",
     "Live PSX quotes, KSE-100 constituents, 5 years of end-of-day history, "
     "intraday ticks and corporate announcements. No API key, zero "
     "dependencies, built-in caching and rate limiting."),
    ("docs/API.md", "api.html", "Python API reference — psx-dps",
     "Every method, option and exception in the psx-dps Pakistan Stock "
     "Exchange client: quotes, EOD history, indices, announcements."),
    ("docs/ENDPOINTS.md", "endpoints.html", "PSX data portal endpoint reference — psx-dps",
     "Every dps.psx.com.pk endpoint, payload shape and quirk: /symbols, "
     "/market-watch, /timeseries, /historical, /announcements."),
    ("docs/SYMBOLS.md", "symbols.html", "PSX symbols explained — psx-dps",
     "What a Pakistan Stock Exchange ticker tells you: listed vs trading, "
     "debt maturity dates, rights issues, sector codes, index membership."),
    ("docs/DISCOVERY.md", "discovery.html", "How the PSX API was reverse-engineered — psx-dps",
     "How the undocumented Pakistan Stock Exchange endpoints were found, "
     "and why dps.psx.com.pk returns 404 for data routes on some nodes."),
    ("docs/FAIR-USE.md", "fair-use.html", "Polling PSX responsibly — psx-dps",
     "Request budgets for polling Pakistan Stock Exchange data on a "
     "schedule, and how to keep the footprint small."),
    ("docs/STAYING-UNBLOCKED.md", "staying-unblocked.html", "Not getting blocked by PSX — psx-dps",
     "Why data clients get blocked, what a cooldown is, and the runbook "
     "for when the Pakistan Stock Exchange pushes back."),
    ("docs/TROUBLESHOOTING.md", "troubleshooting.html", "PSX API troubleshooting — psx-dps",
     "Why dps.psx.com.pk returns 404 when the website works, what a 403 "
     "really means, and how to tell when PSX changed its IP addresses."),
    ("docs/EVALUATION-PROMPT.md", "evaluation.html", "Audit your PSX integration — psx-dps",
     "A ready-made brief for auditing an existing Pakistan Stock Exchange "
     "data client against eleven documented failure modes."),
]

NAV = [
    ("index.html", "Home"), ("api.html", "API"), ("endpoints.html", "Endpoints"),
    ("symbols.html", "Symbols"), ("fair-use.html", "Fair use"),
    ("troubleshooting.html", "Troubleshooting"), ("discovery.html", "How it works"),
]


def md_to_html(text):
    return markdown.markdown(
        text,
        extensions=["extra", "toc", "sane_lists", "codehilite"],
        extension_configs={"codehilite": {"noclasses": True,
                                          "pygments_style": "friendly"}},
    )


def rewrite_links(body):
    """Point inter-document links at the built pages."""
    mapping = {src: dst for src, dst, _t, _d in PAGES}
    for src, dst in mapping.items():
        leaf = os.path.basename(src)
        body = body.replace(f'href="docs/{leaf}', f'href="{dst}')
        body = body.replace(f'href="{leaf}', f'href="{dst}')
    return body


def faq_jsonld(markdown_text):
    """Turn the README's <details> FAQ into FAQPage structured data.

    This is the bit search engines can surface directly, so it is worth
    generating from the real questions rather than inventing a second list.
    """
    items = []
    for block in re.findall(r"<details>\s*<summary>(.*?)</summary>(.*?)</details>",
                            markdown_text, re.S):
        question = re.sub(r"<[^>]+>", "", block[0]).strip()
        answer = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", md_to_html(block[1]))).strip()
        if question and answer:
            items.append({
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer[:900]},
            })
    if not items:
        return ""
    return json.dumps({"@context": "https://schema.org",
                       "@type": "FAQPage", "mainEntity": items}, indent=2)


SOFTWARE_JSONLD = json.dumps({
    "@context": "https://schema.org",
    "@type": "SoftwareSourceCode",
    "name": "psx-dps",
    "description": "Unofficial Python client and CLI for Pakistan Stock "
                   "Exchange (PSX) market data from dps.psx.com.pk.",
    "codeRepository": REPO_URL,
    "programmingLanguage": "Python",
    "license": "https://opensource.org/licenses/MIT",
    "author": {"@type": "Person", "name": "Umair Aslam Sandhu"},
    "keywords": "Pakistan Stock Exchange, PSX, KSE-100, market data, "
                "Karachi Stock Exchange, Python, financial data",
}, indent=2)


def render(template, *, title, description, body, canonical, jsonld):
    links = []
    for href, label in NAV:
        cls = ' class="current"' if href == canonical else ""
        links.append(f'<a href="{href}"{cls}>{label}</a>')
    nav = "\n".join(links)
    return (template
            .replace("{{TITLE}}", html.escape(title))
            .replace("{{DESCRIPTION}}", html.escape(description))
            # "/" and "/index.html" are the same page; canonicalise to the
            # directory form so search engines do not see a duplicate.
            .replace("{{CANONICAL}}",
                     f"{BASE_URL}/" if canonical == "index.html"
                     else f"{BASE_URL}/{canonical}")
            .replace("{{NAV}}", nav)
            .replace("{{JSONLD}}", jsonld)
            .replace("{{BODY}}", body)
            .replace("{{REPO}}", REPO_URL))


def main():
    template = open(os.path.join(ROOT, "site", "template.html")).read()
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
        os.makedirs(OUT)
    else:
        os.makedirs(OUT, exist_ok=True)
    shutil.copy(os.path.join(ROOT, "site", "style.css"), OUT)

    built = []
    for src, dst, title, description in PAGES:
        path = os.path.join(ROOT, src)
        if not os.path.exists(path):
            print(f"  skip (missing): {src}", file=sys.stderr)
            continue
        raw = open(path).read()
        body = rewrite_links(md_to_html(raw))
        blocks = [SOFTWARE_JSONLD]
        if dst == "index.html":
            faq = faq_jsonld(raw)
            if faq:
                blocks.append(faq)
        jsonld = "\n".join(
            f'<script type="application/ld+json">{b}</script>' for b in blocks
        )
        with open(os.path.join(OUT, dst), "w") as fh:
            fh.write(render(template, title=title, description=description,
                            body=body, canonical=dst, jsonld=jsonld))
        built.append(dst)
        print(f"  built {dst:<26} from {src}")

    urls = "\n".join(
        f"  <url><loc>{BASE_URL}/{'' if p == 'index.html' else p}</loc>"
        f"<changefreq>weekly</changefreq>"
        f"<priority>{'1.0' if p == 'index.html' else '0.8'}</priority></url>"
        for p in built
    )
    with open(os.path.join(OUT, "sitemap.xml"), "w") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                 '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                 f"{urls}\n</urlset>\n")
    with open(os.path.join(OUT, "robots.txt"), "w") as fh:
        fh.write(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    print(f"  built sitemap.xml ({len(built)} urls), robots.txt")


if __name__ == "__main__":
    main()
