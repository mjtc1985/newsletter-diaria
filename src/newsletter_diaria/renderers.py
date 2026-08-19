from __future__ import annotations

import html
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
                f"   - Why: {ranked.why or '—'}",
                f"   - Takeaway: {ranked.takeaway or '—'}",
                "",
            ]
        )
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


def render_email_html(draft: NewsletterDraft) -> str:
    def esc(value: str) -> str:
        return html.escape(value or "")

    def fmt_dt(item: Item) -> str:
        return item.published_at.isoformat() if item.published_at else "sin fecha"

    trend_html = ""
    if draft.trends:
        trend_html = """
        <div class="trends">
          <h2>Tendencias</h2>
          <ul>
            {trends}
          </ul>
        </div>
        """.format(trends="".join(f"<li>{esc(trend)}</li>" for trend in draft.trends))

    cards_html = '<tr><td class="card-gap">&nbsp;</td></tr>'.join(
        f"""
            <tr>
              <td class="card">
                <div class="meta">
                  <span class="badge">#{ranked.rank}</span>
                  <span class="score">{ranked.importance}/100</span>
                </div>
                <h3><a href="{esc(ranked.item.link)}">{esc(ranked.translated_title or ranked.item.title)}</a></h3>
                <p class="source">{esc(ranked.item.source)} · {esc(fmt_dt(ranked.item))}</p>
                <p class="summary">{esc(ranked.summary or ranked.item.summary or '—')}</p>
                {('<p class="why"><span class="why-label">Por qué importa:</span> ' + esc(ranked.why) + '</p>') if ranked.why else ''}
              </td>
            </tr>
        """
        for ranked in draft.items
    )

    # Texto de preview (preheader): lo que se ve en la lista de la bandeja
    preheader = esc(", ".join(draft.trends[:3]) if draft.trends else "Resumen diario de tecnología e IA")

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
      body {{ margin: 0; padding: 0; background: #f4f7fb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #0f172a; -webkit-text-size-adjust: 100%; }}
      .preheader {{ display: none !important; visibility: hidden; opacity: 0; color: transparent; height: 0; width: 0; overflow: hidden; mso-hide: all; }}
      .wrap {{ width: 100%; padding: 32px 0; }}
      .container {{ max-width: 760px; margin: 0 auto; background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 8px 30px rgba(15, 23, 42, 0.08); }}
      .hero {{ padding: 32px 32px 22px; background: linear-gradient(135deg, #0f172a, #1d4ed8); color: #ffffff; }}
      .hero h1 {{ margin: 0; font-size: 27px; line-height: 1.25; color: #ffffff; }}
      .hero p {{ margin: 10px 0 0; opacity: 0.9; font-size: 14px; }}
      .content {{ padding: 24px 24px 12px; }}
      .trends {{ margin-bottom: 24px; padding: 16px 18px; background: #eff6ff; border-radius: 14px; }}
      .trends h2 {{ margin: 0 0 10px; font-size: 16px; color: #0f172a; }}
      .trends ul {{ margin: 0; padding-left: 20px; }}
      .trends li {{ margin: 3px 0; color: #334155; }}
      table {{ width: 100%; border-collapse: collapse; }}
      .card {{ padding: 18px; border: 1px solid #e2e8f0; border-radius: 14px; background: #ffffff; }}
      .card-gap {{ height: 14px; line-height: 14px; font-size: 0; }}
      .meta {{ margin-bottom: 10px; }}
      .badge {{ display: inline-block; background: #dbeafe; color: #1d4ed8; font-weight: 700; font-size: 12px; padding: 4px 9px; border-radius: 999px; }}
      .score {{ font-size: 12px; color: #64748b; font-weight: 700; float: right; padding-top: 4px; }}
      h3 {{ margin: 0 0 8px; font-size: 19px; line-height: 1.3; }}
      h3 a {{ color: #0f172a; text-decoration: none; }}
      .source {{ margin: 0 0 12px; color: #64748b; font-size: 13px; }}
      .summary {{ margin: 0; color: #334155; line-height: 1.6; font-size: 15px; }}
      .why {{ margin: 10px 0 0; padding: 10px 12px; background: #f8fafc; border-left: 3px solid #1d4ed8; border-radius: 8px; color: #475569; font-size: 13.5px; line-height: 1.55; }}
      .why-label {{ font-weight: 700; color: #1d4ed8; }}
      .footer {{ padding: 18px 24px 28px; color: #64748b; font-size: 12px; text-align: center; }}
      @media (max-width: 640px) {{
        .wrap {{ padding: 12px 0; }}
        .hero {{ padding: 24px 18px 18px; }}
        .hero h1 {{ font-size: 23px; }}
        .content {{ padding: 18px 14px 8px; }}
        .container {{ border-radius: 12px; }}
      }}
      @media (prefers-color-scheme: dark) {{
        body, .wrap {{ background: #0e1421 !important; }}
        body {{ color: #e6ebf3 !important; }}
        .container {{ background: #151c2a !important; box-shadow: none !important; }}
        .trends {{ background: #1a2740 !important; }}
        .trends h2 {{ color: #e6ebf3 !important; }}
        .trends li {{ color: #c3cddb !important; }}
        .card {{ background: #1a2130 !important; border-color: #2a3344 !important; }}
        h3 a {{ color: #f0f4fa !important; }}
        .source {{ color: #93a1b4 !important; }}
        .score {{ color: #93a1b4 !important; }}
        .summary {{ color: #c3cddb !important; }}
        .why {{ background: #131c2b !important; border-left-color: #3b6fe0 !important; color: #aab6c8 !important; }}
        .why-label {{ color: #9fbcff !important; }}
        .badge {{ background: #23345a !important; color: #9fbcff !important; }}
        .footer {{ color: #7d8aa0 !important; }}
      }}
    </style>
  </head>
  <body>
    <div class="preheader">{preheader}</div>
    <div class="wrap">
      <div class="container">
        <div class="hero">
          <h1>{esc(draft.headline or 'Resumen diario')}</h1>
          <p>Selección de lo más relevante de las últimas 24 horas.</p>
        </div>
        <div class="content">
          {trend_html}
          <table role="presentation">
            <tbody>
              {cards_html}
            </tbody>
          </table>
        </div>
        <div class="footer">Generado automáticamente por newsletter-diaria</div>
      </div>
    </div>
  </body>
</html>"""
