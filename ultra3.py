import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import json
import datetime
# ✅ إضافة timezone لحل مشكلة التحذير والوقت
from datetime import timedelta, timezone
import re
import sys
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed

# ✅ استيراد مكتبة cloudscraper لتجاوز الحظر
import cloudscraper

# ✅ استيراد مكتبات Firebase
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask 
from threading import Thread 
import os 

# ==========================================
# ⚙️ إعدادات البوت وقاعدة البيانات
# ==========================================
# ✅ جلب مفاتيح Firebase من متغيرات البيئة
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))

# ==========================================
# 📡 دالة إرسال التنبيهات (تم رفعها للأعلى لاستخدامها فوراً)
# ==========================================
def send_telegram_alert(message):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        # استخدام cloudscraper هنا أيضاً لضمان الوصول
        scraper = cloudscraper.create_scraper() 
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try: 
            scraper.post(url, data=data, timeout=10)
        except Exception as e: 
            print(f"⚠️ Telegram Error: {e}")

# ✅ تهيئة الاتصال بـ Cloud Firestore
db = None
if FIREBASE_CREDENTIALS_JSON:
    try:
        cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ Cloud Firestore Initialized Successfully.")
    except Exception as e:
        err_msg = f"❌ Firestore Init Error: {e}"
        print(err_msg)
        send_telegram_alert(err_msg) # 🚨 تنبيه فوري
else:
    msg = "⚠️ Warning: FIREBASE_CREDENTIALS is missing."
    print(msg)
    send_telegram_alert(msg) # 🚨 تنبيه فوري

# ==========================================
# إعداد سيرفر وهمي (Flask)
# ==========================================
app = Flask('')

@app.route('/')
def home():
    return "I am alive! The Bot is running with Firestore & CloudScraper & Error Reporting..."

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ==========================================
# 1. إعدادات الاتصال (CloudScraper)
# ==========================================
BASE_URL = "https://www.ysscores.com"

# ✅ استخدام cloudscraper
session = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)

retry_strategy = Retry(
    total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Referer': 'https://www.google.dz/',
    'Accept-Language': 'ar-DZ,ar;q=0.9,fr-DZ;q=0.8,fr;q=0.7,en;q=0.5',
}
session.headers.update(HEADERS)

# ==========================================
# 🛠️ الدوال (لم يتم تغيير المنطق الأساسي)
# ==========================================
def convert_to_algeria_time(time_str):
    if not time_str or ":" not in time_str:
        return time_str
    try:
        is_pm = "م" in time_str or "مساء" in time_str
        clean_time = re.sub(r'[^0-9:]', '', time_str)
        match_time = datetime.datetime.strptime(clean_time, "%H:%M")

        if is_pm and match_time.hour != 12:
            match_time = match_time.replace(hour=match_time.hour + 12)
        elif not is_pm and match_time.hour == 12:
            match_time = match_time.replace(hour=0)

        new_time = match_time + timedelta(hours=6) 
        return new_time.strftime("%H:%M")
    except:
        return time_str

def clean_text(text):
    if text:
        return text.strip().replace('\n', ' ').replace('\r', '').replace('  ', ' ')
    return None

