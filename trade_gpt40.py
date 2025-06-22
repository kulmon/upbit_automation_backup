import os
import time
import datetime
import pyupbit
import pandas as pd
import openai
import numpy as np
from dotenv import load_dotenv
from telegram import Bot
import requests
import logging
from logging import StreamHandler, FileHandler
import ta

# 환경 설정
load_dotenv()
ACCESS_KEY = os.getenv("UPBIT_ACCESS_KEY")
SECRET_KEY = os.getenv("UPBIT_SECRET_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

openai.api_key = OPENAI_API_KEY

# 로깅 설정
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
logger = logging.getLogger("TradeGPT")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
stream_handler = StreamHandler()
stream_handler.setFormatter(formatter)
file_handler = FileHandler(os.path.join(LOG_DIR, "trade_gpt.log"), encoding="utf-8")
file_handler.setFormatter(formatter)
if not logger.handlers:
    logger.addHandler(stream_handler)
    logger.addHandler(file_handler)

bot = Bot(token=TELEGRAM_BOT_TOKEN)

# 전략 설정
TICKER = "KRW-BTC"  # 거래할 Ticker를 변수로 관리합니다.
STOP_LOSS = -0.01
TAKE_PROFIT = 0.04
VOLATILITY_THRESHOLD = 0.02
RSI_OVERSOLD = 35
RSI_OVERBOUGHT = 70

# 상태 변수
position_open = False
entry_price = 0.0
total_held_volume_btc = 0.0
total_bought_amount_krw = 0.0
first_half_buy_price = 0.0
half_sell_count = 0
half_buy_count = 0
strong_buy_count = 0
last_buy_time = None
last_half_buy_time = None
last_half_sell_time = None
last_strong_sell_time = None
TRADE_COOL_DOWN_SECONDS = 45 * 60
TRADE_BUY_AMOUNT_KRW_STRONG = 20000000
TRADE_BUY_AMOUNT_KRW_HALF = 10000000
TRAILING_PROFIT_SELL = 0.015  # 수익 보호 매도 기준 (1.5%)
TRAILING_PROFIT_HALF_SELL = 0.007  # 부분 매도 기준 (0.7%)

# 시그널 전송 함수
def send_trade_signal(ticker, action, price, amount, reason, strength="약함"):
    global position_open, entry_price, total_held_volume_btc, total_bought_amount_krw
    global strong_buy_count, half_buy_count, half_sell_count, first_half_buy_price
    global last_buy_time, last_half_buy_time, last_half_sell_time, last_strong_sell_time
    try:
        signal_map = {
            "매수": "buy",
            "반매수": "half_buy",
            "반매도": "half_sell",
            "매도": "sell",
            "손절": "sell"
        }
        signal_type = signal_map.get(action)
        if not signal_type:
            logger.warning(f"⚠️ 알 수 없는 시그널 액션: {action}")
            return False

        WEBHOOK_URL = "https://kulmon.ngrok.app/webhook"
        SECRET_TOKEN = "kulmon-secret"
        payload = {
            "token": SECRET_TOKEN,
            "ticker": ticker,
            "signal": signal_type,
            "action": action,
            "price": float(price),
            "amount": amount,
            "reason": reason
        }
        response = requests.post(WEBHOOK_URL, json=payload)
        response.raise_for_status()

        if response.status_code == 200:
            if action in ["매수", "반매수"]:
                bought_volume_btc = amount / price
                new_total_bought_krw = total_bought_amount_krw + amount
                new_total_held_volume_btc = total_held_volume_btc + bought_volume_btc
                entry_price = new_total_bought_krw / new_total_held_volume_btc if new_total_held_volume_btc > 0 else 0.0
                total_bought_amount_krw = new_total_bought_krw
                total_held_volume_btc = new_total_held_volume_btc
                position_open = True
                half_sell_count = 0
                if action == "매수":
                    strong_buy_count += 1
                    last_buy_time = time.time()
                    logger.info("📈 강한 매수로 인해 반매도 횟수가 0으로 초기화되었습니다.")
                elif action == "반매수":
                    half_buy_count += 1
                    if first_half_buy_price == 0.0:
                        first_half_buy_price = price
                    last_half_buy_time = time.time()
                    logger.info("🔄 반매수로 인해 반매도 횟수가 0으로 초기화되었습니다.")
            elif action in ["매도", "손절"]:
                position_open = False
                entry_price = 0.0
                total_held_volume_btc = 0.0
                total_bought_amount_krw = 0.0
                strong_buy_count = 0
                half_buy_count = 0
                half_sell_count = 0
                first_half_buy_price = 0.0
                last_buy_time = None
                last_half_buy_time = None
                last_half_sell_time = None
                if strength == "강함" or action == "손절":
                    last_strong_sell_time = time.time()
            elif action == "반매도":
                sold_volume_btc = amount
                if total_held_volume_btc > sold_volume_btc:
                    total_held_volume_btc -= sold_volume_btc
                    total_bought_amount_krw = total_held_volume_btc * entry_price
                else:
                    logger.warning("⚠️ 보유 수량보다 많은 반매도 시도. 전량 매도로 처리합니다.")
                    position_open = False
                    entry_price = 0.0
                    total_held_volume_btc = 0.0
                    total_bought_amount_krw = 0.0
                    strong_buy_count = 0
                    half_buy_count = 0
                    first_half_buy_price = 0.0
                    half_sell_count = 0

                if position_open:
                    half_sell_count += 1

                last_half_sell_time = time.time()

                if position_open:
                    strong_buy_count = 0
                    half_buy_count = 0
                    first_half_buy_price = 0.0
                    logger.info("🔄 반매도로 인해 매수 관련 카운트들이 초기화되었습니다.")

            logger.info(f"✅ 시그널 전송 성공 및 가상 포지션 업데이트: {action} / {price:.0f} / {amount}")
            return True
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ 웹훅 시그널 전송 실패: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ 시그널 전송 중 오류 발생: {e}")
        return False

# 히든 및 일반 다이버전스 탐지 함수
def detect_hidden_and_regular_divergence(df):
    hidden_bullish = False
    hidden_bearish = False
    regular_bullish = False
    regular_bearish = False

    recent = df[-60:].copy()  # 60봉 데이터 사용
    recent["rsi"] = ta.momentum.RSIIndicator(recent["close"], window=14).rsi()
    recent.dropna(subset=["rsi"], inplace=True)

    if len(recent) < 2:
        return False, False, False, False

    lows = recent.nsmallest(2, "low").sort_index()
    highs = recent.nlargest(2, "high").sort_index()

    if len(lows) >= 2:
        p1, p2 = lows.iloc[0], lows.iloc[1]
        if p1["low"] < p2["low"] and p1["rsi"] > p2["rsi"]:
            hidden_bullish = True

    if len(highs) >= 2:
        p1, p2 = highs.iloc[0], highs.iloc[1]
        if p1["high"] > p2["high"] and p1["rsi"] < p2["rsi"]:
            hidden_bearish = True

    if len(lows) >= 2:
        p1, p2 = lows.iloc[0], lows.iloc[1]
        if p1["low"] > p2["low"] and p1["rsi"] < p2["rsi"]:
            regular_bullish = True

    if len(highs) >= 2:
        p1, p2 = highs.iloc[0], highs.iloc[1]
        if p1["high"] < p2["high"] and p1["rsi"] > p2["rsi"]:
            regular_bearish = True

    return hidden_bullish, hidden_bearish, regular_bullish, regular_bearish

# 최근 급락 후 강한 반등(V자 패턴)을 감지하는 함수
def detect_v_rebound(df, drop_thresh=-0.01, rebound_thresh=0.005):
    if len(df) < 3:
        return False

    recent_drop = df['close'].pct_change().iloc[-3:-1].sum()
    recent_rebound = df['close'].pct_change().iloc[-1]
    volume_increase = df['volume'].iloc[-1] > df['volume'].rolling(5).mean().iloc[-2]

    return (
        recent_drop <= drop_thresh
        and recent_rebound >= rebound_thresh
        and volume_increase
    )

# GPT 판단 요청
def ask_gpt(ticker, indicators):
    up_candles_info = indicators.get("up_candles", "N/A")
    above_5ma_info = indicators.get("above_5ma", "N/A")
    rate_10_info = indicators.get("rate_10", 0)

    system_message = f"""
    당신은 암호화폐 시장, 특히 비트코인({TICKER}) 스윙 매매를 전문으로 하는 AI 트레이딩 전략가입니다.
    **매매 기회를 포착하는 데 있어서 과도한 보수성보다는, 합리적인 위험을 감수하고 잠재적인 기회를 적극적으로 탐색하는 데 중점을 두세요.**
    보조지표, 추세 흐름, 거래량, 다이버전스, 피보나치 등을 종합하여 스윙 매매 시그널을 판단하세요.
    다음 정보를 기반으로 현재 가격이 저점에 가까운지, 과매도 상태인지, 급락 이후 반등 가능성이 있는지를 종합 판단하여 매수, 매도, 관망 중 하나를 선택하세요.

    ■ 판단 기준
    - 현재 가격이 저점에 가까운지, 과매도 상태인지, 하락 이후 반등 가능성이 있는지
    - 또는 고점에 가까운지, 과매수 상태인지, 상승 이후 하락 가능성이 있는지
    - 보조지표와 추세가 일관된 방향을 가리키는지 확인하세요.
    - 다이버전스 (일반 및 히든), 변동성, 이동 평균 교차, RSI, MACD 등 핵심 기술적 조건을 종합 고려하세요.
    - 최소 1% 이상의 가격 변동이 예상될 때만 매매 신호를 제시하세요.

    ※ 다이버전스 개념:
    - **일반 상승 다이버전스**: 가격 저점은 낮아짐, RSI 등은 더 높은 저점 → 하락 추세 반전 시사
    - **일반 하락 다이버전스**: 가격 고점은 높아짐, RSI 등은 더 낮은 고점 → 상승 추세 반전 시사
    - **히든 상승 다이버전스**: 가격 저점은 높아짐, RSI 등은 더 낮은 저점 → 상승 추세 지속
    - **히든 하락 다이버전스**: 가격 고점은 낮아짐, RSI 등은 더 높은 고점 → 하락 추세 지속

    ※ 추세 판단도 반영:
    - 최근 10봉 변화율 ({rate_10_info:.03%})
    - 상승봉 개수 (5봉): {up_candles_info}
    - 5봉 평균 이상 여부: {above_5ma_info}
    - 5봉 변화율: {indicators.get('rate_3', 0):.02%}
    - RSI 지표 ({RSI_OVERSOLD} 이하 과매도 여부)
    - RSI 지표 ({RSI_OVERBOUGHT} 이상 과매수 여부)
    - MACD와 Signal선의 관계 (골든크로스/데드크로스)

    ■ 민감도 조정:

    - 매도:
        - 높음: 다음 조건들 중 **세 가지 이상**이 매우 명확하게 나타날 경우, 시장의 강력한 하락 전환 가능성을 시사하므로 '매도' 시그널과 함께 반드시 '강함' 신뢰도를 반환해야 합니다.
            - RSI 과매수 및 RSI 고점이 낮아짐, 상승 과열, MACD 데드크로스, 히든 하락 다이버전스, 일반 하락 다이버전스
            - 최근 가격 급등 (5봉 변화율 급증) 후 10봉 변화율 둔화 (양수 값 감소) 또는 5봉 변화율이 -0.05% 이하로 급락
            - 가격 고점 형성 후 하락 반전, 최근 상승봉 개수 감소 추세 또는 10봉 변화율이 음수로 전환은 강한 매도 신호입니다.
        - 중간: 상승 탄력 둔화, 저항 돌파 실패, 거래량 감소, RSI 저점이 낮아짐, RSI 고점이 낮아짐, 5봉 평균 이하로 가격 하락 시, 최근 상승봉 개수 감소 추세 또는 5봉 변화율이 음수로 전환 매도를 고려합니다.
        - 낮음: 단순 가격 조정이나 박스권 내 움직임, 최근 10봉 변화율이 안정적인 상승세이거나, 하락세가 미미한 경우 매도하지 않습니다.

    - 매수:
        - 높음: 다음 조건들 중 **세 가지 이상**이 매우 명확하게 나타날 경우
            - RSI 과매도 (예: RSI 40 이하) 및 RSI 저점이 높아짐, 볼린저 밴드 하단 이탈, MACD 골든크로스, 히든 상승 다이버전스, 일반 상승 다이버전스
            - 최근 5봉 가격 변화율이 -0.5% 이하로 급감하여 과매도 상태를 명확히 보인 후, 양수로 전환되거나 가파르게 상승할 때
            - 또는, 10봉 가격 변화율이 -1% 이하의 깊은 하락을 보이다가 5봉 변화율이 양수로 전환되며 10봉 변화율의 음수 폭을 줄이거나 양수로 전환될 때
            - 거래량 급증과 함께 강한 반등 캔들(장대 양봉)이 출현할 때 적극적으로 매수합니다.
        - 중간: 하락세 둔화, 반등 캔들 출현, 지지선 근접, RSI 고점이 높아짐, RSI 저점이 높아짐, 5봉 평균 이상으로 가격 상승 시 매수를 고려합니다.
        - 낮음: 명확한 반등 신호 없이 하락세가 지속, 최근 10봉 변화율이 안정적인 하락세이거나, 5봉 변화율이 여전히 강한 음수일 경우 매수를 보류합니다.

    신뢰도는 반드시 '강함', '중간', '약함' 중 하나로 판단하세요.
    첫 줄: '매수', '매도', '관망' 중 하나만
    두 번째 줄: 신뢰도 ('강함', '중간', '약함') 중 하나
    세 번째 줄: 간단한 사유
    """

    prompt = f"""
    코인: {ticker}
    종가: {indicators.get('close', 0)}
    RSI: {indicators.get('rsi', 50):.2f} (과매도: {indicators.get('oversold', False)}, 과매수: {indicators.get('overbought', False)})
    MACD: {indicators.get('macd', 0):.2f}, Signal: {indicators.get('macd_signal', 0):.2f}
    MACD 골든크로스 여부: {indicators.get('macd_cross', False)}
    DI: {indicators.get('di', 0):.2f}
    MA20 > MA60: {indicators.get('ma_cross', False)}
    볼린저밴드 상단: {indicators.get('bb_upper', 0):.0f}, 하단: {indicators.get('bb_lower', 0):.0f}
    변동성 (10봉 연율화): {indicators.get('volatility', 0):.4f}
    거래량 변화율: {indicators.get('volume_change', 0):.2%}
    5봉 가격 변화율: {indicators.get('rate_3', 0):.02%}
    10봉 가격 변화율: {rate_10_info:.03%}
    과매도 ({RSI_OVERSOLD} 이하): {indicators.get('oversold', False)}
    과매수 ({RSI_OVERBOUGHT} 이상): {indicators.get('overbought', False)}
    피보나치 레벨: 0.0={indicators.get('fib_0', 0):.0f}, 38.2={indicators.get('fib_382', 0):.0f}, 50.0={indicators.get('fib_500', 0):.0f}, 61.8={indicators.get('fib_618', 0):.0f}, 78.6={indicators.get('fib_786', 0):.0f}, 88.6={indicators.get('fib_886', 0):.0f}, 100.0={indicators.get('fib_100', 0):.0f}
    히든 상승 다이버전스: {indicators.get('hidden_bullish', False)}
    히든 하락 다이버전스: {indicators.get('hidden_bearish', False)}
    일반 상승 다이버전스: {indicators.get('regular_bullish', False)}
    일반 하락 다이버전스: {indicators.get('regular_bearish', False)}
    """

    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()


def send_telegram_message(gpt_decision, gpt_strength, gpt_reason, final_decision, final_strength, final_reason):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    message = f"""🤖 판단 결과 ({now})

--- GPT 분석 ---
결정: {gpt_decision}
신뢰도: {gpt_strength}
사유: {gpt_reason}

--- 최종 결정 ---
결정: {final_decision}
신뢰도: {final_strength}
사유: {final_reason}
"""

    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
    except Exception as e:
        logger.warning(f"❌ 텔레그램 전송 실패: {e}")


while True:
    try:
        df = pyupbit.get_ohlcv(TICKER, interval="minute60", count=480)
        if df is None or len(df) < 100:
            logger.warning(f"📉 시간봉 데이터 부족. 현재 {len(df) if df is not None else 0}개. 잠시 후 재시도.")
            time.sleep(60)
            continue

        todays_open_price = None
        try:
            df_day = pyupbit.get_ohlcv(TICKER, interval="day", count=1)
            if df_day is not None and not df_day.empty:
                todays_open_price = df_day['open'].iloc[0]
                logger.info(f"📈 당일 시가: {todays_open_price:,.0f}원")
        except Exception as e:
            logger.warning(f"⚠️ 당일 시가 데이터 조회 중 오류 발생: {e}")

        df['rsi'] = ta.momentum.RSIIndicator(close=df['close'], window=14).rsi()
        macd = ta.trend.MACD(df['close'])
        df['macd'] = macd.macd()
        df['macd_signal'] = macd.macd_signal()
        df['macd_cross'] = (df['macd'] > df['macd_signal']).fillna(False)
        bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_upper'] = bb.bollinger_hband().fillna(df['close'])
        df['bb_lower'] = bb.bollinger_lband().fillna(df['close'])
        df['ma_20'] = df['close'].rolling(window=20).mean()
        df['ma_60'] = df['close'].rolling(window=60).mean()
        df['ma_cross'] = (df['ma_20'] > df['ma_60']).fillna(False)
        df['volume_change'] = df['volume'].pct_change().fillna(0)
        df['volatility'] = df['close'].pct_change().rolling(window=10).std().fillna(0) * (252**0.5)
        df['rate_3'] = df['close'].pct_change(periods=5).fillna(0)
        df['oversold'] = (df['rsi'] < RSI_OVERSOLD).fillna(False)
        df['overbought'] = (df['rsi'] > RSI_OVERBOUGHT).fillna(False)
        df['rate_10'] = df['close'].pct_change(periods=10).fillna(0)
        df['up_candles'] = (df['close'] > df['open']).rolling(window=5).sum().fillna(0)
        df['above_5ma'] = (df['close'] > df['close'].rolling(window=5).mean()).fillna(False)
        adx_indicator = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
        df['plus_di'] = adx_indicator.adx_pos().fillna(0)
        df['minus_di'] = adx_indicator.adx_neg().fillna(0)
        df['di'] = (df['plus_di'] - df['minus_di']).fillna(0)

        recent_df_for_fib = df[-100:].copy()
        high = recent_df_for_fib['high'].max()
        low = recent_df_for_fib['low'].min()
        price_diff = high - low
        fib_0 = high
        fib_382 = high - price_diff * 0.382
        fib_500 = high - price_diff * 0.5
        fib_618 = high - price_diff * 0.618
        fib_786 = high - price_diff * 0.786
        fib_886 = high - price_diff * 0.886
        fib_100 = low

        hidden_bullish, hidden_bearish, regular_bullish, regular_bearish = detect_hidden_and_regular_divergence(df)
        v_rebound = detect_v_rebound(df)
        latest = df.iloc[-1]

        indicators = {
            "close": latest.get('close', 0),
            "macd": latest.get('macd', 0),
            "macd_signal": latest.get('macd_signal', 0),
            "macd_cross": latest.get('macd_cross', False),
            "rsi": latest.get('rsi', 50),
            "di": latest.get('di', 0),
            "ma_cross": latest.get('ma_cross', False),
            "volume_change": latest.get('volume_change', 0),
            "bb_upper": latest.get('bb_upper', 0),
            "bb_lower": latest.get('bb_lower', 0),
            "volatility": latest.get('volatility', 0),
            "rate_3": latest.get('rate_3', 0),
            "rate_10": latest.get('rate_10', 0),
            "oversold": latest.get('oversold', False),
            "overbought": latest.get('overbought', False),
            "fib_0": fib_0,
            "fib_382": fib_382,
            "fib_500": fib_500,
            "fib_618": fib_618,
            "fib_786": fib_786,
            "fib_886": fib_886,
            "fib_100": fib_100,
            "hidden_bullish": hidden_bullish,
            "hidden_bearish": hidden_bearish,
            "regular_bullish": regular_bullish,
            "regular_bearish": regular_bearish,
            "v_rebound": v_rebound,
            "up_candles": latest.get('up_candles', 0),
            "above_5ma": latest.get('above_5ma', False)
        }

        gpt_result = ask_gpt(TICKER, indicators)
        lines = gpt_result.splitlines()
        gpt_decision = lines[0].strip() if lines else "관망"
        gpt_strength = lines[1].replace("신뢰도:", "").strip() if len(lines) > 1 else "약함"
        gpt_reason = lines[2].strip() if len(lines) > 2 else "사유 없음"

        strength_score = 0
        final_strength = "약함"
        if gpt_decision == "매수":
            if indicators.get('volatility', 0) > VOLATILITY_THRESHOLD:
                strength_score += 1
            if indicators.get('rsi', 50) < RSI_OVERSOLD:
                strength_score += 1
            if indicators.get('rate_3', 0) > 0.005:
                strength_score += 1
            if indicators.get('macd', 0) > indicators.get('macd_signal', 0):
                strength_score += 2
            if indicators.get('hidden_bullish', False):
                strength_score += 1
            if indicators.get('regular_bullish', False):
                strength_score += 1
            if indicators.get('v_rebound', False):
                strength_score += 1
            if indicators.get('above_5ma', False):
                strength_score += 1
            if strength_score >= 6:
                final_strength = "강함"
            elif 3 <= strength_score < 6:
                final_strength = "중간"
        elif gpt_decision == "매도":
            if indicators.get('volatility', 0) > VOLATILITY_THRESHOLD:
                strength_score += 1
            if indicators.get('rsi', 50) > RSI_OVERBOUGHT:
                strength_score += 1
            if indicators.get('rate_3', 0) < -0.003:
                strength_score += 1
            if indicators.get('macd', 0) < indicators.get('macd_signal', 0):
                strength_score += 1
            if indicators.get('hidden_bearish', False):
                strength_score += 1
            if indicators.get('regular_bearish', False):
                strength_score += 1
            if not indicators.get('above_5ma', False):
                strength_score += 1
            if gpt_strength == "강함":
                strength_score += 1
            if strength_score >= 5:
                final_strength = "강함"
            elif 4 <= strength_score < 5:
                final_strength = "중간"

        current_price = indicators["close"]
        logger.info(
            f"📊 현재 상태: PosOpen={position_open}, Entry={entry_price:,.0f}, "
            f"BTC={total_held_volume_btc:.8f}, KRW={total_bought_amount_krw:,.0f}, "
            f"Price={current_price:,.0f}, GPT={gpt_decision}/{gpt_strength}, Final={final_strength}"
        )

        action_taken = None
        action_reason = None
        trade_amount = 0

        if position_open:
            profit = (current_price - entry_price) / entry_price if entry_price != 0 else 0
            if profit <= STOP_LOSS:
                action_taken = "손절"
                action_reason = f"손절 조건 충족 (손실율: {profit:.2%})"
                trade_amount = total_held_volume_btc
            elif profit >= TAKE_PROFIT:
                action_taken = "매도"
                action_reason = f"익절 조건 충족 (수익율: {profit:.2%})"
                trade_amount = total_held_volume_btc
            elif profit >= TRAILING_PROFIT_SELL and (
                indicators.get('rate_3', 0) < -0.002 or not indicators.get('ma_cross', True)
            ):
                action_taken = "매도"
                action_reason = f"수익 보호 매도 (수익율: {profit:.2%})"
                trade_amount = total_held_volume_btc
            elif (
                profit >= TRAILING_PROFIT_HALF_SELL and indicators.get('rate_3', 0) < 0 and (
                    last_half_sell_time is None or (time.time() - last_half_sell_time) >= TRADE_COOL_DOWN_SECONDS
                ) and half_sell_count < 5
            ):
                action_taken = "반매도"
                action_reason = f"부분 수익 실현 (수익율: {profit:.2%})"
                trade_amount = round(total_held_volume_btc / 2, 8)
                if trade_amount * current_price < 5000:
                    trade_amount = total_held_volume_btc
            elif gpt_decision == "매도":
                lower_bound = entry_price * 0.999
                upper_bound = entry_price * 1.001
                if lower_bound <= current_price <= upper_bound:
                    logger.info(
                        f"🚫 현재가({current_price:,.0f})가 평단가({entry_price:,.0f})의 +-0.1% 이내이므로 매도/반매도 무시."
                    )
                elif final_strength == "강함":
                    action_taken = "매도"
                    action_reason = f"강함 매도: {gpt_reason}"
                    trade_amount = total_held_volume_btc
                elif final_strength == "중간" and half_sell_count < 5:
                    if last_half_sell_time is None or (
                        time.time() - last_half_sell_time
                    ) >= TRADE_COOL_DOWN_SECONDS:
                        action_taken = "반매도"
                        action_reason = f"중간 매도 (반매도 {half_sell_count + 1}회차): {gpt_reason}"
                        trade_amount = round(total_held_volume_btc / 2, 8)
                        if trade_amount * current_price < 5000:
                            trade_amount = total_held_volume_btc
                    else:
                        logger.info("⏳ 반매도 쿨다운. 스킵.")

        bottom_detected = (
            (
                indicators.get('oversold', False)
                and indicators.get('macd_cross', False)
                and (
                    indicators.get('hidden_bullish', False)
                    or indicators.get('regular_bullish', False)
                )
            )
            or indicators.get('v_rebound', False)
        )

        if action_taken is None and gpt_decision == "매수":
            if (
                indicators.get('rate_3', 0) < -0.003
                or not indicators.get('ma_cross', True)
            ) and not bottom_detected:
                logger.info("🚫 하락 추세에서 추가 매수 제한.")
            else:
                if (
                    todays_open_price is not None
                    and current_price <= (todays_open_price * 0.99)
                ):
                    logger.info(
                        f"🚫 현재가({current_price:,.0f})가 당일 시가({todays_open_price:,.0f}) 대비 -1% 이하이므로 매수 금지."
                    )
                else:
                    if (
                        last_strong_sell_time is not None
                        and (time.time() - last_strong_sell_time) < TRADE_COOL_DOWN_SECONDS
                    ):
                        remaining_cooldown = int(
                            TRADE_COOL_DOWN_SECONDS - (time.time() - last_strong_sell_time)
                        )
                        logger.info(
                            f"🚫 강한 매도 후 {remaining_cooldown}초 남음. 매수 금지."
                        )
                    else:
                        if final_strength == "강함" and strong_buy_count < 10:
                            if last_buy_time is None or (
                                time.time() - last_buy_time
                            ) >= TRADE_COOL_DOWN_SECONDS:
                                if not position_open or current_price < entry_price * (1 + 0.003):
                                    action_taken = "매수"
                                    trade_amount = TRADE_BUY_AMOUNT_KRW_STRONG
                                    action_reason = f"강함 매수 ({strong_buy_count + 1}회차): {gpt_reason}"
                                else:
                                    logger.info(
                                        "🚫 강함 매수 조건 불충족: 현재가가 평단가보다 0.3% 이상 높음."
                                    )
                            else:
                                logger.info("⏳ 강함 매수 쿨다운. 스킵.")
                        elif final_strength == "중간":
                            if last_half_buy_time is None or (
                                time.time() - last_half_buy_time
                            ) >= TRADE_COOL_DOWN_SECONDS:
                                if half_buy_count < 15:
                                    if not position_open or (
                                        entry_price > 0
                                        and current_price <= entry_price * (1 + 0.005)
                                    ):
                                        action_taken = "반매수"
                                        trade_amount = TRADE_BUY_AMOUNT_KRW_HALF
                                        action_reason = (
                                            f"중간 매수 (반매수, {half_buy_count + 1}회차): {gpt_reason}"
                                        )
                                    else:
                                        logger.info(
                                            "🚫 반매수 조건 불충족: 현재가가 평단가보다 0.5% 이상 높음."
                                        )
                                else:
                                    logger.info("🚫 반매수 횟수 제한(15회) 도달.")
                            else:
                                logger.info("⏳ 반매수 쿨다운. 스킵.")

        final_decision_text = "관망"
        final_reason_text = gpt_reason
        log_strength = final_strength

        if action_taken:
            final_decision_text = action_taken
            final_reason_text = action_reason
            signal_sent_success = send_trade_signal(
                TICKER,
                action_taken,
                current_price,
                trade_amount,
                action_reason,
                final_strength,
            )
            signal_status_text = (
                "📡 시그널 전송 성공" if signal_sent_success else "❌ 시그널 전송 실패"
            )
            final_log_message = (
                f"📊 최종 결정: {final_decision_text} | 신뢰도: {log_strength} | 사유: {final_reason_text} | "
                f"{signal_status_text} | GPT 판단: {gpt_decision}/{gpt_strength} ({gpt_reason})"
            )
        elif position_open:
            final_decision_text = "보유"
            final_reason_text = (
                f"보유: 매매 조건 불충족 (GPT 판단: {gpt_decision}/{gpt_strength}, 사유: {gpt_reason})"
            )
            if action_reason:
                final_reason_text = f"보유: {action_reason}"
            final_log_message = (
                f"📊 최종 결정: {final_decision_text} | 신뢰도: {log_strength} | 사유: {final_reason_text}"
            )
        else:
            final_decision_text = "관망"
            final_reason_text = (
                f"관망: 매수/매도 조건 불충족 (GPT 판단: {gpt_decision}/{gpt_strength}, 사유: {gpt_reason})"
            )
            final_log_message = (
                f"📊 최종 결정: {final_decision_text} | 신뢰도: {log_strength} | 사유: {final_reason_text}"
            )

        logger.info(final_log_message)
        send_telegram_message(
            gpt_decision,
            gpt_strength,
            gpt_reason,
            final_decision_text,
            log_strength,
            final_reason_text,
        )

        time.sleep(60 * 10)

    except Exception as e:
        logger.error(f"❌ 메인 루프 실행 중 오류 발생: {e}", exc_info=True)
        time.sleep(60)
