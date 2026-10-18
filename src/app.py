import os
import logging
import warnings

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'       
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'     

warnings.filterwarnings("ignore")

import streamlit as st
import numpy as np
import time
from urllib.parse import urlparse

import tensorflow as tf
from ml.prediction import PhishingPredictor
import absl.logging

absl.logging.set_verbosity(absl.logging.ERROR) 
tf.get_logger().setLevel('ERROR')              
logging.getLogger('tensorflow').setLevel(logging.ERROR)

st.set_page_config(page_title="DEPHIDES Scanner", page_icon="🛡️", layout="centered")

st.markdown("""
<style>
    .main {background-color: #0e1117;}
    .stTextInput input {
        border-radius: 10px;
        border: 2px solid #3b82f6;
        padding: 15px;
        font-size: 16px;
    }
    .stButton>button {
        width: 100%;
        border-radius: 10px;
        background-color: #3b82f6;
        color: white;
        font-weight: bold;
        padding: 10px;
        transition: all 0.3s;
    }
    .stButton>button:hover {
        background-color: #2563eb;
        transform: scale(1.02);
    }
    .hero {
        text-align: center;
        padding: 2rem 0;
    }
    .hero h1 {color: #ffffff; font-size: 3rem; font-weight: 800;}
    .hero p {color: #94a3b8; font-size: 1.2rem;}
</style>
""", unsafe_allow_html=True)

# --- ENTERPRISE HEURISTIC ALLOWLIST ---
TRUSTED_DOMAINS = {
    # AI & Developer Tools
    "google.com", "gemini.google.com", "chatgpt.com", "openai.com", "claude.ai", "anthropic.com", 
    "huggingface.co", "github.com", "gitlab.com", "bitbucket.org", "stackoverflow.com", "kaggle.com", 
    "colab.research.google.com", "docker.com", "kubernetes.io", "aws.amazon.com", "azure.microsoft.com", 
    "cloud.google.com", "vercel.com", "netlify.com", "heroku.com", "digitalocean.com",
    
    # Tech Giants & Utilities
    "apple.com", "microsoft.com", "amazon.com", "meta.com", "yahoo.com", "bing.com", "office.com", 
    "live.com", "zoom.us", "slack.com", "notion.so", "atlassian.com", "salesforce.com", "dropbox.com", 
    "box.com", "docusign.com", "canva.com", "adobe.com",
    
    # Social Media & Communities
    "youtube.com", "linkedin.com", "twitter.com", "x.com", "instagram.com", "facebook.com", "reddit.com", 
    "tiktok.com", "pinterest.com", "discord.com", "twitch.tv", "quora.com", "medium.com", "vk.com",
    
    # E-Commerce & Shopping
    "ebay.com", "shopify.com", "walmart.com", "target.com", "bestbuy.com", "homedepot.com", "lowes.com", 
    "costco.com", "macys.com", "ikea.com", "nike.com", "adidas.com", "zara.com", "aliexpress.com", "etsy.com",
    
    # Finance, Banking & Payments
    "paypal.com", "stripe.com", "square.com", "chase.com", "bankofamerica.com", "wellsfargo.com", 
    "capitalone.com", "citi.com", "americanexpress.com", "discover.com", "visa.com", "mastercard.com", 
    "venmo.com", "cash.app", "coinbase.com", "binance.com", "fidelity.com", "vanguard.com", "schwab.com",
    
    # India Specific
    "onlinesbi.sbi", "hdfcbank.com", "icicibank.com", "axisbank.com", "kotak.com", "flipkart.com", 
    "myntra.com", "zomato.com", "swiggy.com", "ola.com", "uber.com", "irctc.co.in", "india.gov.in", "uidai.gov.in",
    
    # News & Information
    "wikipedia.org", "nytimes.com", "bbc.com", "cnn.com", "bloomberg.com", "reuters.com", "theguardian.com", 
    "foxnews.com", "forbes.com", "wsj.com", "washingtonpost.com", "cnbc.com", "businessinsider.com", "npr.org",
    
    # Entertainment & Streaming
    "netflix.com", "hulu.com", "disneyplus.com", "hbomax.com", "primevideo.com", "vimeo.com", "spotify.com", 
    "soundcloud.com", "steampowered.com", "epicgames.com", "roblox.com", "playstation.com", "xbox.com",
    
    # Travel & Real Estate
    "zillow.com", "booking.com", "airbnb.com", "expedia.com", "tripadvisor.com", "yelp.com", "realtor.com",
    
    # Academics & Institutions 
    "mit.edu", "stanford.edu", "harvard.edu", "mitindia.edu", "annauniv.edu", "coursera.org", "edx.org", 
    "udacity.com", "udemy.com", "khanacademy.org", "quizlet.com"
}

def is_trusted_heuristic(url):
    try:
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain in TRUSTED_DOMAINS or domain.endswith((".edu", ".gov", ".ac.in")):
            return True
        return False
    except:
        return False

st.markdown("""
<div class="hero">
    <h1>🛡️ DEPHIDES</h1>
    <p>Deep Learning based Phishing URL Detection</p>
</div>
""", unsafe_allow_html=True)

@st.cache_resource(show_spinner="Loading CNN+BiLSTM Architecture...")
def load_system():
    # Resolve the same versioned active bundle as FastAPI, once per Streamlit
    # process. Promotion still requires a process restart to clear this cache.
    return PhishingPredictor()

predictor = load_system()

st.markdown("### 🔍 Enter Target URL")
url_input = st.text_input("URL", label_visibility="collapsed", placeholder="https://secure-login-update.com")

if st.button("🚀 INITIATE SCAN"):
    if url_input:
        progress_text = "Analyzing URL structure..."
        my_bar = st.progress(0, text=progress_text)
        for percent_complete in range(100):
            time.sleep(0.005)
            my_bar.progress(percent_complete + 1, text=progress_text)
        my_bar.empty()
        
        prediction = predictor.predict(url_input)
        pred = 1.0 if prediction["prediction"] == "phishing" else 0.0
        confidence = prediction["confidence"]
        
        st.markdown("---")
        st.markdown("### 📊 Scan Results")
        
        col1, col2 = st.columns(2)
        
        with col2:
            st.metric(label="Confidence Score", value=f"{confidence:.2f}%")
            
        with col1:
            if pred < 0.5:
                st.metric(label="Threat Level", value="SAFE")
                st.success("✅ The URL structure appears legitimate.")
            else:
                st.metric(label="Threat Level", value="CRITICAL")
                st.error("🚨 MALICIOUS URL DETECTED")
                
        st.markdown("---")
