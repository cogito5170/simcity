"""Build a monitoring page for one run: growth video, charts, yearly maps, decisions and their outcomes.

Writes into the run directory:
  index.html     full page, open it in any browser (no install, works offline except web fonts)
  artifact.html  the same page as a fragment, for publishing as a claude.ai Artifact
  video.mp4, poster.png, keyframes/*.png
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path

import numpy as np

from .render import render_run

METRIC_KO = {"population": "인구", "funds": "자금", "crime_avg": "범죄", "pollution_avg": "오염",
             "traffic_avg": "교통", "land_value_avg": "지가", "city_score": "도시 점수"}
DIR_KO = {"up": "증가", "down": "감소", "flat": "유지"}
STRAT_KO = {"tax_rate": "세율(%)", "reserve_funds": "예비 자금", "zones_per_police": "경찰서 1곳당 구역",
            "zones_per_fire": "소방서 1곳당 구역", "max_builds_per_month": "월 건설 한도", "power_type": "발전소 종류",
            "build_specials": "경기장·항구·공항 자동 건설", "land_use": "토지 이용", "zone_mix": "구역 비율 목표"}
BUILD_KO = {"place:residential": "주거 구역", "place:commercial": "상업 구역", "place:industrial": "공업 구역",
            "place:police": "경찰서", "place:fire": "소방서", "power:coal": "석탄 발전소", "power:nuclear": "원자력 발전소",
            "special:stadium": "경기장", "special:seaport": "항구", "special:airport": "공항"}
FPS = 8


class _Run:
    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        self.months = [json.loads(line) for line in (run_dir / "months.jsonl").read_text(encoding="utf-8").splitlines()]
        rp = run_dir / "reviews.jsonl"
        self.reviews = [json.loads(line) for line in rp.read_text(encoding="utf-8").splitlines()] if rp.exists() else []
        self.summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
        self.tiles = list(np.load(run_dir / "tiles.npz")["tiles"])


def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, t = [], start
    while t <= hi + step * 0.001:
        ticks.append(round(t, 6))
        t += step
    return ticks


def _fmt(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v / 1000:g}k"
    return f"{v:g}"


def svg_chart(months: list[dict], series: list[tuple[str, list[float], str]], review_idx: list[int],
              title: str, unit: str = "", height: int = 190, zero_line: bool = False) -> str:
    """Line chart, one shared y scale. series = (label, values, css class)."""
    W, H, L, R, T, B = 640, height, 46, 12, 12, 26
    n = len(months)
    vals = [v for _, s, _ in series for v in s]
    lo, hi = min(vals + [0]), max(vals + [1])
    ticks = _nice_ticks(lo, hi)
    lo, hi = min(ticks[0], lo), max(ticks[-1], hi)
    x = lambda i: L + (W - L - R) * i / max(n - 1, 1)
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(title)}">']
    for t in ticks:
        out.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                   f'<text class="tick" x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{_fmt(t)}</text>')
    if zero_line and lo < 0 < hi:
        out.append(f'<line class="zero" x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    years = sorted({i for i, m in enumerate(months) if m["date"].endswith("-01")})
    step = max(1, math.ceil(len(years) / 6))
    for i in years[::step]:
        out.append(f'<text class="tick" x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">{months[i]["date"][:4]}</text>')
    for i in review_idx:
        out.append(f'<line class="review" x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{T}" y2="{H - B}"/>')
    for label, s, cls in series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s))
        if len(series) == 1:
            out.append(f'<polygon class="area {cls}" points="{x(0):.1f},{y(max(lo, 0)):.1f} {pts} '
                       f'{x(n - 1):.1f},{y(max(lo, 0)):.1f}"/>')
        out.append(f'<polyline class="line {cls}" points="{pts}"/>')
        out.append(f'<circle class="dot {cls}" cx="{x(n - 1):.1f}" cy="{y(s[-1]):.1f}" r="3.5"/>')
    out.append("</svg>")
    legend = "".join(f'<span class="key"><i class="sw {cls}"></i>{html.escape(lbl)}</span>' for lbl, _, cls in series)
    return (f'<figure class="chart"><figcaption><b>{html.escape(title)}</b>'
            f'<span class="unit">{html.escape(unit)}</span>{legend}</figcaption>{"".join(out)}</figure>')


def _strategy_changes(prev: dict | None, cur: dict) -> list[str]:
    out = []
    for k, label in STRAT_KO.items():
        a, b = (prev or {}).get(k), cur.get(k)
        if prev is None or a != b:
            if k == "zone_mix":
                b = " / ".join(f"{z[0].upper()} {v:.0%}" for z, v in b.items())
                a = " / ".join(f"{z[0].upper()} {v:.0%}" for z, v in a.items()) if a else None
            if isinstance(b, bool):
                b = "켬" if b else "끔"
                a = None if a is None else ("켬" if a else "끔")
            if k == "land_use":
                b = {"mixed": "혼합", "separated": "공업 분리"}.get(b, b)
                a = {"mixed": "혼합", "separated": "공업 분리"}.get(a, a) if a else None
            out.append(f"{label}: {a} → {b}" if prev is not None else f"{label}: {b}")
    return out


def _review_cards(run: _Run) -> str:
    idx_by_date = {m["date"]: i for i, m in enumerate(run.months)}
    cards, prev = [], None
    for rv in run.reviews:
        i = idx_by_date.get(rv["date"], 0)
        changes = _strategy_changes(prev, rv["strategy"])
        prev = rv["strategy"]
        ev = rv.get("evaluation_of_previous") or {}
        ev_rows = "".join(
            f'<tr><td>{METRIC_KO.get(r["metric"], r["metric"])}</td><td>{DIR_KO[r["expected"]]}</td>'
            f'<td>{DIR_KO[r["actual"]]}</td><td class="num">{r["before"]:,.0f} → {r["after"]:,.0f}</td>'
            f'<td><span class="pill {"hit" if r["hit"] else "miss"}">{"적중" if r["hit"] else "빗나감"}</span></td></tr>'
            for r in ev.get("rows", []))
        ev_html = (f'<details><summary>직전 판단의 예측 검증 · 적중률 {ev["score"]:.0%}</summary>'
                   f'<div class="scroll"><table><thead><tr><th>지표</th><th>예측</th><th>실제</th><th>변화</th><th></th></tr>'
                   f'</thead><tbody>{ev_rows}</tbody></table></div></details>') if ev_rows else ""
        exp = ", ".join(f"{METRIC_KO.get(k, k)} {DIR_KO.get(v, v)}" for k, v in rv.get("expected", {}).items())
        src = rv["source"]
        model = (rv.get("usage") or {}).get("model")
        src_label = ("규칙 기반" if src == "rules" else "LLM 실패 → 규칙" if "failed" in src
                     else f"Claude Code · {model}" if "claude-code" in src else f"LLM · {model or src}")
        cards.append(f'''
<article class="review" id="r-{html.escape(rv["date"])}">
  <header><time>{rv["date"][:4]}년 {int(rv["date"][5:]):d}월</time><span class="src">{html.escape(src_label)}</span>
    <button class="seek" type="button" data-t="{i / FPS:.2f}">영상에서 보기</button></header>
  <ul class="why">{"".join(f"<li>{html.escape(w)}</li>" for w in rv.get("reasoning", []))}</ul>
  {f'<p class="changes">{" · ".join(html.escape(c) for c in changes)}</p>' if changes else ""}
  {f'<p class="expect">예상: {html.escape(exp)}</p>' if exp else ""}
  {"".join(f'<p class="unsure">불확실: {html.escape(u)}</p>' for u in rv.get("uncertainties", []))}
  {"".join(f'<p class="note">{html.escape(n)}</p>' for n in rv.get("notes", []))}
  {ev_html}
</article>''')
    return "\n".join(cards)


CSS = """
/* Layout: one reading column; video first, then charts in a two-up grid, then the decision log. */
:root {
  --paper: #eef1f3; --sheet: #ffffff; --ink: #18222d; --muted: #5b6875; --rule: #d3dae0;
  --accent: #1f6f8b; --res: #2f9e44; --com: #2f6fd1; --ind: #c48a06; --bad: #c2410c; --good: #2f9e44;
  --font-body: "IBM Plex Sans KR", "Malgun Gothic", "Apple SD Gothic Neo", system-ui, sans-serif;
  --font-data: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #11161c; --sheet: #182029; --ink: #e3e9ee; --muted: #93a1ae; --rule: #2a3542;
  --accent: #5fb3cf; --res: #5ccf72; --com: #6ea3f0; --ind: #e3b23c; --bad: #f08a4b; --good: #5ccf72; color-scheme: dark } }
:root[data-theme="dark"] {
  --paper: #11161c; --sheet: #182029; --ink: #e3e9ee; --muted: #93a1ae; --rule: #2a3542;
  --accent: #5fb3cf; --res: #5ccf72; --com: #6ea3f0; --ind: #e3b23c; --bad: #f08a4b; --good: #5ccf72; color-scheme: dark }
body { background: var(--paper); color: var(--ink); font-family: var(--font-body); line-height: 1.6; }
.wrap { max-width: 980px; margin: 0 auto; padding-inline: 16px; padding-block: 28px 64px; display: grid; gap: 28px; }
h1 { font-size: clamp(1.6rem, 4vw, 2.3rem); line-height: 1.2; margin: 0; text-wrap: balance; letter-spacing: -0.01em; }
h2 { font-size: 1.15rem; margin: 0 0 12px; text-wrap: balance; }
.eyebrow { font-family: var(--font-data); font-size: 0.75rem; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }
.lede { color: var(--muted); max-width: 65ch; margin: 6px 0 0; }
.zones { display: flex; gap: 14px; flex-wrap: wrap; font-size: 0.85rem; color: var(--muted); margin-top: 10px; }
.zones span::before { content: ""; display: inline-block; width: 10px; height: 10px; margin-right: 6px; border-radius: 2px; vertical-align: -1px; }
.zones .r::before { background: var(--res); } .zones .c::before { background: var(--com); } .zones .i::before { background: var(--ind); }
.player { background: var(--sheet); border: 1px solid var(--rule); border-radius: 10px; padding: 12px; display: grid; gap: 10px; }
video, .player img { width: 100%; height: auto; border-radius: 6px; background: #000; image-rendering: pixelated; display: block; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.kpi { background: var(--sheet); border: 1px solid var(--rule); border-radius: 8px; padding: 10px 12px; min-width: 0; }
.kpi b { display: block; font-family: var(--font-data); font-size: 1.35rem; font-variant-numeric: tabular-nums; }
.kpi span { font-size: 0.8rem; color: var(--muted); }
.charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.chart { margin: 0; background: var(--sheet); border: 1px solid var(--rule); border-radius: 8px; padding: 10px 10px 4px; min-width: 0; }
.chart figcaption { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; font-size: 0.85rem; }
.chart .unit { color: var(--muted); font-size: 0.75rem; }
.key { color: var(--muted); font-size: 0.78rem; } .sw { display: inline-block; width: 12px; height: 3px; margin-right: 5px; vertical-align: middle; }
.chart svg { width: 100%; height: auto; display: block; }
.grid { stroke: var(--rule); stroke-width: 1; } .zero { stroke: var(--muted); stroke-width: 1; }
.review { stroke: var(--accent); stroke-width: 1; stroke-dasharray: 3 3; opacity: 0.55; }
.tick { fill: var(--muted); font: 11px var(--font-data); }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; } .area { stroke: none; opacity: 0.12; }
.s-pop { stroke: var(--accent); fill: var(--accent); background: var(--accent); }
.s-funds { stroke: var(--ink); fill: var(--ink); background: var(--ink); }
.s-res { stroke: var(--res); fill: var(--res); background: var(--res); }
.s-com { stroke: var(--com); fill: var(--com); background: var(--com); }
.s-ind { stroke: var(--ind); fill: var(--ind); background: var(--ind); }
.s-bad { stroke: var(--bad); fill: var(--bad); background: var(--bad); }
polyline.s-pop, polyline.s-funds, polyline.s-res, polyline.s-com, polyline.s-ind, polyline.s-bad { fill: none; }
.years { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
.years figure { margin: 0; background: var(--sheet); border: 1px solid var(--rule); border-radius: 6px; overflow: hidden; }
.years img { width: 100%; display: block; image-rendering: pixelated; }
.years figcaption { font-family: var(--font-data); font-size: 0.75rem; padding: 4px 8px; color: var(--muted); font-variant-numeric: tabular-nums; }
.log { display: grid; gap: 10px; }
article.review { background: var(--sheet); border: 1px solid var(--rule); border-radius: 8px; padding: 12px 14px; min-width: 0; }
article.review header { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; }
article.review time { font-family: var(--font-data); font-weight: 600; }
.src { font-size: 0.75rem; color: var(--muted); border: 1px solid var(--rule); border-radius: 99px; padding: 1px 8px; }
.seek { margin-left: auto; font: inherit; font-size: 0.8rem; color: var(--accent); background: none; border: 1px solid var(--accent); border-radius: 6px; padding: 2px 10px; cursor: pointer; }
.seek:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.why { margin: 8px 0 4px; padding-left: 1.2em; }
.changes, .expect { font-size: 0.85rem; margin: 4px 0; } .changes { font-family: var(--font-data); }
.unsure, .note { font-size: 0.82rem; color: var(--muted); margin: 2px 0; }
details { margin-top: 6px; font-size: 0.85rem; } summary { cursor: pointer; color: var(--accent); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.82rem; margin-top: 6px; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--rule); white-space: nowrap; }
.num { font-family: var(--font-data); font-variant-numeric: tabular-nums; }
.pill { font-size: 0.72rem; border-radius: 99px; padding: 1px 8px; border: 1px solid currentColor; }
.pill.hit { color: var(--good); } .pill.miss { color: var(--bad); }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.facts section { background: var(--sheet); border: 1px solid var(--rule); border-radius: 8px; padding: 12px 14px; min-width: 0; }
.facts h3 { margin: 0 0 6px; font-size: 0.95rem; } .facts ul { margin: 0; padding-left: 1.2em; font-size: 0.88rem; }
.built { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 0.88rem; }
.built b { font-family: var(--font-data); }
footer { font-size: 0.8rem; color: var(--muted); }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""

JS = """
document.querySelectorAll('.seek').forEach(function (b) {
  b.addEventListener('click', function () {
    var v = document.getElementById('city-video');
    if (!v) return;
    v.currentTime = parseFloat(b.dataset.t);
    v.scrollIntoView({ block: 'center' });
    v.play().catch(function () {});
  });
});
"""


def build_page(run: _Run, media: dict) -> tuple[str, str]:
    months, reviews, summ = run.months, run.reviews, run.summary
    idx_by_date = {m["date"]: i for i, m in enumerate(months)}
    review_idx = [idx_by_date[r["date"]] for r in reviews if r["date"] in idx_by_date]
    col = lambda k: [m["stats"][k] for m in months]
    pops = col("population")
    peak_i = int(np.argmax(pops))
    scores = [r["evaluation_of_previous"]["score"] for r in reviews
              if r.get("evaluation_of_previous") and r["evaluation_of_previous"].get("score") is not None]
    built: dict[str, int] = {}
    skipped = failed = 0
    for m in months:
        for a in m["actions"]:
            if a["status"] == "done":
                built[a["action"]] = built.get(a["action"], 0) + 1
            skipped += a["status"] == "skipped"
            failed += a["status"] == "failed"
    reviewer = summ.get("reviewer", "rules")
    models = sorted({(r.get("usage") or {}).get("model") for r in reviews} - {None})
    if reviewer == "rules":
        reviewer_ko = "규칙 기반 시장 (LLM 없이, 비용 0)"
    elif "claude-code" in reviewer:
        reviewer_ko = f"Claude Code 시장 ({', '.join(models) or 'claude -p'})"
    else:
        reviewer_ko = f"LLM 시장 ({', '.join(models) or reviewer})"
    fallbacks = sum(1 for r in reviews if "failed" in r["source"])
    first, last = months[0]["date"], months[-1]["date"]

    charts = "".join([
        svg_chart(months, [("인구", pops, "s-pop")], review_idx, "인구", "명"),
        svg_chart(months, [("자금", col("funds"), "s-funds")], review_idx, "자금", "$ (세금은 매년 1월 정산)"),
        svg_chart(months, [("주거", [v / 2000 for v in col("res_valve")], "s-res"),
                           ("상업", [v / 1500 for v in col("com_valve")], "s-com"),
                           ("공업", [v / 1500 for v in col("ind_valve")], "s-ind")],
                  review_idx, "R/C/I 수요", "-1 ~ 1, 엔진 값", zero_line=True),
        svg_chart(months, [("범죄", col("crime_avg"), "s-bad"), ("오염", col("pollution_avg"), "s-ind"),
                           ("교통", col("traffic_avg"), "s-com")], review_idx, "도시 문제", "엔진 평균 지수"),
        svg_chart(months, [("세율", [float(m["tax_rate"]) for m in months], "s-pop")], review_idx, "세율", "%", height=150),
        svg_chart(months, [("지가", col("land_value_avg"), "s-res")], review_idx, "평균 지가", "엔진 지수", height=150),
    ])
    video = (f'<video id="city-video" controls muted playsinline preload="metadata" poster="{media["poster"]}" '
             f'src="{media["video"]}"></video>') if media.get("video") else f'<img src="{media["poster"]}" alt="최종 도시 지도">'
    years = "".join(f'<figure><img loading="lazy" src="{k["file"]}" alt="{k["date"]} 도시 지도">'
                    f'<figcaption>{k["date"]} · 인구 {months[idx_by_date[k["date"]]]["stats"]["population"]:,.0f}</figcaption></figure>'
                    for k in media["keyframes"])
    built_html = "".join(f'<span>{BUILD_KO.get(k, k)} <b>{v}</b></span>' for k, v in sorted(built.items(), key=lambda x: -x[1]))
    hit = f"{np.mean(scores):.0%}" if scores else "—"
    body = f"""
<div class="wrap">
  <header>
    <div class="eyebrow">Micropolis · seed {summ.get("seed")} · {first} ~ {last}</div>
    <h1>AI 시장의 도시 운영 기록</h1>
    <p class="lede">{html.escape(reviewer_ko)}이 {summ.get("years")}년 동안 도시를 운영한 기록입니다.
      화면은 한 달에 한 장씩 그린 실제 게임 지도이고, 숫자는 모두 시뮬레이션 엔진이 보고한 정확한 값입니다.</p>
    <div class="zones"><span class="r">주거(R)</span><span class="c">상업(C)</span><span class="i">공업(I)</span>
      <span>점선 = 전략 재검토 시점</span></div>
  </header>
  <section class="player" aria-label="도시 성장 영상">{video}</section>
  <section class="kpis">
    <div class="kpi"><b>{pops[-1]:,.0f}</b><span>최종 인구</span></div>
    <div class="kpi"><b>{pops[peak_i]:,.0f}</b><span>최고 인구 ({months[peak_i]["date"]})</span></div>
    <div class="kpi"><b>${months[-1]["stats"]["funds"]:,.0f}</b><span>최종 자금</span></div>
    <div class="kpi"><b>{len(reviews)}</b><span>전략 재검토 횟수</span></div>
    <div class="kpi"><b>{hit}</b><span>예측 적중률 (재검토 평균)</span></div>
    {f'<div class="kpi"><b>{fallbacks}</b><span>LLM 실패로 규칙 대체</span></div>' if fallbacks else ""}
  </section>
  <section><h2>지표 변화</h2><div class="charts">{charts}</div></section>
  <section><h2>매년 1월의 도시</h2><div class="years">{years}</div></section>
  <section><h2>무엇을 지었나</h2><div class="built">{built_html}</div>
    <p class="lede">돈이 예비 자금 아래로 내려가 건설을 미룬 경우 {skipped}회, 실제로 실패한 건설 {failed}회.</p></section>
  <section><h2>시장의 판단 기록</h2>
    <p class="lede">재검토 때마다 무엇을 왜 바꿨는지, 무엇을 예상했는지, 무엇을 모르는지를 남깁니다.
      다음 재검토에서 그 예상을 실제 결과와 비교해 채점합니다.</p>
    <div class="log">{_review_cards(run)}</div></section>
  <section class="facts">
    <section><h3>엔진으로 확인한 규칙</h3><ul>
      <li>도로는 전기를 전달하지 않고, 맞닿은 구역끼리 전기를 전달합니다.</li>
      <li>주민 500명(엔진 단위) 이상인데 경기장이 없으면 주거 수요가 0에 묶입니다. 공업은 항구, 상업은 공항이 같은 역할을 합니다.</li>
      <li>세금은 매년 1월에 한 번 정산됩니다.</li></ul></section>
    <section><h3>아직 모르는 것</h3><ul>
      <li>인구가 정점을 찍고 줄어드는 주기의 정확한 원인 (범죄, 지가 하락, 자금 부족 중 어느 것이 큰지)</li>
      <li>공업 분리 배치가 나은지: 고정 전략 시험 8회에서 정점은 높았지만 최종 인구는 낮았고 오염은 오히려 높았습니다. 이유는 아직 모릅니다.</li>
      <li>도로 배치 자체는 고정된 격자라 시장이 바꿀 수 없습니다.</li></ul></section>
  </section>
  <footer>Micropolis(SimCity Classic 엔진, GPL-3.0)를 화면 없이 실행해 만든 기록입니다.
    원본 데이터: months.jsonl, reviews.jsonl, saves/final.cty</footer>
</div>
<script>{JS}</script>"""
    head = (f"<title>AI 시장의 도시 운영 기록</title>\n"
            '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
            '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600'
            '&family=IBM+Plex+Sans+KR:wght@400;600;700&display=swap">\n'
            f"<style>{CSS}</style>")
    fragment = head + "\n" + body
    full = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            + head + "\n</head>\n<body>" + body + "\n</body>\n</html>\n")
    return full, fragment


def make_report(run_dir: str | Path) -> dict:
    run_dir = Path(run_dir)
    run = _Run(run_dir)
    media = render_run(run, run_dir, fps=FPS)
    full, fragment = build_page(run, media)
    (run_dir / "index.html").write_text(full, encoding="utf-8")
    (run_dir / "artifact.html").write_text(fragment, encoding="utf-8")
    return {"index": run_dir / "index.html", "artifact": run_dir / "artifact.html", **media}
