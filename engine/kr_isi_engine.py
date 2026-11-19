# -*- coding: utf-8 -*-
"""
kr_isi_engine.py — ISI 인텔리전스 수급·일정·VWAP 전략 키움 자동매매 전용 모듈
- 유튜브 프로덕션 폴더(c:\\Users\\아빠\\Desktop\\yt_manager\\kr_shorts_factory) 절대 수정 금지 (100% Read-Only 격리)
- 모드 1 (BROADCAST_SYNC): 08:15 / 16:15 방송 추출 완료된 JSON을 읽기 전용으로 구독 -> 방송 종목과 100.0% 일치 보장
- 모드 2 (STANDALONE_CALC): 동일한 네이버 테마·일봉·외인기관 수급 수식을 매매 폴더 내부에서 독립 연산 (15:18 정규장 종가배팅 등 활용)
- 소액 계좌 맞춤형 필터: 전체 1등주가 예수금 초과 고가주일 때 Top 5 주도테마 내 매수 가능 대장주 자동 탐색 지원
"""
import os
import re
import sys
import math
import json
import datetime
import xml.etree.ElementTree as ET
import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ISI_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_DATA_DIR = os.path.join(ISI_MODULE_DIR, "data")
LOCAL_HIST_DIR = os.path.join(LOCAL_DATA_DIR, "history")

# 유튜브 방송 프로덕션 경로 (오직 읽기 전용 참조만 허용! 절대 쓰기 금지!)
YT_PROD_READONLY_DIR = r"c:\Users\아빠\Desktop\yt_manager\kr_shorts_factory"
YT_PROD_DATA_READONLY_DIR = os.path.join(YT_PROD_READONLY_DIR, "data")
YT_PROD_HIST_READONLY_DIR = os.path.join(YT_PROD_DATA_READONLY_DIR, "history")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://m.stock.naver.com/",
}