def get_match_deep_details(match_url):
    if not match_url: return None
    full_url = match_url if match_url.startswith('http') else f"{BASE_URL}{match_url}"
    
    try:
        response = session.get(full_url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        match_id = "0"
        id_search = re.search(r'/match/(\d+)', full_url)
        if id_search:
            match_id = id_search.group(1)

        match_details = {
            "id": match_id,
            "url": full_url, 
            "info": {}, 
            "teams": {}, 
            "channels": []
        }

        team_divs = soup.find_all('div', class_=re.compile(r'(team|club)'))
        main_teams = [t for t in team_divs if t.find('img')][:2]
        
        if len(main_teams) >= 2:
            t1_name = main_teams[0].get_text(strip=True)
            t1_img = main_teams[0].find('img')['src']
            t2_name = main_teams[1].get_text(strip=True)
            t2_img = main_teams[1].find('img')['src']
            match_details["teams"]["home"] = {"name": t1_name, "logo": t1_img}
            match_details["teams"]["away"] = {"name": t2_name, "logo": t2_img}
        else:
            title_tag = soup.find('title')
            match_details["teams"]["full_title"] = title_tag.text.strip() if title_tag else "مباراة"

        target_keys = {"البطولة": "championship", "الجولة": "round", "ملعب المباراة": "stadium", 
                       "وقت المباراة": "time", "تاريخ المباراة": "date"}
        info_block = soup.find('div', class_='match-info') or soup
        for label in info_block.find_all(string=re.compile(r'البطولة|الجولة|ملعب|وقت|تاريخ')):
            clean_lbl = clean_text(label)
            for key_ar, key_en in target_keys.items():
                if key_ar in clean_lbl:
                    parent = label.find_parent()
                    val_elem = parent.find_next_sibling() or parent.find('span', class_='value')
                    val = clean_text(val_elem.text) if val_elem else clean_text(parent.get_text().replace(key_ar, ''))
                    
                    if key_en == "time":
                        val = convert_to_algeria_time(val)
                        
                    match_details["info"][key_en] = val

        current_score = "- : -"
        match_status = ""

        s1_tag = soup.find('div', class_=re.compile(r'first-team-result')) or soup.find('span', class_=re.compile(r'first-team-result'))
        s2_tag = soup.find('div', class_=re.compile(r'second-team-result')) or soup.find('span', class_=re.compile(r'second-team-result'))
        
        if s1_tag and s2_tag:
            s1 = clean_text(s1_tag.text)
            s2 = clean_text(s2_tag.text)
            if s1.isdigit() and s2.isdigit():
                current_score = f"{s1} - {s2}"
        else:
             main_res = soup.find('div', class_='main-result')
             if main_res:
                 bs = main_res.find_all('b')
                 if len(bs) >= 2:
                     current_score = f"{clean_text(bs[0].text)} - {clean_text(bs[1].text)}"

        finished_keywords = soup.find_all(string=re.compile(r'(إنتهت|نهاية|Full Time)'))
        if finished_keywords:
             match_status = "إنتهت المباراة"

        if not match_status:
            live_status = soup.find('span', class_=re.compile(r'live-match-status'))
            if live_status and live_status.text.strip():
                match_status = clean_text(live_status.text)

        if not match_status:
            end_status_candidates = soup.find_all('span', class_=re.compile(r'result-status-text'))
            for status_item in end_status_candidates:
                if status_item.text.strip() and ":" not in status_item.text:
                    match_status = clean_text(status_item.text)
                    break
        
        if not match_status or ":" in match_status:
             match_status = "لم تبدأ"

        match_details["info"]["current_score"] = current_score
        match_details["info"]["match_status"] = match_status

        section_header = soup.find(string=re.compile(r'القنوات الناقلة والمعلقين'))
        if section_header:
            block_container = section_header.find_parent('div', class_='match-block-item')
            if block_container:
                channel_rows = block_container.find_all('div', class_='match-info-item sub')
                for row in channel_rows:
                    title_div = row.find('div', class_='title')
                    content_div = row.find('div', class_='content')
                    match_details["channels"].append({
                        "channel": clean_text(title_div.text) if title_div else "غير محدد",
                        "commentator": clean_text(content_div.text) if content_div else "غير محدد"
                    })

        if not match_details["channels"]:
            comm_single = soup.find(string=re.compile(r'^المعلق$'))
            ch_single = soup.find(string=re.compile(r'^القناة$'))
            if comm_single and ch_single:
                c_val = ch_single.find_parent().find_next_sibling()
                m_val = comm_single.find_parent().find_next_sibling()
                if c_val and m_val:
                    match_details["channels"].append({
                        "channel": clean_text(c_val.text),
                        "commentator": clean_text(m_val.text)
                    })

        return match_details

    except Exception:
        return None

def main_scraper():
    url = f"{BASE_URL}/ar/index"
    try:
        response = session.get(url, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        err_msg = f"⚠️ Scraping Error (Site might be down/blocked): {e}"
        print(err_msg)
        send_telegram_alert(err_msg) # 🚨 إشعار عند فشل سحب الصفحة الرئيسية
        return None

    links = set()
    for a in soup.find_all('a', href=re.compile(r'/match/\d+')):
        links.add(a['href'])
    
    links_list = list(links)
    total = len(links_list)
    print(f"[*] Found {total} matches.")

    final_data = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_url = {executor.submit(get_match_deep_details, u): u for u in links_list}
        for future in as_completed(future_to_url):
            data = future.result()
            if data: final_data.append(data)

    return sorted(final_data, key=lambda x: x['info'].get('championship', ''))

# ==========================================
# 🆕 دوال التعامل مع Cloud Firestore
# ==========================================

def clear_old_matches():
    if not db: return
    try:
        print("🧹 Clearing old matches from Firestore...")
        collection_ref = db.collection('today')
        docs = collection_ref.list_documents(page_size=100)
        deleted_count = 0
        for doc in docs:
            doc.delete()
            deleted_count += 1
        print(f"✅ Cleared {deleted_count} old matches.")
        send_telegram_alert(f"🧹 Cleared {deleted_count} old matches for the new day.") # ✅ إشعار بالتنظيف
    except Exception as e:
        err = f"❌ Error clearing matches: {e}"
        print(err)
        send_telegram_alert(err)

def update_firestore_db(matches_list):
    if not db:
        return False
        
    try:
        batch = db.batch()
        collection_ref = db.collection('today')

        count = 0
        for match in matches_list:
            doc_id = str(match['id']) 
            doc_ref = collection_ref.document(doc_id)
            batch.set(doc_ref, match, merge=True)
            count += 1
            
            if count >= 450:
                batch.commit()
                batch = db.batch()
                count = 0
        
        if count > 0:
            batch.commit()
            
        print(f"✅ Firestore Updated: {len(matches_list)} matches.")
        return True
    except Exception as e:
        err = f"❌ Firestore Update Error: {e}"
        print(err)
        send_telegram_alert(err) # 🚨 تنبيه فوري
        return False

def monitor_matches():
    last_hash = ""
    last_update_day = datetime.date.min
    
    print(f"🚀 Bot Started monitoring {BASE_URL}...")
    send_telegram_alert("🚀 Bot Started on Render (Firestore & CloudScraper & Alerts).")

    while True:
        try:
            current_data = main_scraper()
            
            # ✅ استخدام التوقيت الواعي بالمنطقة الزمنية
            utc_now = datetime.datetime.now(timezone.utc)
            algeria_now = utc_now + timedelta(hours=1)
            current_date = algeria_now.date()
            
            if current_data:
                current_json_str = json.dumps(current_data, sort_keys=True)
                current_hash = hashlib.md5(current_json_str.encode('utf-8')).hexdigest()
                
                force_update = (current_date > last_update_day)
                
                if current_hash != last_hash or force_update:
                    if force_update:
                        print(f"🔄 NEW DAY ({current_date}): Clearing old data first...")
                        clear_old_matches()
                        last_update_day = current_date 
                    else:
                        print("🔄 Change detected! Updating...")

                    if update_firestore_db(current_data):
                        last_hash = current_hash
                else:
                    print("💤 No changes.")
            
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            critical_err = f"🚨 Critical Loop Error (Bot might stop): {e}"
            print(critical_err)
            send_telegram_alert(critical_err) # 🚨 تنبيه هام جداً
            time.sleep(60)

if __name__ == "__main__":
    keep_alive()
    monitor_matches()
