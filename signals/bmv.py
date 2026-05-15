    def _apply_llm(self, signal: BMVSignal, fear_greed: dict | None) -> BMVSignal:
        if not self.use_llm:
            return signal

        try:
            import google.generativeai as genai
            import json

            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            model = genai.GenerativeModel("gemini-2.0-flash")

            fg_value = fear_greed.get("value", 50) if fear_greed else 50
            fg_class = fear_greed.get("classification", "Neutral") if fear_greed else "Neutral"
            fg_delta = fear_greed.get("delta_24h", 0) if fear_greed else 0

            prompt = f"""You are a crypto trading risk analyst. A BMV (Breakout-Momentum-Volume) signal fired. Evaluate it strictly.

    Signal:
    - Asset: {signal.symbol}
    - Direction: {signal.direction}
    - Entry: {signal.entry_price}
    - Breakout level: {signal.breakout_level}
    - Volume ratio: {signal.vol_ratio:.1f}x (min: 2.2x)
    - RSI: {signal.rsi:.1f}
    - Trend score: {signal.trend_score:+.2f}
    - Stop loss: {signal.stop_loss}
    - Take profit: {signal.take_profit}
    - Fear & Greed: {fg_value} ({fg_class}), delta24h: {fg_delta:+d}

    Check:
    1. R/R ratio acceptable? (TP-Entry)/(Entry-SL) >= 1.5
    2. Sentiment aligned with direction?
    3. RSI overbought >80 for LONG or oversold <20 for SHORT?
    4. Fake breakout risk?

    Reply ONLY with raw JSON, no markdown, no backticks:
    {{"verdict": "CONFIRM", "reasoning": "one sentence", "risk_reward": 0.0}}"""

            response = model.generate_content(prompt)
            raw = response.text.strip().replace("```json", "").replace("```", "").strip()
            parsed = json.loads(raw)

            signal.llm_verdict = parsed.get("verdict", "WAIT")
            signal.llm_reasoning = parsed.get("reasoning", "")

            logger.info(
                f"Gemini [{signal.symbol} {signal.direction}]: "
                f"{signal.llm_verdict} — {signal.llm_reasoning}"
            )

        except Exception as e:
            logger.warning(f"Gemini filter failed: {e}")
            signal.llm_verdict = "N/A"
            signal.llm_reasoning = "LLM unavailable"

        return signal

        try:
            fg_value = fear_greed.get("value", 50) if fear_greed else 50
            fg_class = fear_greed.get("classification", "Neutral") if fear_greed else "Neutral"
            fg_delta = fear_greed.get("delta_24h", 0) if fear_greed else 0

            prompt = f"""You are a crypto trading risk analyst. A BMV (Breakout-Momentum-Volume) signal has fired. Evaluate it.

Signal Details:
- Asset: {signal.symbol}
- Direction: {signal.direction}
- Entry: {signal.entry_price}
- Breakout level: {signal.breakout_level}
- Volume ratio: {signal.vol_ratio:.1f}x (threshold: 2.5x)
- RSI: {signal.rsi:.1f}
- Trend score: {signal.trend_score:+.2f} (-1 bearish to +1 bullish)
- Stop loss: {signal.stop_loss}
- Take profit: {signal.take_profit}
- Fear & Greed: {fg_value} ({fg_class}), Δ24h: {fg_delta:+d}

Rules to check:
1. Is the risk/reward ratio acceptable? (TP-Entry)/(Entry-SL) should be >= 1.5
2. Does Fear & Greed sentiment align with the direction?
3. Is RSI dangerously overbought (>80) for LONG or oversold (<20) for SHORT?
4. Any reason this could be a fake breakout?

Respond ONLY with a JSON object, no markdown:
{{"verdict": "CONFIRM" or "FADE" or "WAIT", "reasoning": "one sentence explanation", "risk_reward": <float>}}"""

            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"Content-Type": "application/json"},
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 200,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=10,
            )

            resp.raise_for_status()

            raw = resp.json()["content"][0]["text"].strip()
            parsed = json.loads(raw)

            signal.llm_verdict = parsed.get("verdict", "WAIT")
            signal.llm_reasoning = parsed.get("reasoning", "")

            logger.info(
                f"LLM verdict for {signal.symbol} {signal.direction}: "
                f"{signal.llm_verdict} — {signal.llm_reasoning}"
            )

        except Exception as e:
            logger.warning(f"LLM filter failed (using signal as-is): {e}")
            signal.llm_verdict = "N/A"
            signal.llm_reasoning = "LLM unavailable"

        return signal
