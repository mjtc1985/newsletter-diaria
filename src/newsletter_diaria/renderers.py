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
        print(f"{ranked.rank}. {item.title}  [{ranked.importance}/100]")
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
        lines.extend(
            [
                f"{ranked.rank}. **{item.title}**  _[{ranked.importance}/100]_",
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
    lines = [draft.headline or "Daily roundup", ""]
    if draft.trends:
        lines.append("Trends:")
        lines.extend(f"- {trend}" for trend in draft.trends)
        lines.append("")

    for ranked in draft.items:
        item = ranked.item
        when = item.published_at.isoformat() if item.published_at else "no date"
        lines.extend(
            [
                f"{ranked.rank}. {item.title} [{ranked.importance}/100]",
                f"Source: {item.source}",
                f"Date: {when}",
                f"Link: {item.link}",
                f"Summary: {ranked.summary or '—'}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def render_email_html(draft: NewsletterDraft) -> str:
    def esc(value: str) -> str:
        return html.escape(value or "")

    def fmt_dt(item: Item) -> str:
        return item.published_at.isoformat() if item.published_at else "no date"

    trend_html = ""
    if draft.trends:
        trend_html = """
        <div class="trends">
          <h2>Trends</h2>
          <ul>
            {trends}
          </ul>
        </div>
        """.format(trends="".join(f"<li>{esc(trend)}</li>" for trend in draft.trends))

    cards_html = "".join(
        f"""
            <tr>
              <td class="card">
                <div class="meta">
                  <span class="badge">#{ranked.rank}</span>
                  <span class="score">{ranked.importance}/100</span>
                </div>
                <h3><a href="{esc(ranked.item.link)}">{esc(ranked.item.title)}</a></h3>
                <p class="source">{esc(ranked.item.source)} · {esc(fmt_dt(ranked.item))}</p>
                <p class="summary">{esc(ranked.summary or ranked.item.summary or '—')}</p>
              </td>
            </tr>
        """
        for ranked in draft.items
    )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{esc(draft.headline or 'Daily roundup')}</title>
    <style>
      body {{ margin: 0; padding: 0; background: #f4f7fb; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #0f172a; }}
      .wrap {{ width: 100%; padding: 32px 0; }}
      .container {{ max-width: 760px; margin: 0 auto; background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 8px 30px rgba(15, 23, 42, 0.08); }}
      .hero {{ padding: 32px 32px 20px; background: linear-gradient(135deg, #0f172a, #1d4ed8); color: #fff; }}
      .hero h1 {{ margin: 0; font-size: 28px; line-height: 1.2; }}
      .hero p {{ margin: 10px 0 0; opacity: 0.9; }}
      .content {{ padding: 24px 24px 12px; }}
      .trends {{ margin-bottom: 24px; padding: 18px; background: #eff6ff; border-radius: 14px; }}
      .trends h2 {{ margin: 0 0 10px; font-size: 18px; }}
      .trends ul {{ margin: 0; padding-left: 20px; }}
      table {{ width: 100%; border-collapse: collapse; }}
      .card {{ padding: 18px; border: 1px solid #e2e8f0; border-radius: 14px; margin-bottom: 14px; background: #fff; }}
      .meta {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
      .badge {{ display: inline-block; background: #dbeafe; color: #1d4ed8; font-weight: 700; font-size: 12px; padding: 4px 8px; border-radius: 999px; }}
      .score {{ font-size: 12px; color: #64748b; font-weight: 700; }}
      h3 {{ margin: 0 0 8px; font-size: 20px; line-height: 1.25; }}
      h3 a {{ color: #0f172a; text-decoration: none; }}
      .source {{ margin: 0 0 12px; color: #64748b; font-size: 13px; }}
      .summary {{ margin: 0 0 14px; color: #334155; line-height: 1.55; }}
      .footer {{ padding: 18px 24px 28px; color: #64748b; font-size: 12px; text-align: center; }}
      @media (max-width: 640px) {{
        .hero, .content {{ padding-left: 16px; padding-right: 16px; }}
      }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="container">
        <div class="hero">
          <h1>{esc(draft.headline or 'Daily roundup')}</h1>
          <p>A curated selection of the most relevant stories from the last 24 hours.</p>
        </div>
        <div class="content">
          {trend_html}
          <table role="presentation">
            <tbody>
              {cards_html}
            </tbody>
          </table>
        </div>
        <div class="footer">Generated automatically by newsletter-diaria</div>
      </div>
    </div>
  </body>
</html>"""
