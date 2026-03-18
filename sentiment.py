#!/usr/bin/env python3
"""
Cryptocurrency sentiment analysis via news headlines.
Fetches latest crypto news, analyzes sentiment via OpenAI LLM.
"""

import os
import logging
import time
import json
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta
import sqlite3
import hashlib

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import feedparser
    import openai
except ImportError as e:
    logger.error(f"Missing required packages: {e}")
    logger.error("Run: pip install feedparser openai")

class CryptoSentiment:
    """Cryptocurrency sentiment analyzer"""
    
    # Symbol to keyword mapping
    SYMBOL_KEYWORDS = {
        "BTC-USD": ["Bitcoin", "BTC"],
        "ETH-USD": ["Ethereum", "ETH"],
        "XRP-USD": ["XRP", "Ripple"],
        "BNB-USD": ["BNB", "Binance Coin"],
        "SOL-USD": ["Solana", "SOL"],
        "ADA-USD": ["Cardano", "ADA"],
        "AVAX-USD": ["Avalanche", "AVAX"],
        "DOGE-USD": ["Dogecoin", "DOGE"],
        "DOT-USD": ["Polkadot", "DOT"],
        "LINK-USD": ["Chainlink", "LINK"],
        "LTC-USD": ["Litecoin", "LTC"],
        "UNI-USD": ["Uniswap", "UNI"],
        "SHIB-USD": ["Shiba Inu", "SHIB"],
        "BONK-USD": ["Bonk"],
        "TROLL-USD": ["Troll"],
        "FET-USD": ["Fetch.ai", "FET"],
        "AMP-USD": ["Amp"],
        "GRT-USD": ["The Graph", "GRT"],
    }
    
    # RSS feeds for crypto news
    RSS_FEEDS = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://decrypt.co/feed",
        "https://cryptoslate.com/feed/",
        "https://news.bitcoin.com/feed/",
    ]
    
    def __init__(self, db_path: str = "crypto_data.db", openai_api_key: str = None):
        """Initialize sentiment analyzer"""
        self.db_path = db_path
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY not provided and not in environment")
        
        openai.api_key = self.openai_api_key
        
        # Initialize database
        self.init_database()
        
        # Cache for recent sentiment (avoid repeated API calls)
        self.sentiment_cache = {}  # symbol -> (timestamp, score, summary)
        self.cache_ttl = 3600  # 1 hour
        
        # News cache (feed level)
        self.news_cache = {}
        self.news_cache_ttl = 300  # 5 minutes
        
        logger.info("CryptoSentiment initialized")
    
    def init_database(self):
        """Initialize sentiment database table"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS sentiment_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            score REAL NOT NULL,
            confidence REAL,
            summary TEXT,
            sources TEXT,
            headline_count INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, timestamp)
        )
        ''')
        
        # Index for queries
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_timestamp 
        ON sentiment_scores(symbol, timestamp)
        ''')
        
        conn.commit()
        conn.close()
    
    def fetch_news_for_symbol(self, symbol: str, max_headlines: int = 10) -> List[Dict]:
        """Fetch recent news headlines for a symbol"""
        keywords = self.SYMBOL_KEYWORDS.get(symbol, [])
        if not keywords:
            logger.warning(f"No keywords mapped for symbol {symbol}")
            return []
        
        # Check cache first
        cache_key = f"{symbol}_{int(time.time() // self.news_cache_ttl)}"
        if cache_key in self.news_cache:
            return self.news_cache[cache_key]
        
        all_headlines = []
        
        for feed_url in self.RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:20]:  # Limit per feed
                    title = entry.get('title', '')
                    summary = entry.get('summary', '')
                    link = entry.get('link', '')
                    published = entry.get('published', '')
                    
                    # Check if any keyword appears in title or summary
                    text = f"{title} {summary}".lower()
                    if any(keyword.lower() in text for keyword in keywords):
                        all_headlines.append({
                            'title': title,
                            'summary': summary,
                            'link': link,
                            'published': published,
                            'source': feed_url
                        })
                        
                        if len(all_headlines) >= max_headlines:
                            break
                
                if len(all_headlines) >= max_headlines:
                    break
                    
            except Exception as e:
                logger.error(f"Error parsing feed {feed_url}: {e}")
        
        # Cache results
        self.news_cache[cache_key] = all_headlines
        
        logger.info(f"Fetched {len(all_headlines)} headlines for {symbol}")
        return all_headlines
    
    def analyze_sentiment(self, headlines: List[Dict]) -> Tuple[float, float, str]:
        """
        Analyze sentiment of headlines using OpenAI.
        Returns: (score [-1 to +1], confidence [0-1], summary)
        """
        if not headlines:
            return 0.0, 0.0, "No recent news"
        
        # Prepare prompt
        headlines_text = "\n".join([
            f"{i+1}. {h['title']} ({h['source']})" 
            for i, h in enumerate(headlines[:5])  # Limit to 5 headlines
        ])
        
        prompt = f"""Analyze the sentiment of these cryptocurrency news headlines.
        Return a JSON object with:
        - "score": number from -1.0 (very bearish) to +1.0 (very bullish)
        - "confidence": number from 0.0 to 1.0 (how confident you are in the score)
        - "summary": 2-3 sentence summary of the overall sentiment
        
        Headlines:
        {headlines_text}
        
        JSON response:"""
        
        try:
            response = openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a financial sentiment analyst. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=300
            )
            
            content = response.choices[0].message.content.strip()
            
            # Parse JSON response
            import json
            result = json.loads(content)
            
            score = float(result.get("score", 0.0))
            confidence = float(result.get("confidence", 0.5))
            summary = result.get("summary", "No summary")
            
            # Clamp score
            score = max(-1.0, min(1.0, score))
            
            return score, confidence, summary
            
        except Exception as e:
            logger.error(f"OpenAI sentiment analysis failed: {e}")
            return 0.0, 0.0, f"Analysis failed: {e}"
    
    def get_symbol_sentiment(self, symbol: str, force_refresh: bool = False) -> Dict:
        """
        Get sentiment for a symbol, using cache or fetching new.
        Returns dict with keys: score, confidence, summary, headline_count, timestamp
        """
        # Check cache first
        if not force_refresh and symbol in self.sentiment_cache:
            timestamp, score, summary = self.sentiment_cache[symbol]
            if time.time() - timestamp < self.cache_ttl:
                return {
                    'score': score,
                    'confidence': 0.7,  # assumed
                    'summary': summary,
                    'headline_count': 1,
                    'timestamp': timestamp,
                    'cached': True
                }
        
        # Fetch news
        headlines = self.fetch_news_for_symbol(symbol, max_headlines=5)
        
        if not headlines:
            # No news - return neutral
            result = {
                'score': 0.0,
                'confidence': 0.0,
                'summary': "No recent news found",
                'headline_count': 0,
                'timestamp': int(time.time()),
                'cached': False
            }
        else:
            # Analyze sentiment
            score, confidence, summary = self.analyze_sentiment(headlines)
            
            result = {
                'score': score,
                'confidence': confidence,
                'summary': summary,
                'headline_count': len(headlines),
                'timestamp': int(time.time()),
                'cached': False
            }
            
            # Store in cache
            self.sentiment_cache[symbol] = (result['timestamp'], score, summary)
            
            # Store in database
            self.store_sentiment(symbol, result)
        
        return result
    
    def store_sentiment(self, symbol: str, sentiment: Dict):
        """Store sentiment result in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
            INSERT OR REPLACE INTO sentiment_scores
            (symbol, timestamp, score, confidence, summary, sources, headline_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                symbol,
                sentiment['timestamp'],
                sentiment['score'],
                sentiment.get('confidence', 0.5),
                sentiment['summary'],
                'RSS',
                sentiment['headline_count']
            ))
            
            conn.commit()
            logger.info(f"Stored sentiment for {symbol}: {sentiment['score']:.2f}")
            
        except Exception as e:
            logger.error(f"Error storing sentiment for {symbol}: {e}")
        finally:
            conn.close()
    
    def get_recent_sentiment(self, symbol: str, hours: int = 24) -> Optional[Dict]:
        """Get most recent sentiment from database (within specified hours)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            cutoff = int(time.time()) - (hours * 3600)
            
            cursor.execute('''
            SELECT timestamp, score, confidence, summary, headline_count
            FROM sentiment_scores
            WHERE symbol = ? AND timestamp > ?
            ORDER BY timestamp DESC
            LIMIT 1
            ''', (symbol, cutoff))
            
            row = cursor.fetchone()
            if row:
                return {
                    'timestamp': row[0],
                    'score': row[1],
                    'confidence': row[2],
                    'summary': row[3],
                    'headline_count': row[4],
                    'source': 'database'
                }
            else:
                return None
                
        except Exception as e:
            logger.error(f"Error fetching sentiment for {symbol}: {e}")
            return None
        finally:
            conn.close()
    
    def combine_with_technical_signal(self, technical_score: float, sentiment_score: float) -> float:
        """
        Combine technical and sentiment scores.
        Returns weighted score (0.7 technical, 0.3 sentiment) normalized to -1..+1
        """
        # Normalize technical score from 0..1 to -1..+1 if needed
        # Assume technical_score is already in -1..+1 range
        combined = (0.7 * technical_score) + (0.3 * sentiment_score)
        return max(-1.0, min(1.0, combined))
    
    def generate_sentiment_alert(self, symbol: str, sentiment: Dict, technical_signal: str) -> str:
        """Generate human-readable alert combining sentiment and technical signal"""
        score = sentiment['score']
        summary = sentiment['summary']
        headlines = sentiment['headline_count']
        
        if score > 0.3:
            sentiment_emoji = "📈"
            sentiment_text = "bullish"
        elif score < -0.3:
            sentiment_emoji = "📉"
            sentiment_text = "bearish"
        else:
            sentiment_emoji = "➡️"
            sentiment_text = "neutral"
        
        alert = f"{sentiment_emoji} **{symbol} Sentiment**: {sentiment_text} ({score:.2f})\n"
        alert += f"• Technical: {technical_signal}\n"
        
        if headlines > 0:
            alert += f"• News: {headlines} recent headlines\n"
            if len(summary) < 150:  # Avoid too long
                alert += f"• Summary: {summary}\n"
        
        return alert


def test_sentiment():
    """Test function"""
    import sys
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY environment variable not set")
        sys.exit(1)
    
    analyzer = CryptoSentiment(openai_api_key=api_key)
    
    # Test with BTC
    print("Testing sentiment for BTC-USD...")
    sentiment = analyzer.get_symbol_sentiment("BTC-USD")
    
    print(f"Score: {sentiment['score']:.2f}")
    print(f"Confidence: {sentiment['confidence']:.2f}")
    print(f"Headlines: {sentiment['headline_count']}")
    print(f"Summary: {sentiment['summary']}")
    
    # Test database retrieval
    recent = analyzer.get_recent_sentiment("BTC-USD", hours=1)
    if recent:
        print(f"\nFrom DB: Score {recent['score']:.2f}")


if __name__ == "__main__":
    test_sentiment()