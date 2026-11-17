#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[달의 검 — GitHub Actions 무서버 클라우드 백업 업데이터 (cloud_isi_updater.py)]
- 집 PC(kr_trader.py)가 켜져 있어 최근 3분 이내에 live_state.json을 갱신 중이면 자동 양보(Skip)
- 집 PC가 꺼져 있거나 절전 상태일 때 네이버 증권 공개 API에서 ISI 거래대금 TOP 3 주도테마 및
  15:19 종가베팅 1등 대장주를 독립 연산하여 live_state.json + EMQX 웹소켓을 자동 업데이트!
- 기존 키움 계좌 보유상태(overnight_holding) 및 실전 체결 성적표(stats, trades)는 100% 그대로 보존!
"""
import datetime
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import requests

KST_TZ = ZoneInfo("Asia/Seoul")
STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live_state.json")
BROKER_HOST = "broker.emqx.io"
BROKER_PORT = 1883
TOPIC_ISI = "kr_whale_detector_vip_family_2026/isi"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}


def normalize_kr_code(code: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(code or "")).upper()[:6]


def _parse_int(val_str) -> int:
    if val_str is None:
        return 0
    s = str(val_str).replace(",", "").replace("+", "").strip()
    if not s or s == "-":
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0


def _parse_float(val_str) -> float:
    if val_str is None:
        return 0.0
    s = str(val_str).replace(",", "").replace("%", "").strip()
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def is_valid_stock(code: str, name: str, close_p: int, money_eok: int) -> bool:
    if close_p < 2000:
        return False
    if name.endswith("우") or "우B" in name or "우C" in name:
        return False
    if "스팩" in name or "ETN" in name or "ETF" in name:
        return False
    if money_eok < 30:
        return False
    return True


def fetch_real_ohlcv(code: str, count: int = 7) -> list:
    if not code:
        return []
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content.decode("euc-kr", errors="ignore"))
        candles = []
        for item in root.findall(".//item"):
            parts = item.attrib.get("data", "").split("|")
            if len(parts) >= 6:
                dt, op, hi, lo, cl, vol = parts[:6]
                cl_i = _parse_int(cl)
                op_i = _parse_int(op) or cl_i
                hi_i = _parse_int(hi) or cl_i
                lo_i = _parse_int(lo) or cl_i
                vol_i = _parse_int(vol)
                if cl_i > 0:
                    candles.append({
                        "date": dt,
                        "open": op_i,
                        "high": hi_i,
                        "low": lo_i,
                        "close": cl_i,
                        "volume": vol_i,
                    })
        return candles
    except Exception:
        return []


def fetch_investor_net_buy_eok(code: str, days: int = 1) -> int:
    url = f"https://m.stock.naver.com/api/stock/{code}/trend"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return 0
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            return 0
        total_won = 0
        for row in rows[:days]:
            close_p = _parse_int(row.get("closePrice"))
            f_qty = _parse_int(row.get("foreignerPureBuyQuant"))
            o_qty = _parse_int(row.get("organPureBuyQuant"))
            total_won += (f_qty + o_qty) * close_p
        return int(round(total_won / 100_000_000))
    except Exception:
        return 0


def crawl_naver_top_themes(top_n: int = 25) -> list:
    url = f"https://m.stock.naver.com/api/stocks/theme?page=1&pageSize={top_n}"
    res = requests.get(url, headers=HEADERS, timeout=10)
    res.raise_for_status()
    groups = [g for g in res.json().get("groups", [])[:top_n] if g.get("no") and str(g.get("name", "")).strip()]

    def _fetch_one(g):
        t_no = g.get("no")
        t_name = str(g.get("name", "")).strip()
        detail_url = f"https://m.stock.naver.com/api/stocks/theme/{t_no}?pageSize=20&page=1"
        d_res = requests.get(detail_url, headers=HEADERS, timeout=10)
        stocks_raw = d_res.json().get("stocks", []) if d_res.status_code == 200 else []
        return {"theme_no": t_no, "theme_name": t_name, "stocks": stocks_raw}

    with ThreadPoolExecutor(max_workers=10) as ex:
        return list(ex.map(_fetch_one, groups))


def extract_isi_top3_themes() -> tuple:
    """네이버 증권 테마·일봉·외인기관 수급 API로 ISI 공식 TOP 3 주도테마 및 1등 대장주 산출"""
    themes = crawl_naver_top_themes(top_n=25)
    latest_date_iso = datetime.datetime.now(KST_TZ).strftime("%Y-%m-%d")

    prelim_themes = []
    for t in themes:
        valid_rows = []
        for s in t["stocks"][:15]:
            code = normalize_kr_code(s.get("itemCode", ""))
            name = str(s.get("stockName", "")).strip()
            close_p = _parse_int(s.get("closePrice"))
            rate = round(_parse_float(s.get("fluctuationsRatio")), 1)
            vol = _parse_int(s.get("accumulatedTradingVolume"))
            val_million = _parse_int(s.get("accumulatedTradingValue"))
            money_eok = int(round(val_million / 100.0))
            traded_at = str(s.get("localTradedAt", ""))
            if len(traded_at) >= 10:
                latest_date_iso = traded_at[:10]
            if vol <= 0 or not is_valid_stock(code, name, close_p, money_eok):
                continue
            val_won = val_million * 1_000_000
            vwap = int(val_won / vol) if val_won > 0 and vol > 0 else close_p
            valid_rows.append({
                "code": code,
                "name": name,
                "close": close_p,
                "rate": rate,
                "money_eok": money_eok,
                "vwap": vwap,
            })
        if len(valid_rows) < 3:
            continue
        valid_rows.sort(key=lambda x: x["money_eok"], reverse=True)
        top3_pre = valid_rows[:3]
        theme_money = sum(x["money_eok"] for x in top3_pre)
        leader_pre = max(top3_pre, key=lambda x: x["rate"])
        prelim_themes.append({
            "theme_name": t["theme_name"],
            "theme_money": theme_money,
            "leader_rate": leader_pre["rate"],
            "valid_rows": valid_rows,
        })

    if not prelim_themes:
        return latest_date_iso, []

    prelim_themes.sort(key=lambda x: x["theme_money"], reverse=True)
    surging_pre = [pt for pt in prelim_themes if pt["leader_rate"] >= 5.0 and pt["theme_money"] >= 800]
    target_themes = list(prelim_themes[:5])
    for sp in surging_pre[:5]:
        if sp not in target_themes:
            target_themes.append(sp)

    need_codes = []
    seen_c = set()
    for pt in target_themes:
        for r in pt["valid_rows"]:
            if r["code"] not in seen_c:
                seen_c.add(r["code"])
                need_codes.append(r["code"])

    def _fetch_extra(cd):
        return cd, fetch_real_ohlcv(cd, count=7), fetch_investor_net_buy_eok(cd, days=1)

    extra_cache = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for cd, cands, pm in ex.map(_fetch_extra, need_codes):
            extra_cache[cd] = (cands, pm)

    theme_results = []
    for pt in target_themes:
        stock_metrics = []
        for r in pt["valid_rows"]:
            code = r["code"]
            close_p = r["close"]
            candles, prog_money = extra_cache.get(code, ([], 0))
            if len(candles) >= 2:
                last_c = candles[-1]
                prev_vols = [c["volume"] for c in candles[:-1] if c["volume"] > 0]
                avg_vol_5d = max(1, int(sum(prev_vols) / max(1, len(prev_vols))))
                vol_ratio = int((last_c["volume"] / avg_vol_5d) * 100)
                high_p, low_p = last_c["high"], last_c["low"]
                upper_wick = ((high_p - close_p) / max(1, (high_p - low_p))) * 100
            else:
                vol_ratio = 150
                upper_wick = 15.0

            stock_metrics.append({
                "code": code,
                "name": r["name"],
                "close": close_p,
                "rate": r["rate"],
                "money_eok": r["money_eok"],
                "vwap": r["vwap"],
                "vol_ratio": vol_ratio,
                "upper_wick": round(upper_wick, 1),
                "prog_money": prog_money,
            })

        stock_metrics.sort(key=lambda x: x["money_eok"], reverse=True)
        top3 = stock_metrics[:3]
        theme_money = sum(x["money_eok"] for x in top3)
        leader = max(top3, key=lambda x: x["rate"])
        candidates = [s for s in stock_metrics if s["code"] != leader["code"] and 2.0 <= s["rate"] <= 14.0]
        second = max(candidates, key=lambda x: x["vol_ratio"]) if candidates else top3[1]

        theme_results.append({
            "theme_name": pt["theme_name"],
            "theme_money": theme_money,
            "top3": top3,
            "leader": leader,
            "second": second,
        })

    theme_results.sort(key=lambda x: x["theme_money"], reverse=True)
    surging_themes = [t for t in theme_results if t["leader"]["rate"] >= 5.0 and t["theme_money"] >= 800]

    merged_themes = []
    seen_tnames = set()
    for t in list(surging_themes[:5]) + list(theme_results[:5]):
        tnm = str(t.get("theme_name") or "").strip()
        if not tnm or tnm in seen_tnames:
            continue
        seen_tnames.add(tnm)
        ld = t.get("leader") or {}
        sc = t.get("second") or {}
        t_money = int(t.get("theme_money") or 0)
        is_surging = bool(float(ld.get("rate") or 0.0) >= 5.0 and t_money >= 800)

        stock_list = []
        seen_scodes = set()
        if ld and ld.get("code"):
            l_copy = dict(ld)
            l_copy["is_our_leader"] = True
            l_copy["is_second"] = False
            stock_list.append(l_copy)
            seen_scodes.add(str(ld["code"]))
        for st_it in list(t.get("top3") or []) + ([sc] if sc and sc.get("code") else []):
            if not isinstance(st_it, dict) or not st_it.get("code"):
                continue
            scd = str(st_it["code"])
            if scd in seen_scodes:
                continue
            seen_scodes.add(scd)
            s_copy = dict(st_it)
            s_copy["is_our_leader"] = False
            s_copy["is_second"] = bool(sc and str(sc.get("code")) == scd)
            stock_list.append(s_copy)

        merged_themes.append({
            "rank": len(merged_themes) + 1,
            "theme_name": tnm,
            "theme_money": t_money,
            "is_surging": is_surging,
            "leader": ld,
            "second": sc,
            "stocks": stock_list[:4],
        })
        if len(merged_themes) >= 3:
            break

    return latest_date_iso, merged_themes


def _fetch_residency_map(ymd_compact: str) -> dict:
    codes = ["069500", "114800", "229200", "251340"]
    out = {}
    for cd in codes:
        try:
            u = f"https://api.stock.naver.com/chart/domestic/item/{cd}/minute?startDateTime={ymd_compact}090000&endDateTime={ymd_compact}153000"
            r = requests.get(u, headers=HEADERS, timeout=4)
            if r.status_code != 200:
                continue
            bars = r.json()
            if not isinstance(bars, list) or not bars:
                continue
            cum_v, cum_pv = 0, 0.0
            morn_tot, morn_above, day_tot, day_above = 0, 0, 0, 0
            for b in bars:
                tm = str(b.get("localDateTime") or "")[8:12]
                if tm < "0900" or tm > "1520":
                    continue
                h = int(b.get("highPrice") or 0)
                l = int(b.get("lowPrice") or 0)
                c = int(b.get("currentPrice") or 0)
                dv = max(1, int(b.get("accumulatedTradingVolume") or 0))
                if c <= 0:
                    continue
                cum_v += dv
                cum_pv += ((h + l + c) / 3.0) * dv
                vw = cum_pv / cum_v
                if tm >= "0905":
                    day_tot += 1
                    if c > vw:
                        day_above += 1
                    if tm <= "0930":
                        morn_tot += 1
                        if c > vw:
                            morn_above += 1
            out[cd] = {
                "morn_above_pct": round((morn_above / morn_tot) * 100.0, 1) if morn_tot > 0 else 0.0,
                "day_above_pct": round((day_above / day_tot) * 100.0, 1) if day_tot > 0 else 0.0,
            }
        except Exception:
            pass
    return out


def _fetch_macro_and_news_cloud() -> dict:
    kw_rel = (
        "코스피", "코스닥", "증시", "장중", "마감", "출발", "외국인", "외인", "기관",
        "환율", "달러", "금리", "연준", "파월", "나스닥", "뉴욕", "월가", "다우", "S&P",
        "반도체", "엔비디아", "삼성전자", "SK하이닉스", "관세", "수출", "선물", "옵션", "시황", "지수",
    )
    bull_kw = ("상승", "반등", "급등", "강세", "돌파", "회복", "순매수", "사자", "훈풍", "최고", "호실적", "인하", "완화", "복귀", "기대", "랠리")
    bear_kw = ("하락", "급락", "약세", "무너", "붕괴", "순매도", "팔자", "투매", "우려", "규제", "관세", "하향", "충격", "경계", "인플레", "후퇴")
    urls = {
        "kp": "https://m.stock.naver.com/api/index/KOSPI/trend",
        "kq": "https://m.stock.naver.com/api/index/KOSDAQ/trend",
        "fx": "https://api.stock.naver.com/marketindex/exchange/FX_USDKRW",
        "ndx": "https://api.stock.naver.com/index/.IXIC/basic",
        "sox": "https://api.stock.naver.com/index/.SOX/basic",
        "news": "https://m.stock.naver.com/api/news/list?category=mainnews&pageSize=15",
    }
    def _gj(u):
        try:
            r = requests.get(u, headers=HEADERS, timeout=4)
            return r.json() if r.status_code == 200 and r.text.strip() else None
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=6) as ex:
        raw = dict(zip(urls.keys(), ex.map(_gj, urls.values())))
    kp = raw.get("kp") or {}
    kq = raw.get("kq") or {}
    fx_info = (raw.get("fx") or {}).get("exchangeInfo") or {}
    ndx = raw.get("ndx") or {}
    sox = raw.get("sox") or {}
    news_raw = raw.get("news") if isinstance(raw.get("news"), list) else []

    kp_for = _parse_int(kp.get("foreignValue"))
    kp_inst = _parse_int(kp.get("institutionalValue"))
    kq_for = _parse_int(kq.get("foreignValue"))
    kq_inst = _parse_int(kq.get("institutionalValue"))
    usdkrw = round(_parse_float(fx_info.get("calcPrice")), 1)
    usdkrw_chg = round(_parse_float(fx_info.get("fluctuations")), 1)
    ndx_pct = round(_parse_float(ndx.get("fluctuationsRatio")), 2)
    if str((ndx.get("compareToPreviousPrice") or {}).get("name")) == "FALLING" and ndx_pct > 0:
        ndx_pct = -ndx_pct
    sox_pct = round(_parse_float(sox.get("fluctuationsRatio")), 2)
    if str((sox.get("compareToPreviousPrice") or {}).get("name")) == "FALLING" and sox_pct > 0:
        sox_pct = -sox_pct

    picked, bh, brh = [], 0, 0
    for it in news_raw:
        tit = re.sub(r"<[^>]+>", "", str(it.get("tit") or "")).strip()
        sub = re.sub(r"<[^>]+>", "", str(it.get("subcontent") or "")).strip()
        if not tit:
            continue
        txt = f"{tit} {sub}"
        if not any(k in txt for k in kw_rel):
            continue
        bp = sum(1 for k in bull_kw if k in txt)
        bn = sum(1 for k in bear_kw if k in txt)
        bh += bp
        brh += bn
        tone = "UP" if bp > bn else ("DN" if bn > bp else "NEUTRAL")
        dt_s = str(it.get("dt") or "")
        hm = f"{dt_s[8:10]}:{dt_s[10:12]}" if len(dt_s) >= 12 else ""
        oid, aid = str(it.get("oid") or ""), str(it.get("aid") or "")
        picked.append({
            "title": tit,
            "press": str(it.get("ohnm") or ""),
            "time": hm,
            "tone": tone,
            "url": f"https://n.news.naver.com/mnews/article/{oid}/{aid}" if (oid and aid) else "https://m.stock.naver.com/investment/news/mainnews",
        })
        if len(picked) >= 3:
            break

    news_dir = "UP" if bh >= brh + 2 else ("DN" if brh >= bh + 2 else "NEUTRAL")
    news_lbl = "호재·반등 재료 우위" if news_dir == "UP" else ("경계·하방 재료 우위" if news_dir == "DN" else "호악재 혼조")

    m_sc = 0
    if kp_for >= 1000: m_sc += 2
    elif kp_for <= -1000: m_sc -= 2
    elif kp_for >= 300: m_sc += 1
    elif kp_for <= -300: m_sc -= 1
    if (kq_for + kq_inst) >= 400: m_sc += 1
    elif (kq_for + kq_inst) <= -400: m_sc -= 1
    if usdkrw_chg <= -3.0: m_sc += 1
    elif usdkrw_chg >= 3.0: m_sc -= 1
    if (ndx_pct + sox_pct) / 2.0 >= 0.7: m_sc += 1
    elif (ndx_pct + sox_pct) / 2.0 <= -0.7: m_sc -= 1

    m_dir = "UP" if m_sc >= 2 else ("DN" if m_sc <= -2 else "NEUTRAL")
    m_lbl = "수급·매크로 상승 우호" if m_dir == "UP" else ("수급·매크로 하방 압력" if m_dir == "DN" else "수급·매크로 중립")
    return {
        "kospi_foreign_eok": kp_for,
        "kospi_inst_eok": kp_inst,
        "kosdaq_foreign_eok": kq_for,
        "kosdaq_inst_eok": kq_inst,
        "usdkrw": usdkrw,
        "usdkrw_chg": usdkrw_chg,
        "nasdaq_pct": ndx_pct,
        "sox_pct": sox_pct,
        "macro_dir": m_dir,
        "macro_label": m_lbl,
        "news_dir": news_dir,
        "news_label": news_lbl,
        "headlines": picked,
    }


def _fmt_eok_s(v: int) -> str:
    s = "+" if v > 0 else ("-" if v < 0 else "")
    a = abs(int(v))
    if a >= 10000:
        j, rm = a // 10000, a % 10000
        return f"{s}{j}조 {rm:,}억" if rm > 0 else f"{s}{j}조"
    return f"{s}{a:,}억"


def update_index_radar_snapshot(prev_idx: dict, now_kst) -> dict:
    idx_out = dict(prev_idx) if isinstance(prev_idx, dict) else {}
    etfs = [
        ("069500", "KODEX 200", "KOSPI", "UP", "코스피 상승"),
        ("114800", "KODEX 인버스", "KOSPI", "DN", "코스피 하락"),
        ("229200", "KODEX 코스닥150", "KOSDAQ", "UP", "코스닥 상승"),
        ("251340", "KODEX 코스닥150선물인버스", "KOSDAQ", "DN", "코스닥 하락"),
    ]
    try:
        r = requests.get(
            "https://polling.finance.naver.com/api/realtime?query=SERVICE_ITEM:069500,114800,229200,251340",
            headers=HEADERS,
            timeout=5,
        )
        datas = r.json().get("result", {}).get("areas", [{}])[0].get("datas", [])
        res_map = _fetch_residency_map(now_kst.strftime("%Y%m%d"))
        if not res_map:
            for bd in range(1, 5):
                prev_ymd = (now_kst - datetime.timedelta(days=bd)).strftime("%Y%m%d")
                res_map = _fetch_residency_map(prev_ymd)
                if res_map:
                    break
        mm = {c: (nm, mk, dr, dl) for c, nm, mk, dr, dl in etfs}
        snaps = {}
        for d in datas:
            cd = str(d.get("cd") or "").strip()
            if cd not in mm:
                continue
            nv = abs(int(d.get("nv") or 0))
            pcv = abs(int(d.get("pcv") or 0))
            ov = abs(int(d.get("ov") or nv))
            aq = int(d.get("aq") or 0)
            aa = float(d.get("aa") or 0.0)
            if nv <= 0 or pcv <= 0:
                continue
            val_won = (aa * 1_000_000.0 if (aq > 0 and aa / aq < nv * 0.05) else aa) if (aq > 0 and aa > 0) else 0.0
            vwap = int(round(val_won / aq)) if (aq > 0 and val_won > 0) else nv
            money_eok = int(round(val_won / 1e8)) if val_won > 0 else 0
            nm, mk, dr, dl = mm[cd]
            ri = res_map.get(cd) or {}
            snaps[cd] = {
                "code": cd,
                "name": nm,
                "market": mk,
                "dir": dr,
                "dir_label": dl,
                "cur_price": nv,
                "open_price": ov,
                "prev_close": pcv,
                "vwap": vwap,
                "money_eok": money_eok,
                "vwap_gap_pct": round((nv / max(1, vwap) - 1.0) * 100.0, 2),
                "chg_prev_pct": round((nv / pcv - 1.0) * 100.0, 2),
                "above_vwap": bool(nv > vwap),
                "is_bull_candle": bool(nv > ov),
                "morn_above_pct": float(ri.get("morn_above_pct") if "morn_above_pct" in ri else (100.0 if nv > vwap else 0.0)),
                "day_above_pct": float(ri.get("day_above_pct") if "day_above_pct" in ri else (100.0 if nv > vwap else 0.0)),
                "pair_money_share_pct": 50.0,
                "flow_surge_mult": 1.0,
            }
        for cu, cdn, ndn in (("069500", "114800", 0.45), ("229200", "251340", 0.30)):
            if cu in snaps and cdn in snaps:
                mu, md = max(1, snaps[cu]["money_eok"]), max(1, snaps[cdn]["money_eok"])
                tm = mu + md
                ds, us = md / tm, mu / tm
                snaps[cdn]["pair_money_share_pct"] = round(ds * 100.0, 1)
                snaps[cu]["pair_money_share_pct"] = round(us * 100.0, 1)
                snaps[cdn]["flow_surge_mult"] = round(ds / ndn, 2)
                snaps[cu]["flow_surge_mult"] = round(us / (1.0 - ndn), 2)

        if "069500" in snaps and "114800" in snaps:
            ku = (snaps["069500"]["day_above_pct"], snaps["069500"]["vwap_gap_pct"])
            kd = (snaps["114800"]["day_above_pct"], snaps["114800"]["vwap_gap_pct"])
            w = snaps["069500"] if ku >= kd else snaps["114800"]
            o = snaps["114800"] if w is snaps["069500"] else snaps["069500"]
            idx_out["kospi_pair"] = {"winner": w, "other": o}
        if "229200" in snaps and "251340" in snaps:
            ku2 = (snaps["229200"]["day_above_pct"], snaps["229200"]["vwap_gap_pct"])
            kd2 = (snaps["251340"]["day_above_pct"], snaps["251340"]["vwap_gap_pct"])
            w2 = snaps["229200"] if ku2 >= kd2 else snaps["251340"]
            o2 = snaps["251340"] if w2 is snaps["229200"] else snaps["229200"]
            idx_out["kosdaq_pair"] = {"winner": w2, "other": o2}

        macro = _fetch_macro_and_news_cloud()
        idx_out["macro_news"] = macro
        cands = [x for x in ((idx_out.get("kospi_pair") or {}).get("winner"), (idx_out.get("kosdaq_pair") or {}).get("winner")) if x]
        if cands:
            cands.sort(key=lambda x: (float(x.get("day_above_pct") or 0.0), float(x.get("flow_surge_mult") or 1.0)), reverse=True)
            lead = cands[0]
            cdir = lead["dir"]
            mdir = macro.get("macro_dir", "NEUTRAL")
            ndir = macro.get("news_dir", "NEUTRAL")
            ag = 1 + (1 if mdir == cdir else 0) + (1 if ndir == cdir else 0)
            conf = (mdir != "NEUTRAL" and mdir != cdir)
            grade = "CONFLICT" if conf else ("STRONG" if ag >= 3 else ("GOOD" if ag >= 2 else "WATCH"))
            badge = "🟡 차트·수급 엇갈림 (가짜 움직임 주의)" if conf else (f"🟢 3박자 일치 · {lead['dir_label']} 강력 우위" if ag >= 3 else (f"🔵 수급·VWAP 동조 · {lead['dir_label']} 우위" if ag >= 2 else "⚪ 방향 탐색 구간"))
            kpf, kqf = int(macro.get("kospi_foreign_eok") or 0), int(macro.get("kosdaq_foreign_eok") or 0)
            fd = "순매수 유입" if kpf > 300 else ("대량 순매도 출회" if kpf < -1000 else ("순매도 우위" if kpf < -200 else "중립 탐색"))
            l1 = f"외국인이 코스피 {_fmt_eok_s(kpf)}(코스닥 {_fmt_eok_s(kqf)}) {fd} 중이며, 미 나스닥({macro.get('nasdaq_pct',0):+.2f}%)·반도체({macro.get('sox_pct',0):+.2f}%) 및 원/달러 {macro.get('usdkrw',0):,.1f}원 흐름 속에 시황 뉴스는 '{macro.get('news_label','혼조')}' 분위기입니다."
            l2 = f"정방향·인버스 대결에서도 [{lead['name']}] 쪽으로 평소 대비 {lead.get('flow_surge_mult',1.0):.2f}배 자금이 집중되며 당일 VWAP 상단 체류 {lead.get('day_above_pct',0):.0f}%를 기록 중입니다."
            idx_out["synthesis"] = {"grade": grade, "badge": badge, "agree_count": ag, "conflict_macro": conf, "lead_etf": lead, "line1": l1, "line2": l2}
        idx_out["updated_at"] = f"{now_kst.strftime('%H:%M:%S')} (☁️깃허브 자동)"
    except Exception:
        pass
    return idx_out


def main():
    force = "--force" in sys.argv or os.environ.get("FORCE_UPDATE", "").lower() == "true"
    dry_run = "--dry-run" in sys.argv
    now_kst = datetime.datetime.now(KST_TZ)

    state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

    isi_board = state.get("isi_board")
    if not isinstance(isi_board, dict):
        isi_board = {
            "overnight_holding": {"active": False},
            "top3_themes": [],
            "stats": {"total_closed": 0, "win_count": 0, "loss_count": 0, "win_rate_pct": 0.0, "total_pnl_krw": 0},
            "trades": [],
        }

    # 집 PC(kr_trader.py) 우선 양보 가드: 집 PC가 최근 180초 이내에 업데이트 중이면 클라우드 백업 생략
    if not force and not dry_run:
        last_upd_str = str(isi_board.get("updated_at") or state.get("updated_at") or "")
        last_src = str(isi_board.get("updater_source") or "HOME_PC")
        if last_upd_str and last_src != "GITHUB_CLOUD":
            try:
                last_dt = datetime.datetime.strptime(last_upd_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST_TZ)
                age_sec = (now_kst - last_dt).total_seconds()
                if 0 <= age_sec < 180:
                    print(f"[SKIP] 집 PC가 {int(age_sec)}초 전 실시간 송출 중이므로 클라우드 백업 생략 ({last_upd_str})")
                    return
            except Exception:
                pass

    latest_date_iso, top3_themes = extract_isi_top3_themes()
    if not top3_themes:
        print("[WARN] 추출된 주도 테마 데이터가 없어 업데이트를 건너뜁니다.")
        return

    isi_board["updated_at"] = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    isi_board["updater_source"] = "GITHUB_CLOUD"
    isi_board["theme_date"] = latest_date_iso
    isi_board["theme_updated_at"] = f"{now_kst.strftime('%H:%M:%S')} (☁️깃허브 자동)"
    isi_board["top3_themes"] = top3_themes
    isi_board["index_radar"] = update_index_radar_snapshot(isi_board.get("index_radar"), now_kst)

    state["updated_at"] = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    state["isi_board"] = isi_board

    print(
        f"[OK] ISI TOP 3 추출 완료 ({latest_date_iso}): "
        + ", ".join(f"{t['rank']}위 {t['theme_name']}({t['leader'].get('name')})" for t in top3_themes)
    )

    if dry_run:
        return

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # EMQX 실시간 브로커에도 즉시 송출 (4초 타임아웃 가드)
    try:
        import threading
        import paho.mqtt.publish as publish
        def _pub():
            try:
                publish.single(
                    TOPIC_ISI,
                    payload=json.dumps(isi_board, ensure_ascii=False),
                    qos=1,
                    retain=True,
                    hostname=BROKER_HOST,
                    port=BROKER_PORT,
                )
            except Exception:
                pass
        th = threading.Thread(target=_pub, daemon=True)
        th.start()
        th.join(timeout=4.0)
        print("[OK] EMQX 웹소켓 실시간 푸시 시도 완료")
    except Exception as e:
        print(f"[WARN] EMQX 푸시 생략: {e}")


if __name__ == "__main__":
    main()
