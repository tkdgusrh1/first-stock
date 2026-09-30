"""용어 사전 — 화면의 지표가 무엇이고, 어떻게 계산했고, 무엇을 조심해야 하는지."""

from __future__ import annotations

from ..glossary import groups
from .kit import esc, page_head


def render(ctx) -> str:
    all_groups = groups()
    total = sum(len(v) for v in all_groups.values())
    search = ('<div class="search" style="max-width:none;margin:0 0 8px" data-glossary-filter>'
              '<input type="search" placeholder="용어 찾기 (예: ROIC, 런웨이, 희석)" aria-label="용어 찾기"></div>')
    blocks = []
    for group, terms in all_groups.items():
        items = []
        for entry in terms:
            rows = [f'<p class="g-row" style="color:var(--text)">{esc(entry.short)}</p>']
            if entry.formula:
                rows.append(f'<p class="g-row"><span class="g-key">계산</span> <code>{esc(entry.formula)}</code></p>')
            if entry.how_to_read:
                rows.append(f'<p class="g-row"><span class="g-key">읽는 법</span> {esc(entry.how_to_read)}</p>')
            if entry.caution:
                rows.append(f'<p class="g-row caution"><span class="g-key">주의</span> {esc(entry.caution)}</p>')
            if entry.source:
                rows.append(f'<p class="g-row"><span class="g-key">출처</span> {esc(entry.source)}</p>')
            text = " ".join([entry.name, entry.short, entry.formula or "", entry.how_to_read or ""]).lower()
            items.append(f'<div class="card g-item" id="term-{esc(entry.key)}" data-text="{esc(text)}">'
                         f'<h4>{esc(entry.name)}</h4>{"".join(rows)}</div>')
        blocks.append(f'<section data-group><h2 class="g-group-title">{esc(group)} '
                      f'<span class="muted small">{len(terms)}개</span></h2>'
                      f'<div class="g-items">{"".join(items)}</div></section>')
    head = page_head("용어 사전", f"{total}개 · 표 머리글에 점선 밑줄이 있는 말을 누르면 여기로 옵니다")
    return head + search + "".join(blocks)


__all__ = ["render"]