def round_kr_tick(price: float) -> int:
    """KRX/NXT 가격대별 호가 단위 정렬"""
    p = int(round(price))
    if p < 2000:
        return p
    if p < 5000:
        return (p // 5) * 5
    if p < 20000:
        return (p // 10) * 10
    if p < 50000:
        return (p // 50) * 50
    if p < 200000:
        return (p // 100) * 100
    if p < 500000:
        return (p // 500) * 500
    return (p // 1000) * 1000


def normalize_kr_code(code: str) -> str:
    """신규 영문혼합 종목코드(예: 엘리스 0158S0)까지 훼손 없이 6자리 보존"""
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
    """동전주(<2,000원), 우선주, 스팩/ETF/ETN, 저유동성(<30억) 하드컷 (원본 수식 100% 동일)"""
    if close_p < 2000:
        return False
    if name.endswith("우") or "우B" in name or "우C" in name:
        return False
    if "스팩" in name or "ETN" in name or "ETF" in name:
        return False
    if money_eok < 30:
        return False
    return True


class ISITradingEngine:
    """ISI 인텔리전스 전략 신호 수신·독립 추출·예수금 맞춤 타점 설계 엔진"""

    def __init__(self, prefer_broadcast_json: bool = True):
        self.prefer_broadcast_json = prefer_broadcast_json
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        os.makedirs(LOCAL_HIST_DIR, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. 방송 원본 JSON 읽기 전용 구독 (방송 종목과 100.0% 무오차 동기화)
    # ------------------------------------------------------------------
    def load_broadcast_evening_readonly(self, date_str: str = None) -> dict:
        """
        yt_manager/kr_shorts_factory/data/history 에서 당일 저녁 방송 JSON과 Top5 테마를 Read-Only로 로드.
        """
        if not date_str:
            date_str = datetime.date.today().strftime("%Y-%m-%d")

        ev_path = os.path.join(YT_PROD_HIST_READONLY_DIR, f"{date_str}_evening.json")
        t5_path = os.path.join(YT_PROD_HIST_READONLY_DIR, f"{date_str}_top5_themes.json")

        payload = None
        top5 = []
        if os.path.exists(ev_path):
            with open(ev_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        if os.path.exists(t5_path):
            with open(t5_path, "r", encoding="utf-8") as f:
                top5 = json.load(f)

        if payload:
            return {"source": "BROADCAST_JSON_READONLY", "date": date_str, "payload": payload, "top5_themes": top5}
        return {}

    def load_broadcast_morning_readonly(self, date_str: str = None) -> dict:
        if not date_str:
            date_str = datetime.date.today().strftime("%Y-%m-%d")
        m_path = os.path.join(YT_PROD_HIST_READONLY_DIR, f"{date_str}_morning.json")
        if os.path.exists(m_path):
            with open(m_path, "r", encoding="utf-8") as f:
                return {"source": "BROADCAST_JSON_READONLY", "date": date_str, "payload": json.load(f)}
        return {}

    # ------------------------------------------------------------------
    # 2. 매매 폴더 내부 독립 연산기 (1_extractor.py 수식 100% 동일 복제)
    # ------------------------------------------------------------------
    @staticmethod
    def fetch_real_ohlcv(code: str, count: int = 24) -> list:
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

    @staticmethod
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

    @staticmethod
    def crawl_naver_top_themes(top_n: int = 25) -> list:
        from concurrent.futures import ThreadPoolExecutor
        url = f"https://m.stock.naver.com/api/stocks/theme?page=1&pageSize={top_n}"
        res = requests.get(url, headers=HEADERS, timeout=8)
        res.raise_for_status()
        groups = [g for g in res.json().get("groups", [])[:top_n] if g.get("no") and str(g.get("name", "")).strip()]

        def _fetch_one(g):
            t_no = g.get("no")
            t_name = str(g.get("name", "")).strip()
            detail_url = f"https://m.stock.naver.com/api/stocks/theme/{t_no}?pageSize=20&page=1"
            d_res = requests.get(detail_url, headers=HEADERS, timeout=8)
            stocks_raw = d_res.json().get("stocks", []) if d_res.status_code == 200 else []
            return {"theme_no": t_no, "theme_name": t_name, "stocks": stocks_raw}

        with ThreadPoolExecutor(max_workers=10) as ex:
            return list(ex.map(_fetch_one, groups))

    def extract_evening_standalone(self, save_local: bool = True) -> dict:
        """유튜브 폴더를 전혀 건드리지 않고 매매 모듈 단독으로 저녁 1위 주도테마 및 Top 5 테마 산출 (원본 수식 100% 일치 · 3초 초고속)"""
        from concurrent.futures import ThreadPoolExecutor
        themes = self.crawl_naver_top_themes(top_n=25)
        latest_date_iso = datetime.date.today().strftime("%Y-%m-%d")

        # 1단계: 테마 API 기본 수치만으로 유효 종목 필터링 및 theme_money / leader 정렬 (원본과 100% 동치!)
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
            raw_theme_money = sum(x["money_eok"] for x in top3_pre)
            # [V2.1 핵심: 삼성전자(+1.9%, 3조) 거래대금 착시 제거] 실제 +5% 이상 급등 종목만 합산해 진짜 급등 강도(surge_score) 산출!
            surging_pre_rows = [x for x in valid_rows if x["rate"] >= 5.0]
            surge_cnt_pre = len(surging_pre_rows)
            surge_top3_pre = sorted(surging_pre_rows, key=lambda x: x["money_eok"], reverse=True)[:3]
            surge_money_pre = sum(x["money_eok"] for x in surge_top3_pre)
            surge_avg_rt_pre = (
                sum(x["rate"] for x in sorted(surging_pre_rows, key=lambda x: x["rate"], reverse=True)[:3])
                / max(1, min(3, surge_cnt_pre))
                if surge_cnt_pre > 0 else 0.0
            )
            surge_score_pre = round(
                surge_avg_rt_pre * math.log10(max(10.0, float(surge_money_pre))) * (1.25 if surge_cnt_pre >= 2 else 0.65),
                2,
            ) if surge_money_pre > 0 else 0.0
            leader_pre = max(surging_pre_rows, key=lambda x: x["rate"]) if surging_pre_rows else max(top3_pre, key=lambda x: x["rate"])
            prelim_themes.append({
                "theme_name": t["theme_name"],
                "theme_money": raw_theme_money,
                "surge_money": surge_money_pre,
                "surge_count": surge_cnt_pre,
                "surge_avg_rate": round(surge_avg_rt_pre, 2),
                "surge_score": surge_score_pre,
                "leader_rate": leader_pre["rate"],
                "valid_rows": valid_rows,
            })

        if not prelim_themes:
            return {}

        prelim_themes.sort(key=lambda x: x["theme_money"], reverse=True)
        # 진성 급등 테마: [+5% 이상 급등주 2종목 이상 & 급등 대금 500억 이상] 우선, 없으면 [1종목 & 급등 대금 800억 이상]을 surge_score 내림차순 선별!
        surging_pre = [pt for pt in prelim_themes if pt["surge_count"] >= 2 and pt["surge_money"] >= 500]
        if not surging_pre:
            surging_pre = [pt for pt in prelim_themes if pt["surge_count"] >= 1 and pt["surge_money"] >= 800]
        surging_pre.sort(key=lambda x: (x["surge_score"], x["surge_money"]), reverse=True)
        target_themes = list(surging_pre[:8])
        for pt in prelim_themes[:4]:
            if pt not in target_themes:
                target_themes.append(pt)

        need_codes = []
        seen_c = set()
        for pt in target_themes:
            for r in pt["valid_rows"]:
                if r["code"] not in seen_c:
                    seen_c.add(r["code"])
                    need_codes.append(r["code"])

        def _fetch_extra(cd):
            return cd, self.fetch_real_ohlcv(cd, count=7), self.fetch_investor_net_buy_eok(cd, days=1)

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
            surging_m = [x for x in stock_metrics if x["rate"] >= 5.0]
            surge_cnt = len(surging_m)
            surge_top3 = sorted(surging_m, key=lambda x: x["money_eok"], reverse=True)[:3]
            surge_money = sum(x["money_eok"] for x in surge_top3)
            surge_avg_rt = (
                sum(x["rate"] for x in sorted(surging_m, key=lambda x: x["rate"], reverse=True)[:3])
                / max(1, min(3, surge_cnt))
                if surge_cnt > 0 else 0.0
            )
            surge_score = round(
                surge_avg_rt * math.log10(max(10.0, float(surge_money))) * (1.25 if surge_cnt >= 2 else 0.65),
                2,
            ) if surge_money > 0 else 0.0

            # 급등 테마는 실제 +5% 이상 급등 종목들로 top3·대장주를 구성해 삼성전자(+1.9%) 착시를 완전히 배제!
            if surge_cnt >= 2:
                top3 = sorted(surging_m, key=lambda x: (x["rate"], x["money_eok"]), reverse=True)[:3]
                if len(top3) < 3:
                    for sm in stock_metrics:
                        if sm not in top3:
                            top3.append(sm)
                        if len(top3) == 3:
                            break
                theme_money = surge_money
            else:
                top3 = stock_metrics[:3]
                theme_money = sum(x["money_eok"] for x in top3)

            # 상한가 잠김(>= +29.0%) 종목은 15:18 실매수 체결 불가하므로 매수 가능한(+5.0% ~ +28.9%) 1등 대장주를 우선 선정!
            buyable_surging = [x for x in surging_m if x["rate"] < 29.0]
            if buyable_surging:
                leader = max(buyable_surging, key=lambda x: (x["rate"], x["money_eok"]))
            elif surging_m:
                leader = max(surging_m, key=lambda x: (x["rate"], x["money_eok"]))
            else:
                leader = max(top3, key=lambda x: x["rate"])
            candidates = [s for s in stock_metrics if s["code"] != leader["code"] and 2.0 <= s["rate"] <= 14.0]
            second = max(candidates, key=lambda x: x["vol_ratio"]) if candidates else (top3[1] if len(top3) > 1 else leader)

            theme_results.append({
                "theme_name": pt["theme_name"],
                "theme_money": theme_money,
                "surge_money": surge_money,
                "surge_count": surge_cnt,
                "surge_avg_rate": round(surge_avg_rt, 2),
                "surge_score": surge_score,
                "top3": top3,
                "leader": leader,
                "second": second,
            })

        if not theme_results:
            return {}

        surging_themes = [t for t in theme_results if t["surge_count"] >= 2 and t["surge_money"] >= 500]
        if not surging_themes:
            surging_themes = [t for t in theme_results if t["surge_count"] >= 1 and t["surge_money"] >= 800]
        surging_themes.sort(key=lambda x: (x["surge_score"], x["surge_money"]), reverse=True)
        theme_results.sort(key=lambda x: (x["surge_score"], x["theme_money"]), reverse=True)
        best = surging_themes[0] if surging_themes else theme_results[0]
        L = best["leader"]
        S = best["second"]

        if L["prog_money"] > 0 and L["close"] >= L["vwap"] and L["upper_wick"] <= 25:
            stamp_code, stamp_name = "FIRE", "진성 수급"
        elif S["vol_ratio"] >= 250:
            stamp_code, stamp_name = "BOLT", "키맞추기 대기"
        else:
            stamp_code, stamp_name = "ICE", "단발성/윗꼬리 주의"

        payload = {
            "date": latest_date_iso,
            "mode": "evening",
            "theme_name": best["theme_name"],
            "theme_money": f"{best['theme_money']:,}",
            "leader_name": L["name"],
            "leader_code": L["code"],
            "leader_close": L["close"],
            "leader_rate": f"{L['rate']:+.1f}",
            "prog_money": f"{L['prog_money']:+d}",
            "vwap_price": f"{L['vwap']:,}",
            "second_name": S["name"],
            "second_code": S["code"],
            "second_close": S["close"],
            "second_rate": f"{S['rate']:+.1f}",
            "stamp_code": stamp_code,
            "stamp_name": stamp_name,
        }
        top5 = theme_results[:5]
        surging_top5 = surging_themes[:5]
        if save_local:
            with open(os.path.join(LOCAL_HIST_DIR, f"{latest_date_iso}_evening_local.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {"payload": payload, "surging_themes": surging_top5, "top5_themes": top5},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        return {
            "source": "STANDALONE_CALC",
            "date": latest_date_iso,
            "payload": payload,
            "surging_themes": surging_top5,
            "top5_themes": top5,
        }

    # ------------------------------------------------------------------
    # 3. 예수금 맞춤 타겟 선정 & '익일 +6.0% 매도' 실전 플랜 생성기
    # ------------------------------------------------------------------
    @staticmethod
    def _is_clean_closing_candidate(c: dict) -> bool:
        """
        [진성 종가베팅 5대 품질 관문 — 상한가 잠김·가짜 대장주·장막판 윗꼬리 설거지·빈약 잡주 원천 차단]
        1) 등락률 +5.0% <= rate < +29.0% (실제 강세 상승 종목만 허용 & 상한가 잠김 매수불가 종목 자동 제외!)
        2) 종가(현재가) >= 당일 세력평단(VWAP) 안착 필수 (장 막판 매수세 이탈 종목 차단)
        3) 윗꼬리 비율(upper_wick) <= 30.0% (장중 고점 대비 반토막 설거지 캔들 차단)
        4) 수급 체급: 거래대금 >= 200억 원 OR (거래대금 >= 50억 원 & 외국인+기관 순매수 > 0억)
        """
        if not isinstance(c, dict):
            return False
        cl = int(c.get("close") or 0)
        vw = int(c.get("vwap") or cl)
        rt = float(c.get("rate") or 0.0)
        wick = float(c.get("upper_wick") if c.get("upper_wick") is not None else 15.0)
        meok = int(c.get("money_eok") or 0)
        prog = int(c.get("prog_money") or 0)

        if cl < 2000 or rt < 5.0 or rt >= 29.0:
            return False
        if vw > 0 and cl < vw:
            return False
        if wick > 30.0:
            return False
        if meok < 200 and not (meok >= 50 and prog > 0):
            return False
        return True

    @staticmethod
    def select_evening_leader_for_budget(
        evening_bundle: dict,
        orderable_cash_krw: int,
        allow_top5_affordable_fallback: bool = True,
        target_tp_pct: float = 6.0,
    ) -> dict:
        """
        [V2 업그레이드: 진성 품질 필터 장착 예수금 맞춤 타겟 선정기]
        0순위 (시장 휴식 가드): 당일 진성 급등테마(theme_money >= 800억 & 대장주 +5%↑)가 0개인 폭락/빙하기에는 억지 매매 금지(현금 관망)!
        1순위 (철칙): 진성 1위 급등테마의 메인 1등 대장주가 4대 품질 관문을 통과하고 예수금 이내이면 최우선 선택!
        2순위 (예산 초과 또는 1순위 품질 미달 시 — 진성 급등테마 내에서만 탐색):
          - 2-A: 당일 진성 급등테마(surging_themes) 1위~5위 순서대로 각 테마의 '진짜 1등 대장주(leader)' 중 4대 품질 관문 통과 & 예산 이내 종목 최우선 선택!
          - 2-B: 급등테마들의 1등주가 모두 고가주(또는 윗꼬리 탈락)일 때, 진성 급등테마 내 2등주/상위 핵심주 중 4대 품질 관문 통과 & 거래대금 250억↑ 종목 선택!
          - 만약 통과하는 진성 종목이 단 1개도 없으면 억지로 불량 종목을 사지 않고 안전하게 당일 패스(현금 100% 휴식)!
        """
        if not evening_bundle:
            return {"ok": False, "reason": "저녁 시그널 데이터 없음"}

        top5 = evening_bundle.get("top5_themes") or []
        raw_surging = evening_bundle.get("surging_themes") or []
        payload = evening_bundle.get("payload") or {}
        max_unit_budget = int(orderable_cash_krw * 0.985)  # 슬리피지/수수료 버퍼 1.5%

        # 당일 진성 급등테마 리스트 중복 제거 병합 (+5% 급등주 2종목↑ & 급등대금 500억↑, 또는 단독 800억↑)
        surging_themes = []
        seen_t_names = set()
        for t in list(raw_surging) + list(top5):
            t_nm = str(t.get("theme_name") or "")
            if t_nm and t_nm not in seen_t_names:
                s_cnt = int(t.get("surge_count") or 0)
                s_mon = int(t.get("surge_money") or t.get("theme_money") or 0)
                l_rt = float(t.get("leader", {}).get("rate", 0) or 0)
                if l_rt >= 5.0 and ((s_cnt >= 2 and s_mon >= 500) or s_mon >= 800):
                    seen_t_names.add(t_nm)
                    surging_themes.append(t)

        # [0순위 시장 휴식 가드] 진성 급등테마가 0개인 날(예: 11/17 폭락장)은 가짜 1위 테마/2등주 억지 진입 원천 차단!
        if not surging_themes:
            return {
                "ok": False,
                "reason": "당일 진성 급등테마(거래대금 800억↑ & 대장주 +5%↑) 전무 — 현금 관망(휴식)",
            }

        primary_theme = surging_themes[0]
        primary_leader = dict(primary_theme["leader"])
        primary_leader["theme_name"] = primary_theme.get("theme_name", payload.get("theme_name", ""))
        primary_leader["rank_type"] = f"진성 1위테마 1등 대장주 ({primary_leader['theme_name']})"

        chosen = None
        seen_codes = set()

        # 1순위: 진성 1위 급등테마 1등 대장주가 품질 관문 통과 & 예산 이내면 즉시 확정
        p_cd = normalize_kr_code(primary_leader.get("code"))
        if p_cd:
            seen_codes.add(p_cd)
        if (
            ISITradingEngine._is_clean_closing_candidate(primary_leader)
            and 2000 <= int(primary_leader.get("close") or 0) <= max_unit_budget
        ):
            chosen = primary_leader

        elif allow_top5_affordable_fallback:
            # 2-A: 진성 급등테마(surging_themes) 순서대로 각 테마의 '1등 대장주(leader)' 중 품질 통과 & 예산 이내 우선 선택!
            # (예: 성호전자처럼 윗꼬리 57%·VWAP 이탈 불량 1등주는 건너뛰고 에스피지 같은 꽉 찬 양봉 1등주 포착!)
            for idx, th in enumerate(surging_themes, 1):
                ld = th.get("leader")
                if not ld or not ld.get("code"):
                    continue
                cd = normalize_kr_code(ld["code"])
                if cd in seen_codes:
                    continue
                seen_codes.add(cd)
                if (
                    ISITradingEngine._is_clean_closing_candidate(ld)
                    and 2000 <= int(ld.get("close") or 0) <= max_unit_budget
                ):
                    chosen = dict(ld)
                    chosen["theme_name"] = th.get("theme_name", "")
                    chosen["rank_type"] = f"진성 급등{idx}위테마 1등 대장주 ({chosen['theme_name']})"
                    break

            # 2-B: 급등테마 1등주들이 모두 고가주(또는 윗꼬리 탈락)일 때, 진성 급등테마 내 핵심 강세주(거래대금 250억↑ & 품질 통과) 탐색
            if not chosen:
                for idx, th in enumerate(surging_themes, 1):
                    cand_pool = list(th.get("top3") or []) + ([th.get("second")] if th.get("second") else [])
                    for c in cand_pool:
                        if not c or not c.get("code"):
                            continue
                        cd = normalize_kr_code(c["code"])
                        if cd in seen_codes:
                            continue
                        seen_codes.add(cd)
                        if (
                            ISITradingEngine._is_clean_closing_candidate(c)
                            and int(c.get("money_eok") or 0) >= 250
                            and 2000 <= int(c.get("close") or 0) <= max_unit_budget
                        ):
                            chosen = dict(c)
                            chosen["theme_name"] = th.get("theme_name", "")
                            chosen["rank_type"] = f"진성 급등{idx}위테마 핵심강세주 ({chosen['theme_name']})"
                            break
                    if chosen:
                        break

        if not chosen:
            return {
                "ok": False,
                "reason": (
                    f"진성 급등테마 내 품질 기준(종가>=VWAP · 윗꼬리<=30% · 수급충족) 및 "
                    f"현재 예수금({orderable_cash_krw:,}원)을 동시에 만족하는 종목 없음 — 안전 관망"
                ),
                "primary_leader": primary_leader,
            }

        entry_px = int(chosen["close"])
        qty = max(1, max_unit_budget // entry_px)
        tp_px = round_kr_tick(entry_px * (1.0 + target_tp_pct / 100.0))
        sl_vwap_px = round_kr_tick(min(int(chosen.get("vwap") or entry_px), entry_px * 0.965))

        return {
            "ok": True,
            "primary_leader": primary_leader,
            "selected": chosen,
            "orderable_cash_krw": orderable_cash_krw,
            "recommended_qty": qty,
            "ref_close_price": entry_px,
            "target_tp_pct": target_tp_pct,
            "target_sell_price": tp_px,
            "stop_loss_price": sl_vwap_px,
        }


if __name__ == "__main__":
    print("=" * 88)
    print(" 🔬 [ISI 전략 키움 이식 모듈 단독 시뮬레이션 — 유튜브 원본 폴더 수정 0%]")
    print("=" * 88)
    eng = ISITradingEngine()
    res = eng.extract_evening_standalone(save_local=True)
    p = res.get("payload", {})
    print(f" 📅 기준일: {res.get('date')} | 1위 주도테마: {p.get('theme_name')} ({p.get('theme_money')}억)")
    print(f" 👑 방송용 전체 1등 대장주: {p.get('leader_name')}({p.get('leader_code')}) | 종가 {p.get('leader_close'):,}원 ({p.get('leader_rate')}%) | VWAP {p.get('vwap_price')}원 | 수급 {p.get('prog_money')}억")
    print(f" ⚡ 방송용 2등주 후보    : {p.get('second_name')}({p.get('second_code')}) | 종가 {p.get('second_close'):,}원 ({p.get('second_rate')}%)")
    print("-" * 88)
    for test_cash in (96420, 500000):
        plan = eng.select_evening_leader_for_budget(res, orderable_cash_krw=test_cash, allow_top5_affordable_fallback=True, target_tp_pct=6.0)
        if plan.get("ok"):
            sel = plan["selected"]
            print(
                f" 💰 [예수금 {test_cash:>7,}원 플랜] -> {sel['rank_type']}: {sel['name']}({sel['code']}) "
                f"{plan['recommended_qty']}주 @ {plan['ref_close_price']:,}원 -> 익일 +6% 매도가: {plan['target_sell_price']:,}원 (방어선: {plan['stop_loss_price']:,}원)"
            )
        else:
            print(f" 💰 [예수금 {test_cash:>7,}원 플랜] -> 패스 ({plan.get('reason')})")
