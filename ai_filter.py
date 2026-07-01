import os
import json
import requests
import time

try:
    import google.generativeai as genai  # pyrefly: ignore [missing-import]
except ImportError:
    genai = None

class AIFilter:
    def __init__(self, provider="google", model="gemini-3.5-flash", shadow_mode=True):
        self.provider = provider
        self.model = model
        self.shadow_mode = shadow_mode
        self.timeout = 5
        
        if self.provider == "google":
            api_key = os.getenv("GOOGLE_AI_API_KEY")
            if not api_key:
                print("[AIFilter] Warning: GOOGLE_AI_API_KEY not found.", flush=True)
            elif genai:
                genai.configure(api_key=api_key)
            else:
                print("[AIFilter] Warning: google-generativeai module not installed.", flush=True)

    def _build_prompt(self, symbol, side, indicators_dict, ohlcv_5bars):
        ema_stack = "EMA7 > EMA25 > EMA99" if side == "LONG" else "EMA7 < EMA25 < EMA99"
        rsi_rule = "50-70" if side == "LONG" else "30-50"
        
        prompt = f"""You are a strict Trading Signal Validator. Follow rules ONLY, no personal opinion.
You must return a valid JSON object.

APPROVE {side} for {symbol} if ALL conditions are met:
- EMA stack: {ema_stack} (Price should respect the trend direction)
- RSI: {rsi_rule} (Not overbought/oversold against the trend)
- ADX >= 25 (Strong trend is present)
- Volatility: ATR is not excessively large compared to recent candles

Data:
EMA7={indicators_dict.get('ema7', 0):.4f} | EMA25={indicators_dict.get('ema25', 0):.4f} | EMA99={indicators_dict.get('ema99', 0):.4f}
RSI={indicators_dict.get('rsi', 0):.1f} | ADX={indicators_dict.get('adx', 0):.1f} | ATR={indicators_dict.get('atr', 0):.4f}
StochRSI={indicators_dict.get('stoch_rsi', 0):.2f} | Volume Ratio={indicators_dict.get('volume_ratio', 0):.2f}

Last 5 candles (OHLCV):
{json.dumps(ohlcv_5bars, indent=2)}

Also suggest dynamic parameters if you approve:
- sl_atr_mult: 1.0 to 2.5 (wider if highly volatile, tighter if clean trend)
- tp_rr_ratio: 1.0 to 2.5 (higher if strong trend and good volume)

Reply ONLY with this exact JSON schema:
{{
    "approved": boolean,
    "confidence": integer (0-100),
    "sl_atr_mult": float,
    "tp_rr_ratio": float,
    "reason": string (max 15 words)
}}"""
        return prompt

    def _call_openrouter(self, prompt):
        api_key = os.getenv("OPENROUTER_AI_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_AI_API_KEY not found.")
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "http://localhost",
            "X-Title": "CryptoScannerBot"
        }
        
        payload = {
            "model": self.model,
            "temperature": 0.0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]
        }
        
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout
        )
        resp.raise_for_status()
        
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)

    def _call_google(self, prompt):
        if not genai:
            raise ImportError("google-generativeai module is missing. Install with: pip install google-generativeai")
        
        model_name = self.model
        if model_name.startswith("google/"): 
            model_name = model_name.split("/")[1]
            
        if ":" in model_name: 
            model_name = "gemini-3.5-flash" 

        model = genai.GenerativeModel(
            model_name=model_name,
            generation_config=genai.GenerationConfig(
                temperature=0.0,
                response_mime_type="application/json"
            )
        )
        
        response = model.generate_content(prompt, request_options={"timeout": self.timeout})
        return json.loads(response.text)

    def analyze_signal(self, symbol, side, indicators_dict, ohlcv_5bars):
        prompt = self._build_prompt(symbol, side, indicators_dict, ohlcv_5bars)
        
        fallback_result = {
            "approved": True, 
            "confidence": 0,
            "sl_atr_mult": None,
            "tp_rr_ratio": None,
            "reason": "API Error / Timeout Fallback"
        }

        try:
            start_time = time.time()
            if self.provider == "openrouter":
                result = self._call_openrouter(prompt)
            else:
                result = self._call_google(prompt)
                
            elapsed = time.time() - start_time
            print(f"[AIFilter] {symbol} {side} analysis completed in {elapsed:.2f}s. Approved: {result.get('approved', False)}, Confidence: {result.get('confidence', 0)}", flush=True)
            
            result.setdefault('sl_atr_mult', None)
            result.setdefault('tp_rr_ratio', None)
            
            return result
            
        except requests.exceptions.Timeout:
            print(f"[AIFilter] Error: API Timeout (> {self.timeout}s). Using fallback.", flush=True)
            return fallback_result
        except json.JSONDecodeError:
            print(f"[AIFilter] Error: Invalid JSON response. Using fallback.", flush=True)
            return fallback_result
        except Exception as e:
            print(f"[AIFilter] Error: {type(e).__name__} - {e}. Using fallback.", flush=True)
            return fallback_result

_ai_filter = None

def get_ai_filter():
    global _ai_filter
    import config
    
    if _ai_filter is None and getattr(config, 'AI_FILTER_ENABLED', False):
        _ai_filter = AIFilter(
            provider=getattr(config, 'AI_FILTER_PROVIDER', 'google'),
            model=getattr(config, 'AI_FILTER_MODEL', 'gemini-2.0-flash'),
            shadow_mode=getattr(config, 'AI_FILTER_SHADOW_MODE', True)
        )
    return _ai_filter
