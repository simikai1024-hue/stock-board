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

    # EMQX 실시간 브로커에도 즉시 송출 (전광판 열어둔 브라우저 즉시 반응)
    try:
        import paho.mqtt.publish as publish
        publish.single(
            TOPIC_ISI,
            payload=json.dumps(isi_board, ensure_ascii=False),
            qos=1,
            retain=True,
            hostname=BROKER_HOST,
            port=BROKER_PORT,
        )
        print("[OK] EMQX 웹소켓 실시간 푸시 완료")
    except Exception as e:
        print(f"[WARN] EMQX 푸시 생략: {e}")


if __name__ == "__main__":
    main()
