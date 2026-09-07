from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from newsletter_diaria.models import Item, NewsletterDraft


def render_console(draft: NewsletterDraft) -> None:
    print(f"\n=== {draft.headline} ===\n")
    if draft.trends:
        print("Trends:")
        for trend in draft.trends:
            print(f"- {trend}")
        print()
    for ranked in draft.items:
        item = ranked.item
        when = item.published_at.isoformat() if item.published_at else "no date"
        title = ranked.translated_title or item.title
        print(f"{ranked.rank}. {title}  [{ranked.importance}/100]")
        print(f"   Source: {item.source}")
        print(f"   Date:   {when}")
        print(f"   Link:   {item.link}")
        print(f"   Summary:{ranked.summary}")
        if ranked.why:
            print(f"   Why:    {ranked.why}")
        if ranked.takeaway:
            print(f"   Takeaway:{ranked.takeaway}")
        print()


def write_markdown(draft: NewsletterDraft, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {draft.headline}", ""]
    if draft.trends:
        lines.extend(["## Trends", ""])
        lines.extend(f"- {trend}" for trend in draft.trends)
        lines.append("")

    for ranked in draft.items:
        item = ranked.item
        when = item.published_at.isoformat() if item.published_at else "no date"
        title = ranked.translated_title or item.title
        lines.extend(
            [
                f"{ranked.rank}. **{title}**  _[{ranked.importance}/100]_",
                f"   - Source: {item.source}",
                f"   - Date: {when}",
                f"   - Link: {item.link}",
                f"   - Summary: {ranked.summary or '—'}",
            ]
        )
        if ranked.why:
            lines.append(f"   - Why: {ranked.why}")
        if ranked.takeaway:
            lines.append(f"   - Takeaway: {ranked.takeaway}")
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")


def render_email_text(draft: NewsletterDraft) -> str:
    lines = [draft.headline or "Resumen diario", ""]
    if draft.trends:
        lines.append("Tendencias:")
        lines.extend(f"- {trend}" for trend in draft.trends)
        lines.append("")

    for ranked in draft.items:
        item = ranked.item
        when = item.published_at.isoformat() if item.published_at else "sin fecha"
        title = ranked.translated_title or item.title
        lines.extend(
            [
                f"{ranked.rank}. {title} [{ranked.importance}/100]",
                f"Fuente: {item.source}",
                f"Fecha: {when}",
                f"Link: {item.link}",
                f"Resumen: {ranked.summary or '—'}",
            ]
        )
        if ranked.why:
            lines.append(f"Por qué importa: {ranked.why}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


MONTHS_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def spanish_date(moment: datetime) -> str:
    return f"{moment.day} de {MONTHS_ES[moment.month - 1]} de {moment.year}"


def render_email_html(draft: NewsletterDraft) -> str:
    """Maquetacion para cliente de correo: tablas, fuentes del sistema y colores
    literales. Nada de flexbox, grid ni variables CSS, que Outlook y Gmail no
    soportan de forma fiable."""

    def esc(value: str) -> str:
        return html.escape(value or "")

    def fmt_dt(item: Item) -> str:
        if not item.published_at:
            return "sin fecha"
        return f"{item.published_at.day} {MONTHS_ES[item.published_at.month - 1][:3]}"

    trend_html = ""
    if draft.trends:
        rows = "".join(
            f'<tr><td class="trend-bullet">&#8212;</td><td class="trend-text">{esc(trend)}</td></tr>'
            for trend in draft.trends
        )
        trend_html = f"""
              <tr><td class="section">
                <p class="kicker">Lo que se repite hoy</p>
                <table role="presentation" class="trend-table"><tbody>{rows}</tbody></table>
              </td></tr>"""

    articles: list[str] = []
    for position, ranked in enumerate(draft.items, start=1):
        item = ranked.item
        title = esc(ranked.translated_title or item.title)
        blocks = [
            f"""
              <tr><td class="article">
                <p class="meta">
                  <span class="num">{position:02d}</span>
                  <span class="sep">&#183;</span>{esc(item.source)}
                  <span class="sep">&#183;</span>{esc(fmt_dt(item))}
                  <span class="sep">&#183;</span><span class="score">{ranked.importance}/100</span>
                </p>
                <h2 class="title"><a href="{esc(item.link)}">{title}</a></h2>
                <p class="summary">{esc(ranked.summary or item.summary)}</p>"""
        ]
        if ranked.why:
            blocks.append(
                f'<table role="presentation" class="note"><tbody><tr><td class="note-cell">'
                f'<span class="note-label">Por qu&eacute; importa</span>{esc(ranked.why)}'
                f"</td></tr></tbody></table>"
            )
        if ranked.takeaway:
            blocks.append(f'<p class="takeaway"><span class="note-label">Para llevarse</span>{esc(ranked.takeaway)}</p>')
        blocks.append(f'<p class="readmore"><a href="{esc(item.link)}">Leer en {esc(item.source)} &#8594;</a></p>')
        blocks.append("</td></tr>")
        articles.append("".join(blocks))

    articles_html = '<tr><td class="rule"></td></tr>'.join(articles)
    sources = ", ".join(dict.fromkeys(ranked.item.source for ranked in draft.items))
    count = len(draft.items)
    plural = "art\u00edculo" if count == 1 else "art\u00edculos"
    preheader = esc(", ".join(draft.trends[:3]) if draft.trends else "; ".join(
        (ranked.translated_title or ranked.item.title) for ranked in draft.items[:2]
    ))

    return f"""<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light dark">
    <meta name="supported-color-schemes" content="light dark">
    <title>{esc(draft.headline or 'Resumen diario')}</title>
    <style>
      :root {{ color-scheme: light dark; supported-color-schemes: light dark; }}
      body {{ margin: 0; padding: 0; background: #f2f1ee; color: #16181d;
              font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
              -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
      .preheader {{ display: none !important; visibility: hidden; opacity: 0; color: transparent;
                    height: 0; width: 0; overflow: hidden; mso-hide: all; }}
      table {{ border-collapse: collapse; }}
      .wrap {{ width: 100%; background: #f2f1ee; padding: 28px 0 40px; }}
      .container {{ width: 640px; max-width: 640px; background: #ffffff; }}
      .masthead {{ padding: 34px 40px 22px; border-bottom: 2px solid #16181d; }}
      .kicker {{ margin: 0 0 14px; font-size: 10.5px; letter-spacing: 1.4px; text-transform: uppercase;
                 font-weight: 700; color: #8a8578; }}
      h1 {{ margin: 0; font-family: Georgia, 'Times New Roman', serif; font-size: 27px;
            line-height: 1.28; font-weight: 400; color: #16181d; letter-spacing: -0.2px; }}
      .dateline {{ margin: 14px 0 0; font-size: 12.5px; color: #8a8578; }}
      .section {{ padding: 22px 40px 4px; }}
      .trend-table {{ width: 100%; }}
      .trend-bullet {{ width: 18px; vertical-align: top; color: #b4ad9c; font-size: 13px;
                       line-height: 1.6; padding: 3px 0; }}
      .trend-text {{ font-size: 13.5px; line-height: 1.6; color: #4a4d55; padding: 3px 0; }}
      .article {{ padding: 26px 40px 28px; }}
      .rule {{ height: 1px; background: #e6e3dc; line-height: 1px; font-size: 0; }}
      .meta {{ margin: 0 0 9px; font-size: 11px; letter-spacing: 0.5px; text-transform: uppercase;
               color: #8a8578; font-weight: 600; }}
      .num {{ color: #16181d; font-weight: 700; }}
      .sep {{ padding: 0 6px; color: #c9c3b4; }}
      .score {{ letter-spacing: 0; text-transform: none; font-weight: 400; }}
      .title {{ margin: 0 0 11px; font-family: Georgia, 'Times New Roman', serif; font-size: 20px;
                line-height: 1.34; font-weight: 400; }}
      .title a {{ color: #16181d; text-decoration: none; }}
      .summary {{ margin: 0; font-size: 15px; line-height: 1.62; color: #3b3e45; }}
      .note {{ width: 100%; margin: 14px 0 0; }}
      .note-cell {{ padding: 12px 0 12px 14px; border-left: 2px solid #16181d;
                    font-size: 13.5px; line-height: 1.58; color: #4a4d55; }}
      .note-label {{ display: block; font-size: 10px; letter-spacing: 1.2px; text-transform: uppercase;
                     font-weight: 700; color: #8a8578; margin-bottom: 4px; }}
      .takeaway {{ margin: 13px 0 0; font-size: 13.5px; line-height: 1.58; color: #4a4d55; }}
      .readmore {{ margin: 15px 0 0; font-size: 12.5px; }}
      .readmore a {{ color: #8a8578; text-decoration: none; border-bottom: 1px solid #d8d3c7;
                     padding-bottom: 1px; }}
      .footer {{ padding: 24px 40px 30px; border-top: 2px solid #16181d; }}
      .footer p {{ margin: 0 0 6px; font-size: 11.5px; line-height: 1.6; color: #8a8578; }}
      @media only screen and (max-width: 640px) {{
        .wrap {{ padding: 0 !important; }}
        .container {{ width: 100% !important; }}
        .masthead {{ padding: 26px 20px 18px !important; }}
        h1 {{ font-size: 23px !important; }}
        .section {{ padding: 18px 20px 2px !important; }}
        .article {{ padding: 22px 20px 24px !important; }}
        .title {{ font-size: 18.5px !important; }}
        .footer {{ padding: 20px 20px 26px !important; }}
      }}
      @media (prefers-color-scheme: dark) {{
        body, .wrap {{ background: #14151a !important; }}
        body {{ color: #e8e6e1 !important; }}
        .container {{ background: #1b1d23 !important; }}
        .masthead, .footer {{ border-color: #4c4f58 !important; }}
        h1, .title a, .num {{ color: #f4f2ee !important; }}
        .kicker, .dateline, .note-label, .footer p, .readmore a {{ color: #9a958a !important; }}
        .summary {{ color: #c8c6c1 !important; }}
        .trend-text, .note-cell, .takeaway {{ color: #b6b4af !important; }}
        .trend-bullet, .sep {{ color: #5d6067 !important; }}
        .rule {{ background: #2f323a !important; }}
        .note-cell {{ border-left-color: #7f8590 !important; }}
        .readmore a {{ border-bottom-color: #3a3d45 !important; }}
      }}
    </style>
  </head>
  <body>
    <div class="preheader">{preheader}</div>
    <table role="presentation" class="wrap" width="100%"><tbody><tr><td align="center">
      <table role="presentation" class="container" width="640"><tbody>
          <tr><td class="masthead">
            <p class="kicker">Bolet&iacute;n t&eacute;cnico diario</p>
            <h1>{esc(draft.headline or 'Resumen diario')}</h1>
            <p class="dateline">{spanish_date(datetime.now())} &#183; {count} {plural}</p>
          </td></tr>{trend_html}
          <tr><td class="rule"></td></tr>
          {articles_html}
          <tr><td class="footer">
            <p>Selecci&oacute;n autom&aacute;tica sobre las &uacute;ltimas 72 horas. Un art&iacute;culo por fuente como m&aacute;ximo.</p>
            <p>Fuentes de esta edici&oacute;n: {esc(sources) or '&#8212;'}</p>
          </td></tr>
      </tbody></table>
    </td></tr></tbody></table>
  </body>
</html>
"""
